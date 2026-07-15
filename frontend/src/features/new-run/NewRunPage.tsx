import { useMutation } from "@tanstack/react-query";
import { useState, type CSSProperties, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { AppError } from "../../api/client";
import type { RunConfig } from "../../api/contracts";
import { createRun } from "../../api/runs";

const defaultPhotoCriteria =
  "어둡고 차분한 분위기, 넓은 좌석 간격, 대화하기 좋은 구조";
const defaultReviewCriteria = "깔끔함, 조용함, 대화하기 좋음 등 긍정 표현";

const FieldError = ({ id, message }: { id: string; message?: string }) =>
  message ? <span className="field-error" id={id}>{message}</span> : null;

export const NewRunPage = () => {
  const navigate = useNavigate();
  const [location, setLocation] = useState("");
  const [searchKeyword, setSearchKeyword] = useState("");
  const [maxPlaces, setMaxPlaces] = useState(3);
  const [photoPercent, setPhotoPercent] = useState(50);
  const [photoCriteria, setPhotoCriteria] = useState(defaultPhotoCriteria);
  const [reviewCriteria, setReviewCriteria] = useState(defaultReviewCriteria);
  const mutation = useMutation({
    mutationFn: createRun,
    onSuccess: ({ runId }) => navigate(`/app/runs/${runId}`),
  });
  const fieldErrors = mutation.error instanceof AppError ? mutation.error.fieldErrors : {};

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const config: RunConfig = {
      location: location.trim(),
      searchKeyword: searchKeyword.trim(),
      maxPlaces,
      weights: {
        photoPercent,
        reviewPercent: 100 - photoPercent,
      },
      scoring: {
        photo: photoCriteria.trim(),
        review: reviewCriteria.trim(),
      },
    };
    mutation.mutate(config);
  };

  return (
    <main className="new-run-page page-shell">
      <section className="hero-copy" aria-labelledby="new-run-title">
        <p className="eyebrow">DATE SPOT CURATOR</p>
        <h1 id="new-run-title">
          대화가 오래 머무는
          <br />한 곳을 찾습니다.
        </h1>
        <p className="hero-description">
          실제 지도와 리뷰를 살피고, 사진의 분위기까지 읽어 소개팅에 어울리는
          장소를 골라드림.
        </p>
        <div className="hero-proof" aria-label="탐색 방식">
          <span>LIVE MAP</span>
          <span>PHOTO MOOD</span>
          <span>REVIEW SIGNAL</span>
        </div>
      </section>

      <section className="search-card" aria-label="새 장소 탐색 설정">
        <div className="section-heading">
          <p className="eyebrow">NEW SEARCH</p>
          <h2>오늘의 만남을 알려주세요.</h2>
        </div>
        <form onSubmit={submit}>
          <div className="field-grid two-columns">
            <div className="form-field">
              <label htmlFor="location"><span>어디에서 만날까요?</span></label>
              <input
                id="location"
                value={location}
                aria-describedby={fieldErrors.location ? "location-error" : undefined}
                aria-invalid={Boolean(fieldErrors.location)}
                onChange={(event) => setLocation(event.target.value)}
                placeholder="예: 성수역"
                required
              />
              <FieldError id="location-error" message={fieldErrors.location} />
            </div>
            <div className="form-field">
              <label htmlFor="search-keyword"><span>어떤 장소를 찾을까요?</span></label>
              <input
                id="search-keyword"
                value={searchKeyword}
                aria-describedby={fieldErrors.searchKeyword ? "search-keyword-error" : undefined}
                aria-invalid={Boolean(fieldErrors.searchKeyword)}
                onChange={(event) => setSearchKeyword(event.target.value)}
                placeholder="예: 이탈리안"
                required
              />
              <FieldError id="search-keyword-error" message={fieldErrors.searchKeyword} />
            </div>
          </div>

          <div className="field-grid count-and-weight">
            <div className="form-field">
              <label htmlFor="max-places"><span>확인할 장소 수</span></label>
              <input
                id="max-places"
                type="number"
                min="1"
                max="10"
                value={maxPlaces}
                aria-describedby={fieldErrors.maxPlaces ? "max-places-error" : undefined}
                aria-invalid={Boolean(fieldErrors.maxPlaces)}
                onChange={(event) => setMaxPlaces(Number(event.target.value))}
                required
              />
              <FieldError id="max-places-error" message={fieldErrors.maxPlaces} />
            </div>
            <div className="weight-control">
              <div className="weight-labels">
                <label htmlFor="photo-weight">사진 {photoPercent}%</label>
                <span>리뷰 {100 - photoPercent}%</span>
              </div>
              <input
                id="photo-weight"
                aria-label="사진 비중"
                type="range"
                min="0"
                max="100"
                step="5"
                value={photoPercent}
                aria-describedby={fieldErrors["weights.photoPercent"] ? "photo-weight-error" : undefined}
                aria-invalid={Boolean(fieldErrors["weights.photoPercent"])}
                style={{ "--range-progress": `${photoPercent}%` } as CSSProperties}
                onChange={(event) => setPhotoPercent(Number(event.target.value))}
              />
              <FieldError id="photo-weight-error" message={fieldErrors["weights.photoPercent"]} />
            </div>
          </div>

          <details className="criteria-panel">
            <summary>평가 기준 직접 조정</summary>
            <div className="form-field">
              <label htmlFor="photo-criteria"><span>사진 평가 기준</span></label>
              <textarea
                id="photo-criteria"
                value={photoCriteria}
                aria-describedby={fieldErrors["scoring.photo"] ? "photo-criteria-error" : undefined}
                aria-invalid={Boolean(fieldErrors["scoring.photo"])}
                onChange={(event) => setPhotoCriteria(event.target.value)}
                required
              />
              <FieldError id="photo-criteria-error" message={fieldErrors["scoring.photo"]} />
            </div>
            <div className="form-field">
              <label htmlFor="review-criteria"><span>리뷰 평가 기준</span></label>
              <textarea
                id="review-criteria"
                value={reviewCriteria}
                aria-describedby={fieldErrors["scoring.review"] ? "review-criteria-error" : undefined}
                aria-invalid={Boolean(fieldErrors["scoring.review"])}
                onChange={(event) => setReviewCriteria(event.target.value)}
                required
              />
              <FieldError id="review-criteria-error" message={fieldErrors["scoring.review"]} />
            </div>
          </details>

          {mutation.isError ? (
            <p className="form-error" role="alert">
              {mutation.error.message}
            </p>
          ) : null}
          <button className="primary-button" type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? "탐색 준비 중…" : "장소 탐색 시작"}
            <span aria-hidden="true">↗</span>
          </button>
        </form>
      </section>
    </main>
  );
};
