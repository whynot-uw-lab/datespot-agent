import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { AnalyzedPlaceResult, AnalysisDigest } from "../../api/contracts";

const uniqueLimit = (values: string[], limit = 4) => (
  [...new Set(values.map((value) => value.trim()).filter(Boolean))].slice(0, limit)
);

const mergeDigestValues = (
  photoDigest: AnalysisDigest | null | undefined,
  reviewDigest: AnalysisDigest | null | undefined,
  key: "strengths" | "cautions",
) => uniqueLimit([
  ...(photoDigest?.[key] ?? []),
  ...(reviewDigest?.[key] ?? []),
]);

const InsightList = ({ items, title }: { items: string[]; title: string }) => (
  <section className="insight-list">
    <h4>{title}</h4>
    {items.length ? (
      <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
    ) : <p>기록된 항목 없음</p>}
  </section>
);

const AnalysisReasonDetails = ({
  label,
  reason,
}: {
  label: string;
  reason?: string | null;
}) => (
  <details className="analysis-reason-details">
    <summary>{label} 분석 근거 자세히 보기</summary>
    <p>{reason ?? "상세 분석 근거가 기록되지 않음"}</p>
  </details>
);

export const PlaceReportCard = ({
  place,
  rank,
}: {
  place: AnalyzedPlaceResult;
  rank: number;
}) => {
  const [expanded, setExpanded] = useState(false);
  const [reviewQuery, setReviewQuery] = useState("");
  const [selectedPhoto, setSelectedPhoto] = useState<{ url: string; label: string } | null>(null);
  const [failedPhotoUrls, setFailedPhotoUrls] = useState<Set<string>>(() => new Set());
  const photoTriggerRef = useRef<HTMLButtonElement | null>(null);
  const photoDialogRef = useRef<HTMLDivElement | null>(null);
  const photoCloseRef = useRef<HTMLButtonElement | null>(null);
  const photoClosingRef = useRef(false);
  const evidence = place.evidence;
  const strengths = mergeDigestValues(place.photoDigest, place.reviewDigest, "strengths");
  const cautions = mergeDigestValues(place.photoDigest, place.reviewDigest, "cautions");
  const normalizedReviewQuery = reviewQuery.trim().toLocaleLowerCase("ko-KR");
  const indexedReviews = useMemo(() => (
    evidence?.reviews.map((review, index) => ({ review, index })) ?? []
  ), [evidence?.reviews]);
  const filteredReviews = useMemo(() => (
    indexedReviews.filter(({ review }) => (
      !normalizedReviewQuery
      || review.toLocaleLowerCase("ko-KR").includes(normalizedReviewQuery)
    ))
  ), [indexedReviews, normalizedReviewQuery]);

  const closePhotoPreview = useCallback(() => {
    photoClosingRef.current = true;
    setSelectedPhoto(null);
    photoTriggerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!selectedPhoto) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closePhotoPreview();
      if (event.key === "Tab") {
        event.preventDefault();
        photoCloseRef.current?.focus();
      }
    };
    const handleFocusIn = (event: FocusEvent) => {
      if (
        !photoClosingRef.current
        && photoDialogRef.current
        && !photoDialogRef.current.contains(event.target as Node)
      ) {
        photoCloseRef.current?.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("focusin", handleFocusIn);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("focusin", handleFocusIn);
    };
  }, [closePhotoPreview, selectedPhoto]);

  const evidenceMeta = evidence
    ? [
        `사진 ${evidence.photoUrls.length}장`,
        `추출 리뷰 ${evidence.reviews.length}건`,
        ...(evidence.sourceReviewCount > 0
          ? [`네이버 전체 ${evidence.sourceReviewCount}건`]
          : []),
      ].join(" · ")
    : null;

  const markPhotoFailed = (url: string) => {
    setFailedPhotoUrls((current) => new Set([...current, url]));
  };

  return (
    <article className={`place-card ${expanded ? "is-expanded" : ""}`}>
      <div className="place-rank">{String(rank).padStart(2, "0")}</div>
      <div className="place-main">
        <div className="place-title-row">
          <div>
            <p className="place-meta">{place.category ?? "장소"}</p>
            <h3>{place.name}</h3>
            {place.address ? <p className="place-address">{place.address}</p> : null}
          </div>
          <div className="place-score-actions">
            <div className="final-score" aria-label={`최종 점수 ${place.finalScore}`}>
              <strong>{place.finalScore.toFixed(1)}</strong><span>/10</span>
            </div>
            {evidence?.placeUrl ? (
              <a
                className="naver-map-link"
                href={evidence.placeUrl}
                rel="noreferrer noopener"
                target="_blank"
              >
                네이버지도에서 보기
                <span aria-hidden="true">↗</span>
              </a>
            ) : null}
          </div>
        </div>

        <div className="place-score-strip" aria-label="세부 점수">
          <span>사진 <strong>{place.photoScore ?? "–"}</strong></span>
          <span>리뷰 <strong>{place.reviewScore ?? "–"}</strong></span>
        </div>

        <section className="place-overview">
          <p className="section-kicker">한눈에 보기</p>
          <div className="overview-summaries">
            {place.photoDigest ? <p><strong>사진</strong>{place.photoDigest.summary}</p> : null}
            {place.reviewDigest ? <p><strong>리뷰</strong>{place.reviewDigest.summary}</p> : null}
            {!place.photoDigest && !place.reviewDigest ? <p>요약 정보 없음</p> : null}
          </div>
          <div className="insight-grid">
            <InsightList items={strengths} title="좋은 점" />
            <InsightList items={cautions} title="고려할 점" />
          </div>
        </section>

        {evidenceMeta ? (
          <p className="evidence-meta">{evidenceMeta}</p>
        ) : (
          <p className="legacy-evidence-notice">이 리포트에는 원본 자료가 저장되지 않음</p>
        )}

        <button
          aria-expanded={expanded}
          className="evidence-toggle"
          onClick={() => setExpanded((current) => !current)}
          type="button"
        >
          {expanded ? "상세 근거 닫기" : "상세 근거 보기"}
          <span aria-hidden="true">{expanded ? "↑" : "↓"}</span>
        </button>

        <div className="place-evidence-details" hidden={!expanded}>
          <section className="evidence-section">
            <div className="evidence-heading">
              <div><p className="section-kicker">PHOTO EVIDENCE</p><h4>실제 내부 사진</h4></div>
              <span>{evidence?.photoUrls.length ?? 0}장</span>
            </div>
            {evidence?.photoUrls.length ? (
              <div className="report-photo-gallery" aria-label="분석에 사용된 내부 사진">
                {evidence.photoUrls.map((url, index) => {
                  const label = `${place.name} 내부 사진 ${index + 1}`;
                  return failedPhotoUrls.has(url) ? (
                    <div className="report-photo-failed" key={`${url}-${index}`} role="img" aria-label={`${label} 불러오기 실패`}>
                      이미지 없음
                    </div>
                  ) : (
                    <button
                      aria-label={`${label} 확대`}
                      className="report-photo-button"
                      key={`${url}-${index}`}
                      onClick={(event) => {
                        photoClosingRef.current = false;
                        photoTriggerRef.current = event.currentTarget;
                        setSelectedPhoto({ url, label });
                      }}
                      type="button"
                    >
                      <img
                        alt={label}
                        loading="lazy"
                        onError={() => markPhotoFailed(url)}
                        referrerPolicy="no-referrer"
                        src={url}
                      />
                    </button>
                  );
                })}
              </div>
            ) : <p className="evidence-empty">분석에 사용된 사진이 없음</p>}
            <AnalysisReasonDetails label="사진" reason={place.photoReason} />
          </section>

          <section className="evidence-section review-evidence-section">
            <div className="evidence-heading">
              <div><p className="section-kicker">REVIEW EVIDENCE</p><h4>실제 추출 리뷰</h4></div>
              <span>{evidence?.reviews.length ?? 0}건</span>
            </div>
            {evidence?.reviews.length ? (
              <>
                <label className="review-search">
                  <span>리뷰 검색</span>
                  <input
                    aria-label="리뷰 검색"
                    onChange={(event) => setReviewQuery(event.target.value)}
                    placeholder="리뷰 내용 검색"
                    type="search"
                    value={reviewQuery}
                  />
                </label>
                <ol aria-label="추출 리뷰 전체" className="review-scroll-list">
                  {filteredReviews.length ? filteredReviews.map(({ review, index }) => (
                    <li key={`${review}-${index}`}>
                      <span>{String(index + 1).padStart(2, "0")}</span>
                      <p>{review}</p>
                    </li>
                  )) : <li className="review-empty">검색 결과가 없음</li>}
                </ol>
              </>
            ) : <p className="evidence-empty">추출된 리뷰가 없음</p>}
            <AnalysisReasonDetails label="리뷰" reason={place.reviewReason} />
          </section>
        </div>
      </div>

      {selectedPhoto ? (
        <div
          aria-label={`${selectedPhoto.label} 확대 보기`}
          aria-modal="true"
          className="photo-preview-backdrop"
          onMouseDown={(event) => {
            if (event.currentTarget === event.target) closePhotoPreview();
          }}
          ref={photoDialogRef}
          role="dialog"
        >
          <div className="photo-preview">
            <button aria-label="사진 확대 닫기" autoFocus onClick={closePhotoPreview} ref={photoCloseRef} type="button">×</button>
            <img alt={selectedPhoto.label} referrerPolicy="no-referrer" src={selectedPhoto.url} />
            <span>{selectedPhoto.label}</span>
          </div>
        </div>
      ) : null}
    </article>
  );
};
