import { expect, test } from "@playwright/test";

const config = {
  location: "성수역",
  searchKeyword: "이탈리안",
  maxPlaces: 3,
  weights: { photoPercent: 50, reviewPercent: 50 },
  scoring: { photo: "차분한 분위기", review: "대화하기 좋음" },
};

const report = {
  runId: "run-e2e",
  status: "completed",
  config,
  errors: [],
  createdAt: "2026-07-15T02:00:00Z",
  results: [
    {
      status: "analyzed",
      placeId: "place-1",
      name: "오스테리아 오르조",
      category: "이탈리아음식",
      address: "서울 성동구 연무장길 17",
      photoScore: 9,
      reviewScore: 8,
      finalScore: 8.5,
      photoReason: "따뜻한 조명과 여유 있는 좌석",
      reviewReason: "대화하기 좋다는 평가가 반복됨",
    },
  ],
};

test("new search flows through live progress into saved report", async ({ page }) => {
  await page.route("**/runs", async (route) => {
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        runId: "run-e2e",
        status: "queued",
        statusUrl: "/runs/run-e2e",
        reportUrl: "/runs/run-e2e/report",
      }),
    });
  });
  await page.route("**/runs/run-e2e", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        runId: "run-e2e",
        status: "running",
        config,
        createdAt: "2026-07-15T02:00:00Z",
        reportAvailable: false,
      }),
    });
  });
  await page.route("**/runs/run-e2e/events", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 350));
    const events = [
      { sequence: 1, type: "running", data: { status: "running", reportAvailable: false } },
      { sequence: 2, type: "progress", data: { stage: "candidate_search", message: "성수역 후보 검색 중" } },
      { sequence: 3, type: "place_result", data: report.results[0] },
      { sequence: 4, type: "completed", data: { status: "completed", reportAvailable: true } },
    ].map((item) => [
      `id: ${item.sequence}`,
      `event: ${item.type}`,
      `data: ${JSON.stringify({ runId: "run-e2e", occurredAt: "2026-07-15T02:00:00Z", ...item })}`,
      "",
    ].join("\n")).join("\n");
    await route.fulfill({
      contentType: "text/event-stream",
      body: `${events}\n`,
    });
  });
  await page.route("**/runs/run-e2e/report", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(report) });
  });
  await page.route("**/reports?**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        items: [{
          runId: report.runId,
          status: report.status,
          config,
          createdAt: report.createdAt,
          resultCount: 1,
          errorCount: 0,
          reportUrl: "/reports/run-e2e",
        }],
        nextCursor: null,
        invalidReportCount: 0,
      }),
    });
  });
  await page.route("**/reports/run-e2e", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(report) });
  });
  await page.routeWebSocket("**/runs/run-e2e/browser-stream", (socket) => {
    socket.send(JSON.stringify({ type: "waiting" }));
    socket.send(Buffer.from("/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAEf/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABD/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/EH//xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/EH//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/EH//2Q==", "base64"));
  });

  await page.goto("/app/");
  await expect(page.getByRole("heading", { name: /대화가 오래 머무는/ })).toBeVisible();
  await page.screenshot({ path: "output/playwright/home-1440.png", fullPage: true });

  await page.getByLabel("어디에서 만날까요?").fill("성수역");
  await page.getByLabel("어떤 장소를 찾을까요?").fill("이탈리안");
  await page.getByRole("button", { name: "장소 탐색 시작" }).click();

  await expect(page).toHaveURL(/\/app\/runs\/run-e2e$/);
  await expect(page.getByRole("img", { name: "실시간 지도 탐색 화면" })).toBeVisible();
  await expect(page.getByText("성수역 후보 검색 중")).toBeVisible();
  await expect(page.getByRole("heading", { name: "오스테리아 오르조" })).toBeVisible();
  await expect(page.getByLabel("최종 점수 8.5")).toBeVisible();
  await page.screenshot({ path: "output/playwright/result-1440.png", fullPage: true });
  await page.setViewportSize({ width: 1024, height: 900 });
  await expect(page.getByLabel("브라우저 실시간 화면")).toBeVisible();
  await expect(page.getByLabel("실행 진행 단계")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(1024);
  await page.screenshot({ path: "output/playwright/result-1024.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });

  await page.getByRole("link", { name: "저장 리포트" }).click();
  const saved = page.getByRole("link", { name: /성수역/ });
  await expect(saved).toBeVisible();
  await saved.click();
  await expect(page).toHaveURL(/\/app\/reports\/run-e2e$/);
  await expect(page.getByRole("heading", { name: "오스테리아 오르조" })).toBeVisible();
});
