import { afterEach, describe, expect, it, vi } from "vitest";

import { AppError, buildReportQuery, requestJson } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("requestJson", () => {
  it("returns parsed JSON for a successful response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ status: "ok" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(requestJson<{ status: string }>("/health")).resolves.toEqual({
      status: "ok",
    });
  });

  it("maps the backend error envelope to AppError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              code: "report_not_found",
              message: "저장 리포트를 찾을 수 없음",
            },
          }),
          { status: 404, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const error = await requestJson("/reports/missing").catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(AppError);
    expect(error).toMatchObject({
      status: 404,
      code: "report_not_found",
      message: "저장 리포트를 찾을 수 없음",
    });
  });

  it("maps FastAPI validation locations to field errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({
          detail: [
            { loc: ["body", "location"], msg: "지역을 입력해 주세요" },
            { loc: ["body", "scoring", "photo"], msg: "사진 기준을 입력해 주세요" },
          ],
        }), { status: 422, headers: { "Content-Type": "application/json" } }),
      ),
    );

    const error = await requestJson("/runs").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(AppError);
    expect(error).toMatchObject({
      status: 422,
      code: "validation_error",
      fieldErrors: {
        location: "지역을 입력해 주세요",
        "scoring.photo": "사진 기준을 입력해 주세요",
      },
    });
  });
});

describe("buildReportQuery", () => {
  it("omits empty filters and includes the cursor", () => {
    expect(
      buildReportQuery({
        status: "completed",
        location: "  ",
        searchKeyword: "일식",
        dateFrom: "",
        dateTo: "2026-07-15",
        cursor: "next-page",
      }),
    ).toBe(
      "?limit=20&status=completed&searchKeyword=%EC%9D%BC%EC%8B%9D&dateTo=2026-07-15&cursor=next-page",
    );
  });
});
