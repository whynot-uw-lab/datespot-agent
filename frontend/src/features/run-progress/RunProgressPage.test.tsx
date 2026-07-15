import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const realtimeState = vi.hoisted(() => ({
  events: {
    connectionState: "connected",
    projection: {
      latestSequence: 4,
      awaitingSnapshot: false,
      status: "running",
      terminal: false,
      reportAvailable: false,
      placeResults: [] as Array<Record<string, unknown>>,
      progressItems: [
        {
          runId: "run-1",
          sequence: 4,
          occurredAt: "2026-07-15T00:00:00Z",
          type: "progress",
          data: { stage: "candidate_search", message: "후보 검색 중" },
        },
      ] as Array<{
        runId: string;
        sequence: number;
        occurredAt: string;
        type: "progress";
        data: Record<string, unknown>;
      }>,
    },
  },
  browser: { state: "ready", frameUrl: "blob:map" },
  eventEnabled: true,
}));

vi.mock("../../realtime/hooks", () => ({
  useRunEvents: (_runId: string, enabled: boolean) => {
    realtimeState.eventEnabled = enabled;
    return realtimeState.events;
  },
  useBrowserStream: () => realtimeState.browser,
}));

import { RunProgressPage } from "./RunProgressPage";

afterEach(() => {
  vi.unstubAllGlobals();
  realtimeState.events.projection.status = "running";
  realtimeState.events.projection.terminal = false;
  realtimeState.events.projection.reportAvailable = false;
  realtimeState.events.projection.latestSequence = 4;
  realtimeState.events.projection.progressItems = [
    {
      runId: "run-1",
      sequence: 4,
      occurredAt: "2026-07-15T00:00:00Z",
      type: "progress",
      data: { stage: "candidate_search", message: "후보 검색 중" },
    },
  ];
  realtimeState.eventEnabled = true;
});

const snapshot = {
  runId: "run-1",
  status: "running",
  reportAvailable: false,
  createdAt: "2026-07-15T00:00:00Z",
  config: {
    location: "성수역",
    searchKeyword: "이탈리안",
    maxPlaces: 3,
    weights: { photoPercent: 50, reviewPercent: 50 },
    scoring: { photo: "분위기", review: "대화" },
  },
};

const renderPage = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/app/runs/run-1"]}>
        <Routes><Route path="/app/runs/:runId" element={<RunProgressPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

describe("RunProgressPage", () => {
  it("renders every retained event with structured analysis details", async () => {
    realtimeState.events.projection.latestSequence = 30;
    realtimeState.events.projection.progressItems = Array.from(
      { length: 30 },
      (_, index) => ({
        runId: "run-1",
        sequence: index + 1,
        occurredAt: `2026-07-15T00:00:${String(index).padStart(2, "0")}Z`,
        type: "progress" as const,
        data: {
          stage: index === 29 ? "review_analysis" : "candidate_search",
          message: `진행 이벤트 ${index + 1}`,
          ...(index === 29
            ? {
                status: "completed",
                placeName: "우니도",
                inputCount: 12,
                durationMs: 1456,
                score: 9,
              }
            : {}),
        },
      }),
    );
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify(snapshot), {
        headers: { "Content-Type": "application/json" },
      }),
    ));

    renderPage();

    expect(await screen.findByText("진행 이벤트 1")).toBeInTheDocument();
    expect(screen.getByText("진행 이벤트 30")).toBeInTheDocument();
    expect(screen.getByText("우니도")).toBeInTheDocument();
    expect(screen.getByText("입력 12건")).toBeInTheDocument();
    expect(screen.getByText("1.5초")).toBeInTheDocument();
    expect(screen.getByText("9점")).toBeInTheDocument();
    expect(screen.queryByText(/기준 충족|기준 미충족/)).not.toBeInTheDocument();
  });

  it("shows analysis photo thumbnails and an accessible preview", async () => {
    const user = userEvent.setup();
    realtimeState.events.projection.progressItems = [
      {
        runId: "run-1",
        sequence: 5,
        occurredAt: "2026-07-15T00:00:05Z",
        type: "progress",
        data: {
          stage: "photo_analysis",
          message: "사진 2장 분석 시작",
          status: "started",
          placeName: "우니도",
          inputCount: 2,
          photoUrls: [
            "https://images.example/one.jpg",
            "https://images.example/two.jpg",
          ],
        },
      },
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify(snapshot), {
        headers: { "Content-Type": "application/json" },
      }),
    ));

    renderPage();

    const first = await screen.findByRole("button", { name: "분석 사진 1 확대" });
    expect(screen.getByRole("img", { name: "분석 사진 1" })).toHaveAttribute(
      "src",
      "https://images.example/one.jpg",
    );
    expect(screen.getByRole("img", { name: "분석 사진 2" })).toBeInTheDocument();

    await user.click(first);
    expect(screen.getByRole("dialog", { name: "분석 사진 미리보기" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "분석 사진 1 크게 보기" })).toBeInTheDocument();

    const closeButton = screen.getByRole("button", { name: "미리보기 닫기" });
    expect(closeButton).toHaveFocus();
    await user.tab();
    expect(closeButton).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(first).toHaveFocus();
  });

  it("keeps the reader position and offers a latest-event button", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify(snapshot), {
        headers: { "Content-Type": "application/json" },
      }),
    ));
    const view = renderPage();
    const timeline = await screen.findByRole("log", { name: "실행 진행 단계" });
    Object.defineProperties(timeline, {
      scrollHeight: { configurable: true, value: 1000 },
      clientHeight: { configurable: true, value: 200 },
      scrollTop: { configurable: true, value: 0, writable: true },
    });
    fireEvent.scroll(timeline);
    realtimeState.events.projection.latestSequence = 5;
    realtimeState.events.projection.progressItems = [
      ...realtimeState.events.projection.progressItems,
      {
        runId: "run-1",
        sequence: 5,
        occurredAt: "2026-07-15T00:00:05Z",
        type: "progress",
        data: { stage: "photo_analysis", message: "새 분석 이벤트" },
      },
    ];

    view.rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/app/runs/run-1"]}>
          <Routes><Route path="/app/runs/:runId" element={<RunProgressPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const latestButton = await screen.findByRole("button", { name: "새 이벤트 1개 · 최신으로" });
    expect(timeline.scrollTop).toBe(0);
    fireEvent.click(latestButton);
    expect(timeline.scrollTop).toBe(1000);
    expect(screen.queryByRole("button", { name: /최신으로/ })).not.toBeInTheDocument();
  });

  it("renders the live browser and friendly progress rail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify(snapshot),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    renderPage();

    expect(await screen.findByText("성수역 · 이탈리안")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "실시간 지도 탐색 화면" })).toHaveAttribute("src", "blob:map");
    expect(screen.getByText("후보 검색")).toBeInTheDocument();
    expect(screen.getByText("후보 검색 중")).toBeInTheDocument();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it("uses the terminal event status when a running snapshot later fails", async () => {
    realtimeState.events.projection.status = "failed";
    realtimeState.events.projection.terminal = true;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify(snapshot), {
        headers: { "Content-Type": "application/json" },
      }),
    ));

    renderPage();

    expect(await screen.findByText("탐색을 완료하지 못함")).toBeInTheDocument();
  });

  it("shows a retry surface when the terminal report cannot be loaded", async () => {
    realtimeState.events.projection.status = "completed";
    realtimeState.events.projection.terminal = true;
    realtimeState.events.projection.reportAvailable = true;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/report")) {
        return Promise.resolve(new Response(JSON.stringify({
          detail: { code: "report_corrupt", message: "저장 리포트가 손상됨" },
        }), { status: 500, headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response(JSON.stringify(snapshot), {
        headers: { "Content-Type": "application/json" },
      }));
    }));

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("저장 리포트가 손상됨");
    expect(screen.getByRole("button", { name: "리포트 다시 불러오기" })).toBeInTheDocument();
  });

  it("does not enable SSE when the run snapshot cannot be found", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        detail: { code: "run_not_found", message: "실행을 찾을 수 없음" },
      }), { status: 404, headers: { "Content-Type": "application/json" } }),
    ));

    renderPage();

    expect(await screen.findByText("실행을 찾을 수 없음")).toBeInTheDocument();
    expect(realtimeState.eventEnabled).toBe(false);
  });
});
