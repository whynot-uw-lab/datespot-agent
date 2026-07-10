# Naver Map Playwright Routing Guide

검증일: 2026-07-09 KST

## 목표 플로우

1. 네이버지도 열기
2. `신사역` 검색
3. `신사역 신분당선` 결과 클릭으로 지도 기준점 이동
4. 지도 기준점이 신사역인 상태에서 `음식점` 검색
5. 음식점 목록 추출
6. 목록의 1번째 음식점 클릭
7. 내부 사진 조회
8. 최신순 방문자 리뷰 50개 로딩
9. 목록으로 복귀 후 2번째 음식점도 동일 반복

## 검증 결과 요약

- 초기 URL: `https://map.naver.com/p?c=15.00,0,0,0,dh`
- `신사역` 검색 성공 URL:
  `https://map.naver.com/p/search/%EC%8B%A0%EC%82%AC%EC%97%AD?c=15.00,0,0,0,dh`
- 역 선택 성공 URL:
  `https://map.naver.com/p/search/%EC%8B%A0%EC%82%AC%EC%97%AD/subway-station/1907?...`
- 음식점 검색 성공 URL:
  `https://map.naver.com/p/search/%EC%9D%8C%EC%8B%9D%EC%A0%90?c=15.00,0,0,0,dh`
- 음식점 목록 iframe:
  `#searchIframe`
- 상세 iframe:
  `#entryIframe` 역할, 실제 frame URL은 `https://pcmap.place.naver.com/restaurant/{placeId}/...`
- 실제 반복 검증:
  - 1번째 결과: `명우한우 신사역 본점`, placeId `33585987`
  - 2번째 결과: `을지다락 신사가로수길`, placeId `1797169863`
  - 두 곳 모두 내부 사진 필터 진입 성공
  - 두 곳 모두 최신순 리뷰 50개 로딩 성공

## 핵심 주의사항

- `Enter` 키 검색은 한 번 timeout 발생. 자동완성의 exact option 클릭이 더 안정적.
- 상단 `검색` 버튼 클릭만으로는 URL 변화가 없었던 케이스 확인.
- 역 결과 클릭은 자식 `span`이 포인터를 가로막음. `force: true` 필요.
- 음식점 목록 iframe URL은 환경에 따라 `/restaurant/list` 또는 `/place/list` 형태 가능.
- 음식점 상위 결과에는 광고가 포함될 수 있음. `광고` 텍스트로 판별 가능.
- `내부` 버튼은 사진 페이지에서 2개 잡힘. 썸네일 `내부`와 필터 chip `내부`를 구분해야 함.
- 리뷰의 개별 본문 `더보기`와 목록 하단 `펼쳐서 더보기`는 다름. 리뷰 추가 로딩은 정확히 `펼쳐서 더보기`.
- 상세 내부 `이전 페이지`는 목록 복귀가 아니라 상세 내부 이전 화면으로 이동함. 목록 복귀는 `페이지 닫기`.
- `page.waitForURL()`은 `waitUntil: "domcontentloaded"` 권장. 기본 `load`는 네이버지도에서 timeout 가능.

## 1. 네이버지도 열기

```ts
await page.goto("https://map.naver.com/", { waitUntil: "domcontentloaded" });
```

## 2. `신사역` 검색

검색 input은 최상위 페이지의 combobox.

```ts
await page.getByRole("combobox").fill("신사역");
await page.getByRole("option", { name: "검색어 신사역", exact: true }).click();
await page.waitForURL(/\/p\/search\//, {
  timeout: 20_000,
  waitUntil: "domcontentloaded",
});
```

## 3. 신사역 결과 클릭

검색 결과 목록은 `#searchIframe` 내부.

```ts
const searchFrame = page.frameLocator("#searchIframe");

await searchFrame
  .getByRole("button", { name: "신사역 신분당선지하철,전철" })
  .click({ force: true, timeout: 10_000 });

await page.waitForURL(/subway-station\/1907/, {
  timeout: 20_000,
  waitUntil: "domcontentloaded",
});
```

## 4. `음식점` 검색

역 상세가 열린 상태에서도 상단 combobox를 다시 사용함.

```ts
await page.getByRole("combobox").fill("음식점");
await page.getByRole("option", { name: "검색어 음식점", exact: true }).click();
await page.waitForURL(/\/p\/search\//, {
  timeout: 20_000,
  waitUntil: "domcontentloaded",
});
```

음식점 검색 후 실제 목록 frame 찾기.

```ts
const listFrame = page
  .frames()
  .find((frame) =>
    /pcmap\.place\.naver\.com\/(restaurant|place)\/list/.test(frame.url()),
  );

if (!listFrame) throw new Error("restaurant list frame not found");
await listFrame.waitForSelector("li", { timeout: 20_000 });
```

검증된 iframe URL 예시:

```text
https://pcmap.place.naver.com/restaurant/list?query=음식점&x=127.01956640000168&y=37.51603560000058&...
```

## 5. 음식점 목록 추출

결과 카드는 `li` 단위. 카드의 첫 `a[role=button]`이 상세 진입용 제목 링크로 동작함.

```ts
const items = (
  await listFrame.$$eval("li", (rows) =>
    rows.map((row, domIndex) => {
      const text = row.innerText.replace(/\s+/g, " ").trim();
      const controls = Array.from(row.querySelectorAll("a, button")).map((el) => ({
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute("role"),
        href: el.getAttribute("href"),
        text: (el.textContent || "").replace(/\s+/g, " ").trim(),
        className: String(el.className || ""),
      }));

      const titleControl = controls.find((control) => {
        if (control.tag !== "a" || control.role !== "button") return false;
        if (!control.text) return false;
        return !["저장", "더보기", "광고", "이전", "다음"].includes(control.text);
      });

      return {
        domIndex,
        rawText: text,
        clickText: titleControl?.text ?? "",
        isAd: text.includes("광고"),
      };
    }),
  )
).filter((item) => item.rawText && item.clickText);
```

검증 당시 첫 목록 일부:

```text
0 명우한우 신사역 본점 예약 톡톡 쿠폰 육류,고기요리 광고
1 을지다락 신사가로수길 예약 톡톡 쿠폰 양식 광고
2 호보식당 신사역직영점 예약 톡톡 쿠폰 한식 광고
3 카이센동 우니도 본점 쿠폰 일식당
4 치보 신사점 예약 쿠폰 일식당
```

광고 제외가 필요하면:

```ts
const organicItems = items.filter((item) => !item.isAd);
```

## 6. 목록 N번째 음식점 클릭

`items[index].domIndex`를 사용하면 추출한 목록 순서와 클릭 대상을 맞출 수 있음.

```ts
async function openListItem(page: Page, listFrame: Frame, item: { domIndex: number }) {
  const row = listFrame.locator("li").nth(item.domIndex);
  const titleLink = row.locator("a[role=button]").first();

  await titleLink.click({ timeout: 10_000 });
  await page.waitForTimeout(2_500);

  const placeId = page.url().match(/\/place\/(\d+)/)?.[1];
  if (!placeId) throw new Error(`placeId not found from URL: ${page.url()}`);

  return placeId;
}
```

상세 진입 후 top-level URL:

```text
https://map.naver.com/p/search/음식점/place/{placeId}?...
```

상세 iframe URL:

```text
https://pcmap.place.naver.com/restaurant/{placeId}/home?...
```

## 7. 내부 사진 조회

사진 tab으로 이동 후 `내부` 필터 chip 클릭.

```ts
async function openInteriorPhotos(page: Page, placeId: string) {
  const homeFrame = page
    .frames()
    .find((frame) => new RegExp(`/restaurant/${placeId}/home`).test(frame.url()));

  if (!homeFrame) throw new Error(`home frame not found: ${placeId}`);

  await homeFrame.getByRole("tab", { name: "사진" }).click({ timeout: 10_000 });
  await page.waitForTimeout(2_000);

  const photoFrame = page
    .frames()
    .find((frame) => new RegExp(`/restaurant/${placeId}/photo`).test(frame.url()));

  if (!photoFrame) throw new Error(`photo frame not found: ${placeId}`);

  // getByRole("button", { name: "내부" })는 썸네일과 필터가 같이 잡혀 2개가 될 수 있음.
  const interiorChip = photoFrame
    .locator("a[role=button].sbkBy")
    .filter({ hasText: "내부" });

  if ((await interiorChip.count()) !== 1) {
    throw new Error("interior filter chip not unique");
  }

  await interiorChip.click({ timeout: 10_000 });
  await page.waitForTimeout(1_500);

  if (!photoFrame.url().includes("subFilter=INTERIOR")) {
    throw new Error(`interior filter not applied: ${photoFrame.url()}`);
  }

  return photoFrame;
}
```

필터 성공 URL 특징:

```text
.../photo?...&filterType=AI%20View&subFilter=INTERIOR
```

내부 사진 URL 추출:

```ts
const photos = await photoFrame.$$eval("img", (imgs) =>
  imgs
    .map((img) => img.currentSrc || img.src)
    .filter(Boolean)
    .filter((src) =>
      [
        "search.pstatic.net/common",
        "ldb-phinf.pstatic.net",
        "blogfiles.pstatic.net",
        "pup-review-phinf.pstatic.net",
      ].some((part) => src.includes(part)),
    ),
);
```

## 8. 최신순 방문자 리뷰 50개 조회

리뷰 tab 클릭 시 방문자 리뷰 frame으로 진입함.

```ts
async function openRecentVisitorReviews(page: Page, placeId: string) {
  const currentEntryFrame = page
    .frames()
    .find((frame) => new RegExp(`/restaurant/${placeId}/`).test(frame.url()));

  if (!currentEntryFrame) throw new Error(`entry frame not found: ${placeId}`);

  await currentEntryFrame.getByRole("tab", { name: "리뷰" }).click({ timeout: 10_000 });
  await page.waitForTimeout(2_000);

  const reviewFrame = page
    .frames()
    .find((frame) =>
      new RegExp(`/restaurant/${placeId}/review/visitor`).test(frame.url()),
    );

  if (!reviewFrame) throw new Error(`visitor review frame not found: ${placeId}`);

  const recentOption = reviewFrame.getByRole("option", { name: "최신순" });
  if ((await recentOption.count()) === 1) {
    await recentOption.click({ timeout: 10_000 });
    await page.waitForTimeout(1_500);
  }

  if (!reviewFrame.url().includes("reviewSort=recent")) {
    throw new Error(`recent sort not applied: ${reviewFrame.url()}`);
  }

  return reviewFrame;
}
```

리뷰 카드 selector:

```ts
const reviewCards = reviewFrame.locator("li.place_apply_pui");
```

50개까지 추가 로딩:

```ts
async function loadReviewCards(page: Page, reviewFrame: Frame, targetCount = 50) {
  const history = [];

  for (let i = 0; i < 10; i += 1) {
    await reviewFrame.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(800);

    const cardCount = await reviewFrame.locator("li.place_apply_pui").count();
    const moreButton = reviewFrame.getByRole("button", {
      name: "펼쳐서 더보기",
    });
    const moreCount = await moreButton.count();

    history.push({ i, cardCount, moreCount });

    if (cardCount >= targetCount) break;
    if (moreCount === 0) break;

    await moreButton.click({ timeout: 10_000 });
    await page.waitForTimeout(1_500);
  }

  const finalCount = await reviewFrame.locator("li.place_apply_pui").count();
  return { finalCount, history };
}
```

검증 당시 로딩 변화:

```text
10 -> 20 -> 30 -> 40 -> 50
```

## 9. 목록으로 복귀 후 다음 음식점 반복

상세 처리 완료 후 목록 복귀는 `페이지 닫기`.

```ts
async function closeEntry(page: Page, placeId: string) {
  const entryFrame = page
    .frames()
    .find((frame) => new RegExp(`/restaurant/${placeId}/`).test(frame.url()));

  if (!entryFrame) throw new Error(`entry frame not found: ${placeId}`);

  await entryFrame.getByRole("button", { name: "페이지 닫기" }).click({
    timeout: 10_000,
  });
  await page.waitForTimeout(2_000);

  const listFrame = page
    .frames()
    .find((frame) =>
      /pcmap\.place\.naver\.com\/(restaurant|place)\/list/.test(frame.url()),
    );

  if (!listFrame) throw new Error("restaurant list frame not restored");
  return listFrame;
}
```

반복 구조:

```ts
await searchSinsa(page);
const listFrame = await searchRestaurantsFromCurrentMap(page);
const items = await extractRestaurantItems(listFrame);

for (const item of items.slice(0, 2)) {
  const placeId = await openListItem(page, listFrame, item);
  await openInteriorPhotos(page, placeId);
  const reviewFrame = await openRecentVisitorReviews(page, placeId);
  await loadReviewCards(page, reviewFrame, 50);
  await closeEntry(page, placeId);
}
```

## 전체 예시 코드

아래 코드는 검증에 사용한 흐름을 축약한 예시. `Frame`, `Page`는 Playwright 타입.

```ts
async function searchSinsa(page: Page) {
  await page.goto("https://map.naver.com/", { waitUntil: "domcontentloaded" });

  await page.getByRole("combobox").fill("신사역");
  await page.getByRole("option", { name: "검색어 신사역", exact: true }).click();
  await page.waitForURL(/\/p\/search\//, {
    timeout: 20_000,
    waitUntil: "domcontentloaded",
  });

  await page
    .frameLocator("#searchIframe")
    .getByRole("button", { name: "신사역 신분당선지하철,전철" })
    .click({ force: true, timeout: 10_000 });

  await page.waitForURL(/subway-station\/1907/, {
    timeout: 20_000,
    waitUntil: "domcontentloaded",
  });
}

async function searchRestaurantsFromCurrentMap(page: Page) {
  await page.getByRole("combobox").fill("음식점");
  await page.getByRole("option", { name: "검색어 음식점", exact: true }).click();
  await page.waitForURL(/\/p\/search\//, {
    timeout: 20_000,
    waitUntil: "domcontentloaded",
  });

  const listFrame = page
    .frames()
    .find((frame) =>
      /pcmap\.place\.naver\.com\/(restaurant|place)\/list/.test(frame.url()),
    );

  if (!listFrame) throw new Error("restaurant list frame not found");
  await listFrame.waitForSelector("li", { timeout: 20_000 });
  return listFrame;
}

async function extractRestaurantItems(listFrame: Frame) {
  return (
    await listFrame.$$eval("li", (rows) =>
      rows.map((row, domIndex) => {
        const text = row.innerText.replace(/\s+/g, " ").trim();
        const titleLink = Array.from(row.querySelectorAll("a[role=button]")).find(
          (el) => {
            const label = (el.textContent || "").replace(/\s+/g, " ").trim();
            return label && !["저장", "더보기", "광고", "이전", "다음"].includes(label);
          },
        );

        return {
          domIndex,
          rawText: text,
          clickText: (titleLink?.textContent || "").replace(/\s+/g, " ").trim(),
          isAd: text.includes("광고"),
        };
      }),
    )
  ).filter((item) => item.rawText && item.clickText);
}

async function openRestaurant(page: Page, listFrame: Frame, domIndex: number) {
  const row = listFrame.locator("li").nth(domIndex);
  await row.locator("a[role=button]").first().click({ timeout: 10_000 });
  await page.waitForTimeout(2_500);

  const placeId = page.url().match(/\/place\/(\d+)/)?.[1];
  if (!placeId) throw new Error(`placeId not found: ${page.url()}`);
  return placeId;
}

async function processRestaurant(page: Page, placeId: string) {
  const photoFrame = await openInteriorPhotos(page, placeId);
  const photoUrls = await photoFrame.$$eval("img", (imgs) =>
    imgs.map((img) => img.currentSrc || img.src).filter(Boolean),
  );

  const reviewFrame = await openRecentVisitorReviews(page, placeId);
  const reviewLoad = await loadReviewCards(page, reviewFrame, 50);
  const reviewTexts = await reviewFrame.$$eval("li.place_apply_pui", (rows) =>
    rows.slice(0, 50).map((row) => row.innerText.replace(/\s+/g, " ").trim()),
  );

  return {
    photoCount: photoUrls.length,
    reviewCount: reviewLoad.finalCount,
    reviews: reviewTexts,
  };
}
```

## 직접 pcmap URL 사용 옵션

목록 클릭/반복은 map shell이 편함. 단, 상세 데이터만 추출할 때는 `pcmap.place.naver.com` 직접 URL이 더 단순함.

```text
https://pcmap.place.naver.com/restaurant/{placeId}/home
https://pcmap.place.naver.com/restaurant/{placeId}/photo?filterType=AI%20View&subFilter=INTERIOR
https://pcmap.place.naver.com/restaurant/{placeId}/review/visitor?reviewSort=recent
```

직접 URL을 쓰면 `#entryIframe` 처리가 사라지고 top-level page가 곧 상세 페이지가 됨.

## 검증된 실패/대체 경로

- 실패: `await page.getByRole("combobox").press("Enter")`
  - 증상: timeout
  - 대체: 자동완성 option exact 클릭
- 실패: `검색` 버튼 클릭만으로 검색 submit 기대
  - 증상: URL 변화 없음
  - 대체: 자동완성 option exact 클릭
- 실패: 역 결과 일반 클릭
  - 증상: child `span`이 pointer intercept
  - 대체: `.click({ force: true })`
- 주의: `reviewFrame.getByRole("button", { name: "더보기" })`
  - 의미: 개별 리뷰 본문 펼침일 가능성 큼
  - 대체: `getByRole("button", { name: "펼쳐서 더보기" })`
- 주의: 상세 `이전 페이지`
  - 의미: 상세 내부 이전 화면
  - 목록 복귀: `페이지 닫기`
