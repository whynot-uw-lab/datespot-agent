import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  it("shows readable digest and source metadata before detailed reasons", () => {
    const evidenceReport = {
      ...report,
      results: [
        {
          status: "analyzed",
          placeId: "1916168522",
          name: "하루인",
          category: "이자카야",
          address: "경기 성남시 분당구 성남대로331번길 9-9 2층",
          photoScore: 8,
          reviewScore: 9,
          finalScore: 8.5,
          photoReason: "상세 사진 분석문",
          reviewReason: "상세 리뷰 분석문",
          photoDigest: {
            summary: "따뜻한 조명과 분리된 좌석이 보임",
            strengths: ["차분한 조명", "프라이빗한 좌석"],
            cautions: ["혼잡 시간대 소음 가능성"],
          },
          reviewDigest: {
            summary: "조용하고 대화하기 좋다는 평가가 많음",
            strengths: ["프라이빗한 좌석", "친절한 서비스"],
            cautions: ["가격대가 높다는 의견"],
          },
          evidence: {
            provider: "naver_map",
            placeUrl: "https://map.naver.com/p/entry/place/1916168522",
            photoUrls: Array.from(
              { length: 5 },
              (_, index) => `https://example.com/photo-${index + 1}.jpg`,
            ),
            reviews: [
              "조용해서 대화하기 좋아요",
              "룸이라 편하게 이야기했어요",
              "음식이 조금 짠 편이에요",
            ],
            sourceReviewCount: 128,
          },
        },
      ],
    } as unknown as RunReport;

    render(<ReportView report={evidenceReport} />);

    expect(screen.getByText("한눈에 보기")).toBeInTheDocument();
    expect(screen.getByText("좋은 점")).toBeInTheDocument();
    expect(screen.getByText("고려할 점")).toBeInTheDocument();
    expect(screen.getByText("차분한 조명")).toBeInTheDocument();
    expect(screen.getAllByText("프라이빗한 좌석")).toHaveLength(1);
    expect(screen.getByText("사진 5장 · 추출 리뷰 3건 · 네이버 전체 128건")).toBeInTheDocument();
    expect(screen.getByText("상세 사진 분석문")).not.toBeVisible();
    expect(screen.getByText("상세 리뷰 분석문")).not.toBeVisible();
    expect(screen.getByRole("link", { name: "네이버지도에서 보기" })).toHaveAttribute(
      "href",
      "https://map.naver.com/p/entry/place/1916168522",
    );
    expect(screen.getByRole("link", { name: "네이버지도에서 보기" })).toHaveAttribute(
      "rel",
      "noreferrer noopener",
    );
  });

  it("expands photos and every extracted review with local review search", async () => {
    const user = userEvent.setup();
    const evidenceReport = {
      ...report,
      results: [
        {
          status: "analyzed",
          placeId: "1916168522",
          name: "하루인",
          finalScore: 8.5,
          photoScore: 8,
          reviewScore: 9,
          photoReason: "상세 사진 분석문",
          reviewReason: "상세 리뷰 분석문",
          photoDigest: { summary: "사진 요약", strengths: [], cautions: [] },
          reviewDigest: { summary: "리뷰 요약", strengths: [], cautions: [] },
          evidence: {
            provider: "naver_map",
            placeUrl: "https://map.naver.com/p/entry/place/1916168522",
            photoUrls: Array.from(
              { length: 5 },
              (_, index) => `https://example.com/photo-${index + 1}.jpg`,
            ),
            reviews: [
              "조용해서 대화하기 좋아요",
              "룸이라 편하게 이야기했어요",
              "음식이 조금 짠 편이에요",
            ],
            sourceReviewCount: 128,
          },
        },
      ],
    } as unknown as RunReport;

    render(<ReportView report={evidenceReport} />);
    await user.click(screen.getByRole("button", { name: "상세 근거 보기" }));

    expect(screen.getAllByRole("img", { name: /하루인 내부 사진/ })).toHaveLength(5);
    const reviews = screen.getByLabelText("추출 리뷰 전체");
    expect(within(reviews).getAllByRole("listitem")).toHaveLength(3);
    expect(reviews).toHaveTextContent("조용해서 대화하기 좋아요");
    expect(reviews).toHaveTextContent("음식이 조금 짠 편이에요");

    const photoTrigger = screen.getByRole("button", { name: "하루인 내부 사진 1 확대" });
    await user.click(photoTrigger);
    const closePhoto = screen.getByRole("button", { name: "사진 확대 닫기" });
    expect(closePhoto).toHaveFocus();
    await user.tab();
    expect(closePhoto).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "하루인 내부 사진 1 확대 보기" })).not.toBeInTheDocument();
    expect(photoTrigger).toHaveFocus();

    await user.type(screen.getByRole("searchbox", { name: "리뷰 검색" }), " 짠 ");
    expect(reviews).not.toHaveTextContent("조용해서 대화하기 좋아요");
    expect(reviews).toHaveTextContent("음식이 조금 짠 편이에요");
    expect(within(reviews).getByRole("listitem")).toHaveTextContent("03");
  });

  it("explains when an older report has no saved source evidence", () => {
    render(<ReportView report={report} />);

    expect(screen.getAllByText("이 리포트에는 원본 자료가 저장되지 않음")).toHaveLength(4);
  });
});
