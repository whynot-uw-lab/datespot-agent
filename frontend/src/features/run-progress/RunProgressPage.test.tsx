import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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
      ],
    },
  },
  browser: { state: "ready", frameUrl: "blob:map" },
}));

vi.mock("../../realtime/hooks", () => ({
  useRunEvents: () => realtimeState.events,
  useBrowserStream: () => realtimeState.browser,
}));

import { RunProgressPage } from "./RunProgressPage";

afterEach(() => {
  vi.unstubAllGlobals();
  realtimeState.events.projection.status = "running";
  realtimeState.events.projection.terminal = false;
  realtimeState.events.projection.reportAvailable = false;
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
});
