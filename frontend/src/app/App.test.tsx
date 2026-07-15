import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("renders the desktop shell and the new search route", () => {
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/app/"]}><App /></MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByRole("link", { name: "DateSpot 홈" })).toHaveAttribute("href", "/app/");
    expect(screen.getByRole("link", { name: "저장 리포트" })).toHaveAttribute("href", "/app/reports");
    expect(screen.getByRole("heading", { name: /대화가 오래 머무는/ })).toBeInTheDocument();
  });
});
