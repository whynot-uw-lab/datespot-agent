"""LangGraph 기반 소개팅 장소 실행 루프."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from datespot_agent.analysis import (
    AnalysisError,
    AnalysisInputError,
    PhotoAnalysisAgent,
    PlaceScoringService,
    ReviewAnalysisAgent,
)
from datespot_agent.browser import BrowserService, BrowserServiceError
from datespot_agent.models import (
    GraphState,
    PlaceResult,
    PlaceResultStatus,
    RunConfig,
    RunReport,
    RunStatus,
)


def utc_now() -> datetime:
    """UTC 현재 시각을 반환함."""
    return datetime.now(timezone.utc)


class GraphRunService:
    """브라우저 탐색과 분석 node를 LangGraph로 조합함."""

    def __init__(
        self,
        *,
        browser_service: BrowserService,
        photo_agent: PhotoAnalysisAgent,
        review_agent: ReviewAnalysisAgent,
        scoring_service: PlaceScoringService,
        clock=utc_now,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._browser_service = browser_service
        self._photo_agent = photo_agent
        self._review_agent = review_agent
        self._scoring_service = scoring_service
        self._clock = clock
        self._log = log
        self._graph = self._build_graph()

    async def run(self, config: RunConfig) -> RunReport:
        """한 번의 장소 탐색 실행을 완료 report로 반환함."""
        run_id = self._make_run_id()
        initial_state = GraphState(run_id=run_id, config=config)
        self._emit(
            f"[run:{run_id}] 시작: location={config.location}, "
            f"keyword={config.search_keyword}, max_places={config.max_places}"
        )
        try:
            raw_state = await self._graph.ainvoke(initial_state)
            final_state = GraphState.model_validate(raw_state)
            if final_state.final_report is None:
                raise RuntimeError("최종 report가 생성되지 않음")
            self._emit(
                f"[run:{run_id}] 종료: status={final_state.final_report.status.value}, "
                f"results={len(final_state.final_report.results)}"
            )
            return final_state.final_report
        finally:
            try:
                await self._browser_service.close_session(run_id)
            except Exception:
                pass
            self._emit(f"[run:{run_id}] 브라우저 세션 정리 완료")

    def _build_graph(self):
        graph = StateGraph(GraphState)
        graph.add_node("validate_request", self._validate_request)
        graph.add_node("init_run", self._init_run)
        graph.add_node("open_browser", self._open_browser)
        graph.add_node("search_candidates", self._search_candidates)
        graph.add_node("normalize_candidates", self._normalize_candidates)
        graph.add_node("resume_candidate_loop", self._resume_candidate_loop)
        graph.add_node("select_current_place", self._select_current_place)
        graph.add_node("extract_place_detail", self._extract_place_detail)
        graph.add_node("analyze_photos", self._analyze_photos)
        graph.add_node("analyze_reviews", self._analyze_reviews)
        graph.add_node("calculate_place_result", self._calculate_place_result)
        graph.add_node("append_failed_place", self._append_failed_place)
        graph.add_node("build_completed_report", self._build_completed_report)
        graph.add_node("build_failed_report", self._build_failed_report)
        graph.add_node("close_browser", self._close_browser)

        graph.add_edge(START, "validate_request")
        graph.add_edge("validate_request", "init_run")
        graph.add_edge("init_run", "open_browser")
        graph.add_conditional_edges(
            "open_browser",
            self._route_after_open,
            {
                "run_failed": "build_failed_report",
                "ok": "search_candidates",
            },
        )
        graph.add_edge("search_candidates", "normalize_candidates")
        graph.add_conditional_edges(
            "normalize_candidates",
            self._route_after_search,
            {
                "run_failed": "build_failed_report",
                "empty": "build_completed_report",
                "has_candidates": "resume_candidate_loop",
            },
        )
        graph.add_conditional_edges(
            "resume_candidate_loop",
            self._route_after_loop,
            {
                "next": "select_current_place",
                "done": "build_completed_report",
            },
        )
        graph.add_edge("select_current_place", "extract_place_detail")
        graph.add_conditional_edges(
            "extract_place_detail",
            self._route_after_place_step,
            {
                "place_failed": "append_failed_place",
                "ok": "analyze_photos",
            },
        )
        graph.add_conditional_edges(
            "analyze_photos",
            self._route_after_place_step,
            {
                "place_failed": "append_failed_place",
                "ok": "analyze_reviews",
            },
        )
        graph.add_conditional_edges(
            "analyze_reviews",
            self._route_after_place_step,
            {
                "place_failed": "append_failed_place",
                "ok": "calculate_place_result",
            },
        )
        graph.add_conditional_edges(
            "calculate_place_result",
            self._route_after_place_step,
            {
                "place_failed": "append_failed_place",
                "ok": "resume_candidate_loop",
            },
        )
        graph.add_edge("append_failed_place", "resume_candidate_loop")
        graph.add_edge("build_completed_report", "close_browser")
        graph.add_edge("build_failed_report", "close_browser")
        graph.add_edge("close_browser", END)
        return graph.compile()

    def _validate_request(self, state: GraphState) -> GraphState:
        config = state.config
        if config.max_places < 1:
            raise ValueError("max_places는 1 이상이어야 함")
        if config.weights.photo_percent + config.weights.review_percent != 100:
            raise ValueError("가중치 합은 100이어야 함")
        return state

    def _init_run(self, state: GraphState) -> GraphState:
        return self._copy_state(
            state,
            status=RunStatus.RUNNING,
            candidates=[],
            current_place_index=0,
            current_place=None,
            current_place_detail=None,
            photo_analysis=None,
            review_analysis=None,
            place_results=[],
            final_report=None,
            last_error=None,
        )

    async def _open_browser(self, state: GraphState) -> GraphState:
        self._emit(f"[run:{state.run_id}] 브라우저 세션 시작")
        try:
            await self._browser_service.start_session(state.run_id)
        except Exception as error:
            message = self._error_message(error, default="브라우저 세션 시작 실패")
            self._emit(f"[run:{state.run_id}] 브라우저 세션 시작 실패: {message}")
            return self._copy_state(
                state,
                last_error=message,
            )
        self._emit(f"[run:{state.run_id}] 브라우저 세션 시작 완료")
        return self._copy_state(state, last_error=None)

    async def _search_candidates(self, state: GraphState) -> GraphState:
        self._emit(
            f"[run:{state.run_id}] 후보 검색 시작: "
            f"{state.config.location} / {state.config.search_keyword}"
        )
        try:
            candidates = await self._browser_service.search_candidates(
                state.run_id,
                state.config,
            )
        except BrowserServiceError as error:
            self._emit(f"[run:{state.run_id}] 후보 검색 실패: {error}")
            return self._copy_state(
                state,
                candidates=[],
                last_error=str(error),
            )
        self._emit(f"[run:{state.run_id}] 후보 검색 완료: {len(candidates)}건")
        return self._copy_state(state, candidates=candidates, last_error=None)

    def _normalize_candidates(self, state: GraphState) -> GraphState:
        return self._copy_state(
            state,
            candidates=state.candidates[: state.config.max_places],
        )

    def _resume_candidate_loop(self, state: GraphState) -> GraphState:
        return state

    def _select_current_place(self, state: GraphState) -> GraphState:
        if state.current_place_index >= len(state.candidates):
            raise RuntimeError("선택할 후보가 없음")
        current_place = state.candidates[state.current_place_index]
        self._emit(
            f"[run:{state.run_id}] 후보 선택: "
            f"{state.current_place_index + 1}/{len(state.candidates)} "
            f"{current_place.name}({current_place.place_id})"
        )
        return self._copy_state(
            state,
            current_place=current_place,
            current_place_index=state.current_place_index + 1,
            current_place_detail=None,
            photo_analysis=None,
            review_analysis=None,
            last_error=None,
        )

    async def _extract_place_detail(self, state: GraphState) -> GraphState:
        current_place = self._require_current_place(state)
        self._emit(
            f"[run:{state.run_id}] 상세 추출 시작: "
            f"{current_place.name}({current_place.place_id})"
        )
        try:
            detail = await self._browser_service.extract_place_detail(
                state.run_id,
                current_place,
            )
        except BrowserServiceError as error:
            self._emit(
                f"[run:{state.run_id}] 상세 추출 실패: "
                f"{current_place.name}({current_place.place_id}) - {error}"
            )
            return self._copy_state(
                state,
                current_place_detail=None,
                last_error=str(error),
            )
        self._emit(
            f"[run:{state.run_id}] 상세 추출 완료: "
            f"photos={len(detail.photo_urls)}, reviews={len(detail.reviews)}"
        )
        return self._copy_state(
            state,
            current_place_detail=detail,
            last_error=None,
        )

    async def _analyze_photos(self, state: GraphState) -> GraphState:
        if state.config.weights.photo_percent == 0:
            self._emit(f"[run:{state.run_id}] 사진 분석 생략: photo_percent=0")
            return self._copy_state(state, photo_analysis=None, last_error=None)

        detail = self._require_detail(state)
        self._emit(f"[run:{state.run_id}] 사진 분석 시작: {detail.name}")
        try:
            photo_analysis = await self._photo_agent.analyze(
                detail,
                state.config.scoring.photo,
            )
        except AnalysisError as error:
            self._emit(f"[run:{state.run_id}] 사진 분석 실패: {detail.name} - {error}")
            return self._copy_state(
                state,
                photo_analysis=None,
                last_error=str(error),
            )
        self._emit(
            f"[run:{state.run_id}] 사진 분석 완료: "
            f"score={photo_analysis.photo_score}, matched={photo_analysis.matched}"
        )
        return self._copy_state(
            state,
            photo_analysis=photo_analysis,
            last_error=None,
        )

    async def _analyze_reviews(self, state: GraphState) -> GraphState:
        if state.config.weights.review_percent == 0:
            self._emit(f"[run:{state.run_id}] 리뷰 분석 생략: review_percent=0")
            return self._copy_state(state, review_analysis=None, last_error=None)

        detail = self._require_detail(state)
        self._emit(f"[run:{state.run_id}] 리뷰 분석 시작: {detail.name}")
        try:
            review_analysis = await self._review_agent.analyze(
                detail,
                state.config.scoring.review,
            )
        except AnalysisError as error:
            self._emit(f"[run:{state.run_id}] 리뷰 분석 실패: {detail.name} - {error}")
            return self._copy_state(
                state,
                review_analysis=None,
                last_error=str(error),
            )
        self._emit(
            f"[run:{state.run_id}] 리뷰 분석 완료: "
            f"score={review_analysis.review_score}, matched={review_analysis.matched}"
        )
        return self._copy_state(
            state,
            review_analysis=review_analysis,
            last_error=None,
        )

    def _calculate_place_result(self, state: GraphState) -> GraphState:
        detail = self._require_detail(state)
        self._emit(f"[run:{state.run_id}] 결과 계산 시작: {detail.name}")
        try:
            result = self._scoring_service.calculate(
                detail,
                state.config.weights,
                state.photo_analysis,
                state.review_analysis,
            )
        except AnalysisInputError as error:
            self._emit(f"[run:{state.run_id}] 결과 계산 실패: {detail.name} - {error}")
            return self._copy_state(state, last_error=str(error))
        result_bits = [f"status={result.status.value}"]
        if result.final_score is not None:
            result_bits.append(f"final_score={result.final_score}")
        self._emit(
            f"[run:{state.run_id}] 결과 계산 완료: {detail.name} "
            + ", ".join(result_bits)
        )

        return self._copy_state(
            state,
            place_results=[*state.place_results, result],
            current_place_detail=None,
            photo_analysis=None,
            review_analysis=None,
            last_error=None,
        )

    def _append_failed_place(self, state: GraphState) -> GraphState:
        current_place = self._require_current_place(state)
        detail = state.current_place_detail
        failed_result = PlaceResult(
            status=PlaceResultStatus.FAILED,
            place_id=current_place.place_id,
            name=current_place.name,
            category=detail.category if detail else None,
            address=detail.address if detail else None,
            failure_reason=state.last_error or "알 수 없는 처리 오류",
        )
        self._emit(
            f"[run:{state.run_id}] 실패 결과 추가: "
            f"{current_place.name}({current_place.place_id}) - "
            f"{failed_result.failure_reason}"
        )
        return self._copy_state(
            state,
            place_results=[*state.place_results, failed_result],
            current_place_detail=None,
            photo_analysis=None,
            review_analysis=None,
            last_error=None,
        )

    def _build_completed_report(self, state: GraphState) -> GraphState:
        results = self._sorted_results(state.place_results)
        analyzed = sum(1 for item in results if item.status is PlaceResultStatus.ANALYZED)
        not_matched = sum(
            1 for item in results if item.status is PlaceResultStatus.NOT_MATCHED
        )
        failed = sum(1 for item in results if item.status is PlaceResultStatus.FAILED)
        report = self._report(
            state,
            status=RunStatus.COMPLETED,
            results=results,
            errors=[],
        )
        self._emit(
            f"[run:{state.run_id}] report 생성 완료: "
            f"completed, analyzed={analyzed}, not_matched={not_matched}, failed={failed}"
        )
        return self._copy_state(
            state,
            status=RunStatus.COMPLETED,
            place_results=results,
            final_report=report,
            last_error=None,
        )

    def _build_failed_report(self, state: GraphState) -> GraphState:
        error_message = state.last_error or "실행 전체 실패"
        report = self._report(
            state,
            status=RunStatus.FAILED,
            results=state.place_results,
            errors=[error_message],
        )
        self._emit(f"[run:{state.run_id}] report 생성 완료: failed - {error_message}")
        return self._copy_state(
            state,
            status=RunStatus.FAILED,
            final_report=report,
        )

    async def _close_browser(self, state: GraphState) -> GraphState:
        try:
            await self._browser_service.close_session(state.run_id)
        except Exception:
            self._emit(f"[run:{state.run_id}] close_browser 실패")
            return state
        self._emit(f"[run:{state.run_id}] close_browser 완료")
        return state

    def _route_after_open(self, state: GraphState) -> str:
        return "run_failed" if state.last_error else "ok"

    def _route_after_search(self, state: GraphState) -> str:
        if state.last_error:
            return "run_failed"
        if not state.candidates:
            return "empty"
        return "has_candidates"

    def _route_after_loop(self, state: GraphState) -> str:
        return "next" if state.current_place_index < len(state.candidates) else "done"

    def _route_after_place_step(self, state: GraphState) -> str:
        return "place_failed" if state.last_error else "ok"

    def _emit(self, message: str) -> None:
        if self._log is None:
            return
        self._log(message)

    def _report(
        self,
        state: GraphState,
        *,
        status: RunStatus,
        results: list[PlaceResult],
        errors: list[str],
    ) -> RunReport:
        return RunReport(
            run_id=state.run_id,
            status=status,
            config=state.config,
            results=results,
            errors=errors,
            created_at=self._ensure_utc(self._clock()),
        )

    @staticmethod
    def _copy_state(state: GraphState, **changes) -> GraphState:
        next_state = state.model_copy(deep=True)
        for field_name, value in changes.items():
            setattr(next_state, field_name, value)
        return next_state

    @staticmethod
    def _require_current_place(state: GraphState):
        if state.current_place is None:
            raise RuntimeError("현재 후보가 설정되지 않음")
        return state.current_place

    @staticmethod
    def _require_detail(state: GraphState):
        if state.current_place_detail is None:
            raise RuntimeError("현재 후보 상세가 설정되지 않음")
        return state.current_place_detail

    @staticmethod
    def _error_message(error: Exception, *, default: str) -> str:
        message = str(error).strip()
        if message:
            return message
        return default

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _make_run_id(self) -> str:
        timestamp = self._ensure_utc(self._clock()).strftime("%Y%m%d_%H%M%S")
        return f"run_{timestamp}_{uuid4().hex[:8]}"

    @staticmethod
    def _sorted_results(results: list[PlaceResult]) -> list[PlaceResult]:
        status_order = {
            PlaceResultStatus.ANALYZED: 0,
            PlaceResultStatus.NOT_MATCHED: 1,
            PlaceResultStatus.FAILED: 2,
        }

        def key(item: tuple[int, PlaceResult]) -> tuple[int, float, int]:
            index, result = item
            score = -(result.final_score or 0.0)
            if result.status is not PlaceResultStatus.ANALYZED:
                score = 0.0
            return status_order[result.status], score, index

        return [
            result
            for _, result in sorted(
                enumerate(results),
                key=key,
            )
        ]
