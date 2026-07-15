import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../realtime/hooks", () => ({
  useRunEvents: () => ({
    connectionState: "connected",
    projection: {
      latestSequence: 4,
      awaitingSnapshot: false,
      status: "running",
      terminal: false,
      reportAvailable: false,
      placeResults: [],
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
  }),
  useBrowserStream: () => ({ state: "ready", frameUrl: "blob:map" }),
}));

import { RunProgressPage } from "./RunProgressPage";

afterEach(() => vi.unstubAllGlobals());

describe("RunProgressPage", () => {
  it("renders the live browser and friendly progress rail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
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
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/app/runs/run-1"]}>
          <Routes><Route path="/app/runs/:runId" element={<RunProgressPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("성수역 · 이탈리안")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "실시간 지도 탐색 화면" })).toHaveAttribute("src", "blob:map");
    expect(screen.getByText("후보 검색")).toBeInTheDocument();
    expect(screen.getByText("후보 검색 중")).toBeInTheDocument();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });
});
