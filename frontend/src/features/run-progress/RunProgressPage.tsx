import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { getRun, getRunReport } from "../../api/runs";
import { useBrowserStream, useRunEvents } from "../../realtime/hooks";
import {
  getRunProgressData,
  type RunEvent,
  type RunProgressData,
} from "../../realtime/runEventReducer";
import { ReportView } from "../reports/ReportView";

const stageLabels: Record<string, string> = {
  session_start: "브라우저 준비",
  candidate_search: "후보 검색",
  place_detail: "장소 정보 확인",
  security_check: "보안 확인 대기",
  photo_analysis: "사진 분위기 분석",
  review_analysis: "리뷰 분석",
  scoring: "소개팅 적합도 계산",
  report_build: "리포트 정리",
};

const connectionLabels: Record<string, string> = {
  connecting: "연결 중",
  connected: "실시간 연결",
  reconnecting: "재연결 중",
  ended: "연결 종료",
  error: "연결 오류",
};

const statusLabels: Record<string, string> = {
  started: "시작",
  in_progress: "진행 중",
  completed: "완료",
  skipped: "건너뜀",
  failed: "실패",
};

const formatEventTime = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--:--:--";
  return date.toLocaleTimeString("ko-KR", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
};

const formatDuration = (durationMs: number) =>
  durationMs < 1_000
    ? `${Math.round(durationMs)}ms`
    : `${(durationMs / 1_000).toFixed(1)}초`;

interface TimelineItemProps {
  event: RunEvent;
  failedPhotoUrls: Set<string>;
  onPhotoError: (url: string) => void;
  onPhotoSelect: (
    photo: { url: string; label: string },
    trigger: HTMLButtonElement,
  ) => void;
}

const TimelineItem = ({
  event,
  failedPhotoUrls,
  onPhotoError,
  onPhotoSelect,
}: TimelineItemProps) => {
  const data = getRunProgressData(event);
  if (!data) return null;
  const inputUnit = data.stage === "photo_analysis" ? "장" : "건";
  return (
    <article className={`timeline-item status-${data.status ?? "default"}`}>
      <i aria-hidden="true" />
      <div className="timeline-content">
        <div className="timeline-meta">
          <time dateTime={event.occurredAt}>{formatEventTime(event.occurredAt)}</time>
          {data.status ? <span>{statusLabels[data.status]}</span> : null}
        </div>
        <div className="timeline-title">
          <strong>{stageLabels[data.stage] ?? "진행 중"}</strong>
          {data.placeName ? <em>{data.placeName}</em> : null}
        </div>
        <p>{data.message}</p>
        {data.inputCount != null || data.durationMs != null || data.score != null ? (
          <div className="timeline-metrics">
            {data.inputCount != null ? <span>입력 {data.inputCount}{inputUnit}</span> : null}
            {data.current != null && data.total != null ? <span>{data.current}/{data.total}</span> : null}
            {data.durationMs != null ? <span>{formatDuration(data.durationMs)}</span> : null}
            {data.score != null ? (
              <b>{data.score}점{data.matched == null ? "" : data.matched ? " · 기준 충족" : " · 기준 미충족"}</b>
            ) : null}
          </div>
        ) : null}
        {data.photoUrls?.length ? (
          <div className="analysis-thumbnails" aria-label="분석 대상 사진">
            {data.photoUrls.map((url, index) => {
              const label = `분석 사진 ${index + 1}`;
              return failedPhotoUrls.has(url) ? (
                <div className="analysis-thumbnail-failed" key={`${url}-${index}`} role="img" aria-label={`${label} 불러오기 실패`}>
                  <span>이미지 없음</span>
                </div>
              ) : (
                <button
                  aria-label={`${label} 확대`}
                  className="analysis-thumbnail"
                  key={`${url}-${index}`}
                  onClick={(clickEvent) => onPhotoSelect(
                    { url, label },
                    clickEvent.currentTarget,
                  )}
                  type="button"
                >
                  <img
                    alt={label}
                    loading="lazy"
                    onError={() => onPhotoError(url)}
                    referrerPolicy="no-referrer"
                    src={url}
                  />
                </button>
              );
            })}
          </div>
        ) : null}
      </div>
    </article>
  );
};

const BrowserSurface = ({ frameUrl, state }: { frameUrl?: string; state: string }) => (
  <section className="browser-surface" aria-label="브라우저 실시간 화면">
    <div className="browser-chrome">
      <div className="traffic-lights" aria-hidden="true"><i /><i /><i /></div>
      <span>map.naver.com</span>
      <div className={`live-badge ${state === "ready" ? "is-live" : ""}`}><i />{state === "ready" ? "LIVE" : "STANDBY"}</div>
    </div>
    <div className="browser-canvas">
      {frameUrl ? <img src={frameUrl} alt="실시간 지도 탐색 화면" /> : (
        <div className="browser-placeholder">
          <div className="radar" aria-hidden="true"><i /></div>
          <strong>{state === "error" ? "실시간 화면을 표시할 수 없음" : "브라우저 화면을 기다리는 중"}</strong>
          <span>진행 결과는 화면 연결과 관계없이 계속 저장됨.</span>
        </div>
      )}
    </div>
  </section>
);

export const RunProgressPage = () => {
  const { runId = "" } = useParams();
  const snapshotQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: () => getRun(runId),
    enabled: Boolean(runId),
  });
  const snapshotTerminal = snapshotQuery.data?.status === "completed" || snapshotQuery.data?.status === "failed";
  const { projection, connectionState } = useRunEvents(
    runId,
    snapshotQuery.isSuccess && !snapshotTerminal,
  );
  const terminal = projection.terminal || snapshotTerminal;
  const reportAvailable = projection.reportAvailable || snapshotQuery.data?.reportAvailable;
  const browser = useBrowserStream(runId, !terminal);
  const reportQuery = useQuery({
    queryKey: ["run-report", runId],
    queryFn: () => getRunReport(runId),
    enabled: Boolean(runId && terminal && reportAvailable),
    retry: (attempt, error) => "status" in error && error.status === 409 && attempt < 4,
    retryDelay: 800,
  });
  const progressItems = projection.progressItems as RunEvent[];
  const placeResults = projection.placeResults;
  const timelineRef = useRef<HTMLElement | null>(null);
  const followsLatestRef = useRef(true);
  const previousEventCountRef = useRef(0);
  const previewTriggerRef = useRef<HTMLButtonElement | null>(null);
  const previewDialogRef = useRef<HTMLDivElement | null>(null);
  const previewCloseRef = useRef<HTMLButtonElement | null>(null);
  const previewClosingRef = useRef(false);
  const [unseenEventCount, setUnseenEventCount] = useState(0);
  const [failedPhotoUrls, setFailedPhotoUrls] = useState<Set<string>>(
    () => new Set(),
  );
  const [selectedPhoto, setSelectedPhoto] = useState<{
    url: string;
    label: string;
  } | null>(null);

  useLayoutEffect(() => {
    const timeline = timelineRef.current;
    const addedCount = Math.max(
      0,
      progressItems.length - previousEventCountRef.current,
    );
    if (timeline && followsLatestRef.current) {
      timeline.scrollTop = timeline.scrollHeight;
      setUnseenEventCount(0);
    } else if (previousEventCountRef.current > 0 && addedCount > 0) {
      setUnseenEventCount((current) => current + addedCount);
    }
    previousEventCountRef.current = progressItems.length;
  }, [progressItems.length]);

  const closePhotoPreview = useCallback(() => {
    previewClosingRef.current = true;
    setSelectedPhoto(null);
    previewTriggerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!selectedPhoto) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closePhotoPreview();
      if (event.key === "Tab") {
        event.preventDefault();
        previewCloseRef.current?.focus();
      }
    };
    const onFocusIn = (event: FocusEvent) => {
      if (
        !previewClosingRef.current
        &&
        previewDialogRef.current
        && !previewDialogRef.current.contains(event.target as Node)
      ) {
        previewCloseRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("focusin", onFocusIn);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("focusin", onFocusIn);
    };
  }, [closePhotoPreview, selectedPhoto]);

  const onTimelineScroll = () => {
    const timeline = timelineRef.current;
    if (!timeline) return;
    const distanceFromBottom =
      timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight;
    const atBottom = distanceFromBottom <= 32;
    followsLatestRef.current = atBottom;
    if (atBottom) setUnseenEventCount(0);
  };

  const moveToLatest = () => {
    followsLatestRef.current = true;
    setUnseenEventCount(0);
    if (timelineRef.current) {
      timelineRef.current.scrollTop = timelineRef.current.scrollHeight;
    }
  };

  const onPhotoError = (url: string) => {
    setFailedPhotoUrls((current) => {
      const next = new Set(current);
      next.add(url);
      return next;
    });
  };

  const onPhotoSelect = (
    photo: { url: string; label: string },
    trigger: HTMLButtonElement,
  ) => {
    previewClosingRef.current = false;
    previewTriggerRef.current = trigger;
    setSelectedPhoto(photo);
  };

  if (snapshotQuery.isLoading) return <main className="loading-page">실행 정보를 불러오는 중…</main>;
  if (snapshotQuery.isError) return <main className="error-page"><p>{snapshotQuery.error.message}</p><Link to="/app/">새 탐색으로</Link></main>;
  const run = snapshotQuery.data;
  if (!run) return null;
  const effectiveStatus = projection.terminal ? projection.status : run.status;
  const failed = effectiveStatus === "failed";

  return (
    <main className="run-page">
      <header className="run-heading page-shell">
        <div>
          <p className="eyebrow">LIVE CURATION</p>
          <h1>{run.config.location} · {run.config.searchKeyword}</h1>
        </div>
        <div className={`connection-pill state-${connectionState}`}><i />{connectionLabels[connectionState]}</div>
      </header>

      <div className="live-layout page-shell">
        <BrowserSurface frameUrl={browser.frameUrl} state={browser.state} />
        <aside className="progress-rail">
          <section className="run-brief">
            <div><span>탐색 장소</span><strong>{run.config.maxPlaces}곳</strong></div>
            <div><span>평가 비중</span><strong>사진 {run.config.weights.photoPercent} · 리뷰 {run.config.weights.reviewPercent}</strong></div>
          </section>
          <section
            aria-label="실행 진행 단계"
            aria-live="polite"
            className="timeline"
            onScroll={onTimelineScroll}
            ref={timelineRef}
            role="log"
            tabIndex={0}
          >
            <div className="rail-heading">
              <p className="eyebrow">PROGRESS</p>
              <div>
                {unseenEventCount > 0 ? (
                  <button onClick={moveToLatest} type="button">
                    새 이벤트 {unseenEventCount}개 · 최신으로
                  </button>
                ) : null}
                <span>#{projection.latestSequence}</span>
              </div>
            </div>
            <div className="timeline-list">
              {progressItems.length ? progressItems.map((item) => (
                <TimelineItem
                  event={item}
                  failedPhotoUrls={failedPhotoUrls}
                  key={`${item.sequence}-${item.type}`}
                  onPhotoError={onPhotoError}
                  onPhotoSelect={onPhotoSelect}
                />
              )) : <div className="timeline-empty">첫 진행 신호를 기다리는 중…</div>}
            </div>
          </section>
          {placeResults.length ? (
            <section className="live-results">
              <p className="eyebrow">LATEST PLACE</p>
              {placeResults.slice(-2).reverse().map((place, index) => (
                <div className="mini-place" key={`${place.placeId ?? place.name}-${index}`}>
                  <div><strong>{place.name}</strong><span>{place.category ?? place.status}</span></div>
                  {place.finalScore != null ? <b>{place.finalScore.toFixed(1)}</b> : null}
                </div>
              ))}
            </section>
          ) : null}
          {terminal ? (
            <div className={`terminal-card ${failed ? "is-failed" : ""}`}>
              <strong>{failed ? "탐색을 완료하지 못함" : "탐색 완료"}</strong>
              <span>{reportQuery.isLoading ? "최종 리포트 불러오는 중…" : reportQuery.isError ? "리포트 확인 필요" : reportAvailable ? "결과가 준비됨" : "저장된 결과 없음"}</span>
            </div>
          ) : null}
        </aside>
      </div>

      {reportQuery.data ? <div className="inline-report page-shell"><ReportView report={reportQuery.data} /></div> : null}
      {reportQuery.isError ? (
        <div className="error-surface terminal-error page-shell" role="alert">
          <p>{reportQuery.error.message}</p>
          <button className="secondary-button" type="button" onClick={() => reportQuery.refetch()}>
            리포트 다시 불러오기
          </button>
        </div>
      ) : null}
      {terminal && !reportAvailable ? <div className="error-surface terminal-error page-shell">저장된 결과를 사용할 수 없음. <Link to="/app/">새 탐색 시작</Link></div> : null}
      {selectedPhoto ? (
        <div
          aria-label="분석 사진 미리보기"
          aria-modal="true"
          className="photo-preview-backdrop"
          onClick={(event) => {
            if (event.currentTarget === event.target) closePhotoPreview();
          }}
          role="dialog"
          ref={previewDialogRef}
        >
          <div className="photo-preview">
            <button aria-label="미리보기 닫기" autoFocus onClick={closePhotoPreview} ref={previewCloseRef} type="button">×</button>
            <img alt={`${selectedPhoto.label} 크게 보기`} referrerPolicy="no-referrer" src={selectedPhoto.url} />
            <span>{selectedPhoto.label}</span>
          </div>
        </div>
      ) : null}
    </main>
  );
};
