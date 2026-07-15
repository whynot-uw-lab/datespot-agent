import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { getRun, getRunReport } from "../../api/runs";
import { useBrowserStream, useRunEvents } from "../../realtime/hooks";
import type { RunEvent } from "../../realtime/runEventReducer";
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
  const { projection, connectionState } = useRunEvents(runId);
  const snapshotTerminal = snapshotQuery.data?.status === "completed" || snapshotQuery.data?.status === "failed";
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
  const progressItems = projection.progressItems.slice(-7) as RunEvent[];
  const placeResults = projection.placeResults;

  if (snapshotQuery.isLoading) return <main className="loading-page">실행 정보를 불러오는 중…</main>;
  if (snapshotQuery.isError) return <main className="error-page"><p>{snapshotQuery.error.message}</p><Link to="/app/">새 탐색으로</Link></main>;
  const run = snapshotQuery.data;
  if (!run) return null;

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
          <section className="timeline" aria-label="실행 진행 단계" aria-live="polite">
            <div className="rail-heading"><p className="eyebrow">PROGRESS</p><span>#{projection.latestSequence}</span></div>
            {progressItems.length ? progressItems.map((item) => (
              <div className="timeline-item" key={`${item.sequence}-${item.type}`}>
                <i aria-hidden="true" />
                <div><strong>{stageLabels[String(item.data.stage)] ?? "진행 중"}</strong><p>{String(item.data.message ?? "")}</p></div>
              </div>
            )) : <div className="timeline-empty">첫 진행 신호를 기다리는 중…</div>}
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
            <div className={`terminal-card ${run.status === "failed" ? "is-failed" : ""}`}>
              <strong>{run.status === "failed" ? "탐색을 완료하지 못함" : "탐색 완료"}</strong>
              <span>{reportQuery.isLoading ? "최종 리포트 불러오는 중…" : "결과가 준비됨"}</span>
            </div>
          ) : null}
        </aside>
      </div>

      {reportQuery.data ? <div className="inline-report page-shell"><ReportView report={reportQuery.data} /></div> : null}
      {terminal && !reportAvailable ? <div className="error-surface terminal-error page-shell">저장된 결과를 사용할 수 없음. <Link to="/app/">새 탐색 시작</Link></div> : null}
    </main>
  );
};
