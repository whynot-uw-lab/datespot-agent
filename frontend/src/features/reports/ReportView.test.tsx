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
    maxPlaces: 5,
    weights: { photoPercent: 60, reviewPercent: 40 },
    scoring: { photo: "차분함", review: "대화하기 좋음" },
  },
  results: [
    { status: "analyzed", name: "두 번째", finalScore: 8.1 },
    { status: "analyzed", name: "0점 장소", finalScore: 0 },
    { status: "failed", name: "확인 실패", failureReason: "상세 조회 실패" },
    { status: "analyzed", name: "동점 먼저", finalScore: 9.2 },
    {
      status: "analyzed",
      name: "동점 나중",
      finalScore: 9.2,
      photoScore: 9,
      reviewScore: 9,
      photoReason: "분위기가 차분함",
      reviewReason: "대화하기 좋다는 평가가 많음",
    },
  ],
};

describe("ReportView", () => {
  it("shows every analyzed place in stable score order and separates failures", () => {
    render(<ReportView report={report} />);

    const scoreList = screen.getByLabelText("점수순 장소");
    const cards = within(scoreList).getAllByRole("article");
    expect(cards).toHaveLength(4);
    expect(cards[0]).toHaveTextContent("동점 먼저");
    expect(cards[0]).toHaveTextContent("9.2");
    expect(cards[1]).toHaveTextContent("동점 나중");
    expect(cards[2]).toHaveTextContent("두 번째");
    expect(cards[3]).toHaveTextContent("0점 장소");
    expect(screen.getByText("평가 완료")).toBeInTheDocument();
    expect(screen.getByText("확인 실패 · 1")).toBeInTheDocument();
    expect(screen.queryByText("추천")).not.toBeInTheDocument();
    expect(screen.queryByText(/기준 충족|기준 미충족|추천 기준/)).not.toBeInTheDocument();
  });
});
