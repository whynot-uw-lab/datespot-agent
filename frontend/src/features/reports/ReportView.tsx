import type { AnalyzedPlaceResult, RunReport } from "../../api/contracts";
import { PlaceReportCard } from "./PlaceReportCard";

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
          <PlaceReportCard key={`${place.placeId ?? place.name}-${index}`} place={place} rank={index + 1} />
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
