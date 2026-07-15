"""LangGraph 기반 소개팅 장소 실행 루프."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from time import monotonic
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from datespot_agent.analysis import (
    AnalysisError,
    PhotoAnalysisAgent,
    PlaceScoringService,
    ReviewAnalysisAgent,
)
from datespot_agent.analysis.photo import MAX_PHOTOS
from datespot_agent.analysis.review import MAX_REVIEWS
from datespot_agent.browser import BrowserService, BrowserServiceError
from datespot_agent.models import (
    GraphState,
    PlaceResult,
    PlaceResultStatus,
    RunConfig,
    RunReport,
    RunStatus,
)
from datespot_agent.observability import log_event

if TYPE_CHECKING:
    from datespot_agent.api.events import RunEventPublisher


LOGGER = logging.getLogger(__name__)


def utc_now() -> datetime:
    """UTC 현재 시각을 반환함."""
    return datetime.now(timezone.utc)


def make_run_id(clock: Callable[[], datetime] = utc_now) -> str:
    timestamp = GraphRunService._ensure_utc(clock()).strftime("%Y%m%d_%H%M%S")
    return f"run_{timestamp}_{uuid4().hex[:8]}"


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
        event_publisher: RunEventPublisher | None = None,
    ) -> None:
        self._browser_service = browser_service
        self._photo_agent = photo_agent
        self._review_agent = review_agent
        self._scoring_service = scoring_service
        self._clock = clock
        self._log = log
        self._events = event_publisher
        self._graph = self._build_graph()

    async def run(
        self,
        config: RunConfig,
        *,
        run_id: str | None = None,
    ) -> RunReport:
        """한 번의 장소 탐색 실행을 완료 report로 반환함."""
        effective_run_id = run_id or make_run_id(self._clock)
        initial_state = GraphState(run_id=effective_run_id, config=config)
        self._emit(
            f"[run:{effective_run_id}] 시작: location={config.location}, "
            f"keyword={config.search_keyword}, max_places={config.max_places}"
        )
        try:
            raw_state = await self._graph.ainvoke(initial_state)
            final_state = GraphState.model_validate(raw_state)
            if final_state.final_report is None:
                raise RuntimeError("최종 report가 생성되지 않음")
            self._emit(
                f"[run:{effective_run_id}] 종료: "
                f"status={final_state.final_report.status.value}, "
                f"results={len(final_state.final_report.results)}"
            )
            return final_state.final_report
        finally:
            try:
                await self._browser_service.close_session(effective_run_id)
            except Exception:
                pass
            self._emit(f"[run:{effective_run_id}] 브라우저 세션 정리 완료")

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
        self._progress(state.run_id, "session_start", "브라우저 세션 시작")
        try:
            await self._browser_service.start_session(state.run_id)
        except Exception as error:
            message = self._error_message(error, default="브라우저 세션 시작 실패")
            self._emit(f"[run:{state.run_id}] 브라우저 세션 시작 실패: {message}")
            self._progress(
                state.run_id,
                "session_start",
                "브라우저 세션 시작 실패",
            )
            return self._copy_state(
                state,
                last_error=message,
            )
        self._emit(f"[run:{state.run_id}] 브라우저 세션 시작 완료")
        self._progress(state.run_id, "session_start", "브라우저 세션 시작 완료")
        return self._copy_state(state, last_error=None)

    async def _search_candidates(self, state: GraphState) -> GraphState:
        self._emit(
            f"[run:{state.run_id}] 후보 검색 시작: "
            f"{state.config.location} / {state.config.search_keyword}"
        )
        self._progress(state.run_id, "candidate_search", "후보 검색 시작")
        try:
            candidates = await self._browser_service.search_candidates(
                state.run_id,
                state.config,
            )
        except BrowserServiceError as error:
            self._emit(f"[run:{state.run_id}] 후보 검색 실패: {error}")
            self._progress(state.run_id, "candidate_search", "후보 검색 실패")
            return self._copy_state(
                state,
                candidates=[],
                last_error=str(error),
            )
        self._emit(f"[run:{state.run_id}] 후보 검색 완료: {len(candidates)}건")
        self._progress(state.run_id, "candidate_search", "후보 검색 완료")
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
        self._progress(
            state.run_id,
            "place_detail",
            "장소 상세 추출 시작",
            place_id=current_place.place_id,
            place_name=current_place.name,
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
            self._progress(
                state.run_id,
                "place_detail",
                "장소 상세 추출 실패",
                place_id=current_place.place_id,
                place_name=current_place.name,
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
        self._progress(
            state.run_id,
            "place_detail",
            "장소 상세 추출 완료",
            place_id=current_place.place_id,
            place_name=current_place.name,
        )
        return self._copy_state(
            state,
            current_place_detail=detail,
            last_error=None,
        )

    async def _analyze_photos(self, state: GraphState) -> GraphState:
        if state.config.weights.photo_percent == 0:
            self._emit(f"[run:{state.run_id}] 사진 분석 생략: photo_percent=0")
            current_place = self._require_current_place(state)
            self._progress(
                state.run_id,
                "photo_analysis",
                "사진 분석 생략",
                status="skipped",
                place_id=current_place.place_id,
                place_name=current_place.name,
                input_count=0,
                duration_ms=0,
            )
            return self._copy_state(state, photo_analysis=None, last_error=None)

        detail = self._require_detail(state)
        photo_urls = tuple(url for url in detail.photo_urls if url)[:MAX_PHOTOS]
        if not photo_urls:
            message = f"사진 분석 자료가 없음: {detail.name}"
            self._emit(f"[run:{state.run_id}] 사진 분석 생략: inputs=0")
            log_event(
                LOGGER,
                "analysis.photo.skipped",
                "사진 분석 입력 없음",
                run_id=state.run_id,
                component="photo_analysis",
                stage="photo_analysis",
                place_id=detail.place_id,
                place_name=detail.name,
                input_count=0,
                duration_ms=0,
            )
            self._progress(
                state.run_id,
                "photo_analysis",
                "분석할 사진이 없어 건너뜀",
                status="skipped",
                place_id=detail.place_id,
                place_name=detail.name,
                input_count=0,
                duration_ms=0,
            )
            return self._copy_state(
                state,
                photo_analysis=None,
                last_error=message,
            )
        started_at = monotonic()
        self._emit(
            f"[run:{state.run_id}] 사진 분석 시작: "
            f"{detail.name}, inputs={len(photo_urls)}"
        )
        self._progress(
            state.run_id,
            "photo_analysis",
            f"사진 {len(photo_urls)}장 분석 시작",
            status="started",
            place_id=detail.place_id,
            place_name=detail.name,
            input_count=len(photo_urls),
            photo_urls=photo_urls,
        )
        self._progress(
            state.run_id,
            "photo_analysis",
            "사진 분석 모델 응답 대기 중",
            status="in_progress",
            place_id=detail.place_id,
            place_name=detail.name,
            input_count=len(photo_urls),
        )
        try:
            photo_analysis = await self._photo_agent.analyze(
                detail,
                state.config.scoring.photo,
            )
        except AnalysisError as error:
            self._emit(f"[run:{state.run_id}] 사진 분석 실패: {detail.name} - {error}")
            self._progress(
                state.run_id,
                "photo_analysis",
                "사진 분석 실패",
                status="failed",
                place_id=detail.place_id,
                place_name=detail.name,
                input_count=len(photo_urls),
                duration_ms=self._elapsed_ms(started_at),
            )
            return self._copy_state(
                state,
                photo_analysis=None,
                last_error=str(error),
            )
        self._emit(
            f"[run:{state.run_id}] 사진 분석 완료: "
            f"score={photo_analysis.photo_score}"
        )
        self._progress(
            state.run_id,
            "photo_analysis",
            "사진 분석 완료",
            status="completed",
            place_id=detail.place_id,
            place_name=detail.name,
            input_count=len(photo_urls),
            duration_ms=self._elapsed_ms(started_at),
            score=photo_analysis.photo_score,
        )
        return self._copy_state(
            state,
            photo_analysis=photo_analysis,
            last_error=None,
        )

    async def _analyze_reviews(self, state: GraphState) -> GraphState:
        if state.config.weights.review_percent == 0:
            self._emit(f"[run:{state.run_id}] 리뷰 분석 생략: review_percent=0")
            current_place = self._require_current_place(state)
            self._progress(
                state.run_id,
                "review_analysis",
                "리뷰 분석 생략",
                status="skipped",
                place_id=current_place.place_id,
                place_name=current_place.name,
                input_count=0,
                duration_ms=0,
            )
            return self._copy_state(state, review_analysis=None, last_error=None)

        detail = self._require_detail(state)
        input_count = len([review for review in detail.reviews if review][:MAX_REVIEWS])
        if input_count == 0:
            message = f"리뷰 분석 자료가 없음: {detail.name}"
            self._emit(f"[run:{state.run_id}] 리뷰 분석 생략: inputs=0")
            log_event(
                LOGGER,
                "analysis.review.skipped",
                "리뷰 분석 입력 없음",
                run_id=state.run_id,
                component="review_analysis",
                stage="review_analysis",
                place_id=detail.place_id,
                place_name=detail.name,
                input_count=0,
                duration_ms=0,
            )
            self._progress(
                state.run_id,
                "review_analysis",
                "분석할 리뷰가 없어 건너뜀",
                status="skipped",
                place_id=detail.place_id,
                place_name=detail.name,
                input_count=0,
                duration_ms=0,
            )
            return self._copy_state(
                state,
                review_analysis=None,
                last_error=message,
            )
        started_at = monotonic()
        self._emit(
            f"[run:{state.run_id}] 리뷰 분석 시작: {detail.name}, inputs={input_count}"
        )
        self._progress(
            state.run_id,
            "review_analysis",
            f"리뷰 {input_count}건 분석 시작",
            status="started",
            place_id=detail.place_id,
            place_name=detail.name,
            input_count=input_count,
        )
        self._progress(
            state.run_id,
            "review_analysis",
            "리뷰 분석 모델 응답 대기 중",
            status="in_progress",
            place_id=detail.place_id,
            place_name=detail.name,
            input_count=input_count,
        )
        try:
            review_analysis = await self._review_agent.analyze(
                detail,
                state.config.scoring.review,
            )
        except AnalysisError as error:
            self._emit(f"[run:{state.run_id}] 리뷰 분석 실패: {detail.name} - {error}")
            self._progress(
                state.run_id,
                "review_analysis",
                "리뷰 분석 실패",
                status="failed",
                place_id=detail.place_id,
                place_name=detail.name,
                input_count=input_count,
                duration_ms=self._elapsed_ms(started_at),
            )
            return self._copy_state(
                state,
                review_analysis=None,
                last_error=str(error),
            )
        self._emit(
            f"[run:{state.run_id}] 리뷰 분석 완료: "
            f"score={review_analysis.review_score}"
        )
        self._progress(
            state.run_id,
            "review_analysis",
            "리뷰 분석 완료",
            status="completed",
            place_id=detail.place_id,
            place_name=detail.name,
            input_count=input_count,
            duration_ms=self._elapsed_ms(started_at),
            score=review_analysis.review_score,
        )
        return self._copy_state(
            state,
            review_analysis=review_analysis,
            last_error=None,
        )

    def _calculate_place_result(self, state: GraphState) -> GraphState:
        detail = self._require_detail(state)
        started_at = monotonic()
        self._emit(f"[run:{state.run_id}] 결과 계산 시작: {detail.name}")
        log_event(
            LOGGER,
            "scoring.started",
            "장소 점수 계산 시작",
            run_id=state.run_id,
            component="scoring",
            stage="scoring",
            place_id=detail.place_id,
            place_name=detail.name,
        )
        self._progress(
            state.run_id,
            "scoring",
            "장소 점수 계산 시작",
            place_id=detail.place_id,
            place_name=detail.name,
        )
        try:
            result = self._scoring_service.calculate(
                detail,
                state.config.weights,
                state.photo_analysis,
                state.review_analysis,
            )
        except Exception as error:
            log_event(
                LOGGER,
                "scoring.failed",
                "장소 점수 계산 실패",
                run_id=state.run_id,
                component="scoring",
                stage="scoring",
                place_id=detail.place_id,
                place_name=detail.name,
                level=logging.ERROR,
                exc_info=True,
                duration_ms=self._elapsed_ms(started_at),
            )
            self._emit(f"[run:{state.run_id}] 결과 계산 실패: {detail.name} - {error}")
            self._progress(
                state.run_id,
                "scoring",
                "장소 점수 계산 실패",
                status="failed",
                place_id=detail.place_id,
                place_name=detail.name,
            )
            return self._copy_state(state, last_error=str(error))
        result_bits = [f"status={result.status.value}"]
        if result.final_score is not None:
            result_bits.append(f"final_score={result.final_score}")
        self._emit(
            f"[run:{state.run_id}] 결과 계산 완료: {detail.name} "
            + ", ".join(result_bits)
        )
        log_event(
            LOGGER,
            "scoring.completed",
            "장소 점수 계산 완료",
            run_id=state.run_id,
            component="scoring",
            stage="scoring",
            place_id=detail.place_id,
            place_name=detail.name,
            result_status=result.status,
            final_score=result.final_score,
            duration_ms=self._elapsed_ms(started_at),
        )
        self._progress(
            state.run_id,
            "scoring",
            "장소 점수 계산 완료",
            place_id=detail.place_id,
            place_name=detail.name,
        )
        next_state = self._copy_state(
            state,
            place_results=[*state.place_results, result],
            current_place_detail=None,
            photo_analysis=None,
            review_analysis=None,
            last_error=None,
        )
        if self._events is not None:
            self._events.place_result(state.run_id, result)
        return next_state

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
        next_state = self._copy_state(
            state,
            place_results=[*state.place_results, failed_result],
            current_place_detail=None,
            photo_analysis=None,
            review_analysis=None,
            last_error=None,
        )
        if self._events is not None:
            self._events.place_result(state.run_id, failed_result)
        return next_state

    def _build_completed_report(self, state: GraphState) -> GraphState:
        results = self._sorted_results(state.place_results)
        analyzed = sum(
            1 for item in results if item.status is PlaceResultStatus.ANALYZED
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
            f"completed, analyzed={analyzed}, failed={failed}"
        )
        self._progress(state.run_id, "report_build", "리포트 생성 완료")
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
        self._progress(state.run_id, "report_build", "리포트 생성 완료")
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

    def _progress(
        self,
        run_id: str,
        stage: str,
        message: str,
        *,
        status: str | None = None,
        place_id: str | None = None,
        place_name: str | None = None,
        current: int | None = None,
        total: int | None = None,
        input_count: int | None = None,
        duration_ms: int | None = None,
        score: int | None = None,
        photo_urls: tuple[str, ...] | None = None,
    ) -> None:
        if self._events is None:
            return
        from datespot_agent.api.events import ProgressStage, ProgressStatus

        details: dict[str, object] = {}
        for key, value in (
            ("status", ProgressStatus(status) if status is not None else None),
            ("current", current),
            ("total", total),
            ("input_count", input_count),
            ("duration_ms", duration_ms),
            ("score", score),
            ("photo_urls", photo_urls),
        ):
            if value is not None:
                details[key] = value

        self._events.progress(
            run_id,
            ProgressStage(stage),
            message,
            place_id=place_id,
            place_name=place_name,
            **details,
        )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, int((monotonic() - started_at) * 1_000))

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
        return make_run_id(self._clock)

    @staticmethod
    def _sorted_results(results: list[PlaceResult]) -> list[PlaceResult]:
        status_order = {
            PlaceResultStatus.ANALYZED: 0,
            PlaceResultStatus.FAILED: 1,
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
