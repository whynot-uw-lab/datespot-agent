import type { AnalyzedPlaceResult, RunReport } from "../../api/contracts";

const ScoreReason = ({ label, score, reason }: { label: string; score?: number | null; reason?: string | null }) => (
  <div className="score-reason">
    <div><span>{label}</span><strong>{score ?? "–"}</strong></div>
    <p>{reason ?? "평가 근거가 기록되지 않음"}</p>
  </div>
);

const PlaceCard = ({ place, rank }: { place: AnalyzedPlaceResult; rank: number }) => (
  <article className="place-card">
    <div className="place-rank">{String(rank).padStart(2, "0")}</div>
    <div className="place-main">
      <div className="place-title-row">
        <div>
          <p className="place-meta">{place.category ?? "장소"}</p>
          <h3>{place.name}</h3>
          {place.address ? <p className="place-address">{place.address}</p> : null}
        </div>
        <div className="final-score" aria-label={`최종 점수 ${place.finalScore}`}>
          <strong>{place.finalScore.toFixed(1)}</strong><span>/10</span>
        </div>
      </div>
      <div className="reason-grid">
        <ScoreReason label="사진" score={place.photoScore} reason={place.photoReason} />
        <ScoreReason label="리뷰" score={place.reviewScore} reason={place.reviewReason} />
      </div>
    </div>
  </article>
);

export const ReportView = ({ report }: { report: RunReport }) => {
  const analyzed = report.results
    .filter((result): result is AnalyzedPlaceResult => result.status === "analyzed")
    .sort((left, right) => right.finalScore - left.finalScore);
  const failed = report.results.filter((result) => result.status === "failed");

  return (
    <section className="report-view">
      <header className="report-hero">
        <div>
          <p className="eyebrow">CURATED RESULT</p>
          <h1>{report.config.location}에서 찾은<br />오늘의 장소</h1>
          <p>{report.config.searchKeyword} · 사진 {report.config.weights.photoPercent}% / 리뷰 {report.config.weights.reviewPercent}%</p>
        </div>
        <div className="report-stats" aria-label="결과 요약">
          <div><strong>{analyzed.length}</strong><span>평가 완료</span></div>
          <div><strong>{failed.length}</strong><span>확인 실패</span></div>
        </div>
      </header>

      <div className="recommendations" aria-label="점수순 장소">
        {analyzed.length ? analyzed.map((place, index) => (
          <PlaceCard key={`${place.placeId ?? place.name}-${index}`} place={place} rank={index + 1} />
        )) : <div className="empty-surface">평가 완료된 장소가 없음.</div>}
      </div>

      {failed.length ? (
        <details className="outcome-details">
          <summary>확인 실패 · {failed.length}</summary>
          {failed.map((place) => (
            <div className="outcome-row" key={place.placeId ?? place.name}>
              <strong>{place.name}</strong><span>{place.failureReason}</span>
            </div>
          ))}
        </details>
      ) : null}
    </section>
  );
};
