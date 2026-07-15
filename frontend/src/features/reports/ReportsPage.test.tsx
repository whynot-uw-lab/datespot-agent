import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReportsPage } from "./ReportsPage";

const LocationProbe = () => <output data-testid="search">{useLocation().search}</output>;

afterEach(() => vi.unstubAllGlobals());

describe("ReportsPage", () => {
  it("hydrates filters from the URL and writes submitted filters back", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            items: [
              {
                runId: "run-1",
                status: "completed",
                createdAt: "2026-07-15T02:00:00Z",
                resultCount: 3,
                errorCount: 0,
                reportUrl: "/reports/run-1",
                config: {
                  location: "성수역",
                  searchKeyword: "이탈리안",
                  maxPlaces: 3,
                  weights: { photoPercent: 50, reviewPercent: 50 },
                  scoring: { photo: "분위기", review: "대화" },
                },
              },
            ],
            nextCursor: null,
            invalidReportCount: 1,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/app/reports?location=성수역"]}>
          <ReportsPage />
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByLabelText("지역")).toHaveValue("성수역");
    expect(await screen.findByRole("link", { name: /성수역/ })).toHaveAttribute(
      "href",
      "/app/reports/run-1",
    );
    expect(screen.getByText("손상된 리포트 1개는 제외됨")).toBeInTheDocument();

    await user.type(screen.getByLabelText("검색어"), "이탈리안");
    await user.click(screen.getByRole("button", { name: "필터 적용" }));
    expect(screen.getByTestId("search")).toHaveTextContent(
      "?location=%EC%84%B1%EC%88%98%EC%97%AD&searchKeyword=%EC%9D%B4%ED%83%88%EB%A6%AC%EC%95%88",
    );
  });
});
