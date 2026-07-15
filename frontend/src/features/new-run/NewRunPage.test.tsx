import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NewRunPage } from "./NewRunPage";

const LocationProbe = () => <div data-testid="location">{useLocation().pathname}</div>;

afterEach(() => vi.unstubAllGlobals());

describe("NewRunPage", () => {
  it("submits the configured weights and navigates to the accepted run", async () => {
    const user = userEvent.setup();
    const fetcher = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          runId: "run-77",
          status: "queued",
          statusUrl: "/runs/run-77",
          reportUrl: "/runs/run-77/report",
        }),
        { status: 202, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetcher);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/app/"]}>
          <Routes>
            <Route path="/app/" element={<NewRunPage />} />
            <Route path="*" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await user.type(screen.getByLabelText("어디에서 만날까요?"), "성수역");
    await user.type(screen.getByLabelText("어떤 장소를 찾을까요?"), "이탈리안");
    await user.clear(screen.getByLabelText("확인할 장소 수"));
    await user.type(screen.getByLabelText("확인할 장소 수"), "3");
    fireEvent.change(screen.getByRole("slider", { name: "사진 비중" }), {
      target: { value: "70" },
    });

    expect(screen.getByText("리뷰 30%")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "장소 탐색 시작" }));

    expect(await screen.findByTestId("location")).toHaveTextContent(
      "/app/runs/run-77",
    );
    const [, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toMatchObject({
      location: "성수역",
      searchKeyword: "이탈리안",
      maxPlaces: 3,
      weights: { photoPercent: 70, reviewPercent: 30 },
    });
  });

  it("connects backend validation errors to their fields", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        detail: [{ loc: ["body", "location"], msg: "지역을 입력해 주세요" }],
      }), { status: 422, headers: { "Content-Type": "application/json" } }),
    ));
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/app/"]}><NewRunPage /></MemoryRouter>
      </QueryClientProvider>,
    );

    await user.type(screen.getByLabelText("어디에서 만날까요?"), "   ");
    await user.type(screen.getByLabelText("어떤 장소를 찾을까요?"), "일식");
    await user.click(screen.getByRole("button", { name: "장소 탐색 시작" }));

    const location = screen.getByLabelText("어디에서 만날까요?");
    expect(await screen.findByText("지역을 입력해 주세요")).toHaveAttribute("id", "location-error");
    expect(location).toHaveAttribute("aria-describedby", "location-error");
    expect(location).toHaveAttribute("aria-invalid", "true");
  });
});
