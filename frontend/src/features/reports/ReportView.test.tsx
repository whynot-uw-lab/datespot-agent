import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RunReport } from "../../api/contracts";
import { ReportView } from "./ReportView";

const report: RunReport = {
  runId: "run-1",
  status: "completed",
  createdAt: "2026-07-15T02:00:00Z",
  errors: [],
  config: {
    location: "성수역",
    searchKeyword: "이탈리안",
    maxPlaces: 4,
    weights: { photoPercent: 60, reviewPercent: 40 },
    scoring: { photo: "차분함", review: "대화하기 좋음" },
  },
  results: [
    { status: "analyzed", name: "두 번째", finalScore: 8.1 },
    { status: "not_matched", name: "기준 미달", mismatchReason: "너무 시끄러움" },
    { status: "failed", name: "확인 실패", failureReason: "상세 조회 실패" },
    {
      status: "analyzed",
      name: "첫 번째",
      finalScore: 9.2,
      photoScore: 9,
      reviewScore: 9,
      photoReason: "분위기가 차분함",
      reviewReason: "대화하기 좋다는 평가가 많음",
    },
  ],
};

describe("ReportView", () => {
  it("sorts analyzed places and separates non-matched outcomes", () => {
    render(<ReportView report={report} />);

    const recommendations = screen.getByLabelText("추천 장소");
    const cards = within(recommendations).getAllByRole("article");
    expect(cards[0]).toHaveTextContent("첫 번째");
    expect(cards[0]).toHaveTextContent("9.2");
    expect(cards[1]).toHaveTextContent("두 번째");
    expect(screen.getByText("기준 미충족 · 1")).toBeInTheDocument();
    expect(screen.getByText("확인 실패 · 1")).toBeInTheDocument();
  });
});
