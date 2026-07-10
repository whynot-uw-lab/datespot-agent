"""1-2 네이버지도 탐색 PoC 자동화.

실행: uv run python poc/1-2-naver-map-flow/explore.py

결과는 poc/1-2-naver-map-flow/output/naver_map_flow_result.json 에 저장된다.
동적 클래스명 의존을 줄이기 위해 role, iframe URL, DOM 구조, direct pcmap route를 사용한다.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from urllib.parse import quote
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Pattern

from playwright.async_api import BrowserContext, Frame, Page, TimeoutError, async_playwright

from datespot_agent.config import get_settings

STATION_QUERY = "신사역"
CATEGORY_QUERY = "음식점"
STATION_RESULT_PATTERN = re.compile("신사역.*신분당선")
LIST_FRAME_PATTERN = re.compile(r"pcmap\.place\.naver\.com/(restaurant|place)/list")
TARGET_ORGANIC_PLACES = 2
REVIEW_TARGET_COUNT = 50
DEFAULT_TIMEOUT_MS = 20_000

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_PATH = OUTPUT_DIR / "naver_map_flow_result.json"

EXCLUDED_CONTROL_TEXTS = {"저장", "더보기", "광고", "이전", "다음"}
TITLE_SUFFIX_MARKERS = ("플레이스 플러스", "예약", "쿠폰", "영업", "별점", "리뷰", "저장")
PHOTO_HOST_MARKERS = (
    "search.pstatic.net/common",
    "ldb-phinf.pstatic.net",
    "blogfiles.pstatic.net",
    "pup-review-phinf.pstatic.net",
)


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def clean_place_title(value: str) -> str:
    title = normalize_text(value)
    for marker in TITLE_SUFFIX_MARKERS:
        index = title.find(marker)
        if index > 0:
            title = title[:index]
    return normalize_text(title)


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def extract_place_id_from_url(url: str) -> str | None:
    match = re.search(r"/(?:place|restaurant)/(\d+)", url)
    return match.group(1) if match else None


def build_place_routes(place_id: str) -> dict[str, str]:
    base = f"https://pcmap.place.naver.com/restaurant/{place_id}"
    return {
        "home": f"{base}/home",
        "photos": f"{base}/photo?filterType=AI%20View&subFilter=INTERIOR",
        "reviews": f"{base}/review/visitor?reviewSort=recent",
    }


def build_map_search_url(query: str) -> str:
    return f"https://map.naver.com/p/search/{quote(query)}"


def build_category_queries(station: str, category: str) -> list[str]:
    primary = f"{station} {category}"
    fallback = f"{station} 맛집" if category == "음식점" else primary
    return dedupe_preserve_order([primary, fallback])


def filter_photo_urls(urls: list[str]) -> list[str]:
    return dedupe_preserve_order(
        [
            url
            for url in urls
            if url and any(marker in url for marker in PHOTO_HOST_MARKERS)
        ]
    )


def parse_list_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        raw_text = normalize_text(row.get("rawText"))
        controls = row.get("controls") or []
        title_control = next(
            (
                control
                for control in controls
                if control.get("tag") == "a"
                and control.get("role") == "button"
                and normalize_text(control.get("text"))
                and normalize_text(control.get("text")) not in EXCLUDED_CONTROL_TEXTS
            ),
            None,
        )

        if not raw_text or not title_control:
            continue

        title = clean_place_title(normalize_text(title_control.get("text")))
        href = title_control.get("href") or ""
        item = {
            "domIndex": int(row.get("domIndex", len(items))),
            "rawText": raw_text,
            "clickText": title,
            "isAd": "광고" in raw_text,
        }
        place_id = extract_place_id_from_url(href)
        if place_id:
            item["placeId"] = place_id
        items.append(item)
    return items


async def find_frame(page: Page, pattern: Pattern[str]) -> Frame | None:
    for frame in page.frames:
        if pattern.search(frame.url):
            return frame
    return None


async def wait_for_frame(page: Page, pattern: Pattern[str], timeout_ms: int) -> Frame:
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
    while asyncio.get_running_loop().time() < deadline:
        frame = await find_frame(page, pattern)
        if frame is not None:
            return frame
        await page.wait_for_timeout(250)
    raise TimeoutError(f"frame not found: {pattern.pattern}")


async def click_search_option(page: Page, query: str) -> None:
    await page.get_by_role("combobox").fill(query, timeout=DEFAULT_TIMEOUT_MS)
    option = page.get_by_role("option", name=f"검색어 {query}", exact=True)
    await option.click(timeout=DEFAULT_TIMEOUT_MS)
    await page.wait_for_url(re.compile(r"/p/search/"), wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)


async def body_sample(page: Page, limit: int = 500) -> str:
    try:
        body = await page.locator("body").inner_text(timeout=2_000)
        return normalize_text(body)[:limit]
    except Exception:
        return ""


async def search_station(page: Page) -> dict[str, Any]:
    status: dict[str, Any] = {
        "query": STATION_QUERY,
        "url": "",
        "selectedStation": False,
        "error": "",
    }
    try:
        await page.goto(
            build_map_search_url(STATION_QUERY),
            wait_until="domcontentloaded",
            timeout=DEFAULT_TIMEOUT_MS,
        )
        status["url"] = page.url
        await wait_for_frame(
            page,
            re.compile(r"pcmap\.place\.naver\.com/(place|restaurant)/list"),
            8_000,
        )
    except Exception as e:  # noqa: BLE001
        status["error"] = f"station search: {type(e).__name__}: {e}"
        status["bodySample"] = await body_sample(page)
        return status

    search_frame = page.frame_locator("#searchIframe")
    station_button = search_frame.get_by_role("button", name=STATION_RESULT_PATTERN)
    if await station_button.count() == 0:
        status["error"] = "station result button not found"
        return status

    try:
        await station_button.first.click(force=True, timeout=5_000)
        await page.wait_for_url(
            re.compile(r"subway-station/1907"),
            wait_until="domcontentloaded",
            timeout=5_000,
        )
        status["url"] = page.url
        status["selectedStation"] = True
    except Exception:
        status["error"] = "station click did not change route; continuing with category direct search"
    return status


async def search_category(context: BrowserContext) -> tuple[Page, Frame, dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for query in build_category_queries(STATION_QUERY, CATEGORY_QUERY):
        for attempt in range(1, 4):
            page = await context.new_page()
            try:
                await page.goto(
                    build_map_search_url(query),
                    wait_until="domcontentloaded",
                    timeout=DEFAULT_TIMEOUT_MS,
                )
                list_frame = await wait_for_frame(page, LIST_FRAME_PATTERN, 15_000)
                await list_frame.wait_for_selector("li", timeout=DEFAULT_TIMEOUT_MS)
                return page, list_frame, {
                    "query": query,
                    "attempt": attempt,
                    "url": page.url,
                    "listFrameUrl": list_frame.url,
                    "diagnostics": diagnostics,
                }
            except Exception as e:  # noqa: BLE001
                diagnostics.append(
                    {
                        "query": query,
                        "attempt": attempt,
                        "url": page.url,
                        "error": f"{type(e).__name__}: {e}",
                        "pcmapFrames": [frame.url for frame in page.frames if "pcmap" in frame.url],
                        "bodySample": await body_sample(page),
                    }
                )
                await page.close()

    raise RuntimeError(f"restaurant list frame not found after retries: {diagnostics}")


async def extract_raw_list_rows(list_frame: Frame) -> list[dict[str, Any]]:
    return await list_frame.eval_on_selector_all(
        "li",
        """(rows) => rows.map((row, domIndex) => {
          const text = (row.innerText || "").replace(/\\s+/g, " ").trim();
          const controls = Array.from(row.querySelectorAll("a, button")).map((el) => ({
            tag: el.tagName.toLowerCase(),
            role: el.getAttribute("role"),
            href: el.getAttribute("href"),
            text: (el.textContent || "").replace(/\\s+/g, " ").trim(),
          }));

          return { domIndex, rawText: text, controls };
        })""",
    )


async def js_click(locator) -> None:
    handle = await locator.element_handle(timeout=DEFAULT_TIMEOUT_MS)
    if handle is None:
        raise TimeoutError("click target not found")
    await handle.evaluate("(el) => el.click()")


async def click_list_item_for_place_id(page: Page, list_frame: Frame, item: dict[str, Any]) -> str:
    row = list_frame.locator("li").nth(item["domIndex"])
    title_link = row.locator("a[role=button]").filter(has_text=item["clickText"]).first
    try:
        await title_link.click(timeout=10_000)
    except Exception:
        await js_click(title_link)

    await page.wait_for_timeout(2_500)
    place_id = extract_place_id_from_url(page.url)
    if place_id:
        return place_id

    for frame in page.frames:
        place_id = extract_place_id_from_url(frame.url)
        if place_id:
            return place_id

    raise RuntimeError(f"placeId not found after clicking: {item['clickText']}")


async def close_entry_if_open(page: Page, place_id: str) -> None:
    entry_frame = next((frame for frame in page.frames if f"/restaurant/{place_id}/" in frame.url), None)
    if entry_frame is None:
        return

    close_button = entry_frame.get_by_role("button", name=re.compile("페이지 닫기"))
    if await close_button.count() == 0:
        return
    try:
        await close_button.first.click(timeout=5_000)
    except Exception:
        await js_click(close_button.first)
    await page.wait_for_timeout(1_500)


async def collect_place_ids(page: Page, list_frame: Frame, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in items:
        if item["isAd"]:
            continue
        enriched = dict(item)
        if "placeId" not in enriched:
            enriched["placeId"] = await click_list_item_for_place_id(page, list_frame, enriched)
            await close_entry_if_open(page, enriched["placeId"])
            list_frame = await wait_for_frame(page, LIST_FRAME_PATTERN, DEFAULT_TIMEOUT_MS)
        selected.append(enriched)
        if len(selected) >= TARGET_ORGANIC_PLACES:
            break
    return selected


async def goto_detail_page(page: Page, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    await page.wait_for_timeout(2_000)


async def extract_home_metadata(page: Page, route: str, name: str) -> dict[str, Any]:
    await goto_detail_page(page, route)
    body_text = normalize_text(await page.locator("body").inner_text(timeout=DEFAULT_TIMEOUT_MS))
    lines = [
        normalize_text(line)
        for line in (await page.locator("body").inner_text(timeout=DEFAULT_TIMEOUT_MS)).splitlines()
        if normalize_text(line)
    ]

    category = ""
    address = ""
    for idx, line in enumerate(lines[:80]):
        if line == name and idx + 1 < len(lines):
            category = lines[idx + 1]
        if line.startswith("서울 "):
            address = line
            break

    return {
        "bodyTextSample": body_text[:500],
        "category": category,
        "address": address,
    }


async def scroll_page(page: Page, rounds: int = 3) -> None:
    for _ in range(rounds):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(800)


async def extract_photo_urls(page: Page, route: str) -> list[str]:
    await goto_detail_page(page, route)
    await scroll_page(page, rounds=3)
    urls = await page.eval_on_selector_all(
        "img",
        """(imgs) => imgs.flatMap((img) => {
          const values = [];
          if (img.currentSrc) values.push(img.currentSrc);
          if (img.src) values.push(img.src);
          if (img.srcset) {
            values.push(...img.srcset.split(",").map((part) => part.trim().split(/\\s+/)[0]));
          }
          return values;
        })""",
    )
    return filter_photo_urls([str(url) for url in urls])


async def click_review_more(page: Page) -> bool:
    more = page.get_by_role("button", name=re.compile("펼쳐서 더보기"))
    if await more.count() == 0:
        return False
    try:
        await more.first.click(timeout=5_000)
    except Exception:
        await js_click(more.first)
    await page.wait_for_timeout(1_500)
    return True


async def extract_review_candidates(page: Page, target_count: int) -> list[str]:
    rows = await page.eval_on_selector_all(
        "li",
        """(rows) => rows.map((row) => (row.innerText || "").replace(/\\s+/g, " ").trim())""",
    )
    candidates = []
    for row in rows:
        text = normalize_text(str(row))
        if len(text) < 20:
            continue
        if text in candidates:
            continue
        if any(marker in text for marker in ("방문", "별점", "음식", "맛", "친절", "데이트", "분위기")):
            candidates.append(text)
        if len(candidates) >= target_count:
            break
    return candidates


async def extract_reviews(page: Page, route: str, target_count: int) -> list[str]:
    await goto_detail_page(page, route)
    reviews: list[str] = []
    for _ in range(8):
        await scroll_page(page, rounds=1)
        reviews = await extract_review_candidates(page, target_count)
        if len(reviews) >= target_count:
            break
        clicked = await click_review_more(page)
        if not clicked:
            break
    return reviews[:target_count]


async def process_place(context: BrowserContext, item: dict[str, Any]) -> dict[str, Any]:
    page = await context.new_page()
    place_id = str(item["placeId"])
    routes = build_place_routes(place_id)
    result = {
        "name": item["clickText"],
        "placeId": place_id,
        "listRawText": item["rawText"],
        "routes": routes,
        "category": "",
        "address": "",
        "photoUrls": [],
        "photoCount": 0,
        "reviews": [],
        "reviewCount": 0,
        "errors": [],
    }

    try:
        metadata = await extract_home_metadata(page, routes["home"], item["clickText"])
        result["category"] = metadata["category"]
        result["address"] = metadata["address"]
        result["homeTextSample"] = metadata["bodyTextSample"]
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"home: {type(e).__name__}: {e}")

    try:
        photo_urls = await extract_photo_urls(page, routes["photos"])
        result["photoUrls"] = photo_urls
        result["photoCount"] = len(photo_urls)
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"photos: {type(e).__name__}: {e}")

    try:
        reviews = await extract_reviews(page, routes["reviews"], REVIEW_TARGET_COUNT)
        result["reviews"] = reviews
        result["reviewCount"] = len(reviews)
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"reviews: {type(e).__name__}: {e}")

    await page.close()
    return result


async def run_flow() -> dict[str, Any]:
    settings = get_settings()
    result: dict[str, Any] = {
        "ranAt": datetime.now(timezone.utc).isoformat(),
        "ok": False,
        "query": {
            "station": STATION_QUERY,
            "category": CATEGORY_QUERY,
            "targetOrganicPlaces": TARGET_ORGANIC_PLACES,
            "reviewTargetCount": REVIEW_TARGET_COUNT,
        },
        "navigation": {},
        "listItems": [],
        "places": [],
        "errors": [],
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=settings.headless)
        station_context = await browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1440, "height": 1000},
        )
        station_page = await station_context.new_page()
        station_context_closed = False
        search_context: BrowserContext | None = None
        search_page: Page | None = None

        try:
            result["navigation"]["station"] = await search_station(station_page)
            await station_context.close()
            station_context_closed = True

            search_context = await browser.new_context(
                locale="ko-KR",
                timezone_id="Asia/Seoul",
                viewport={"width": 1440, "height": 1000},
            )
            search_page, list_frame, category_navigation = await search_category(search_context)
            result["navigation"]["category"] = category_navigation

            raw_rows = await extract_raw_list_rows(list_frame)
            items = parse_list_rows(raw_rows)
            result["listItems"] = items[:20]
            selected = await collect_place_ids(search_page, list_frame, items)

            for item in selected:
                place_result = await process_place(search_context, item)
                result["places"].append(place_result)

            result["ok"] = any(
                place.get("placeId")
                and (place.get("photoCount", 0) > 0 or place.get("reviewCount", 0) > 0)
                for place in result["places"]
            )
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"{type(e).__name__}: {e}")
        finally:
            if not station_page.is_closed():
                await station_page.close()
            if search_page is not None and not search_page.is_closed():
                await search_page.close()
            if search_context is not None:
                await search_context.close()
            if not station_context_closed:
                await station_context.close()
            await browser.close()

    return result


def save_result(result: dict[str, Any]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return OUTPUT_PATH


async def async_main() -> int:
    result = await run_flow()
    out = save_result(result)
    status = "성공" if result["ok"] else "실패"
    print(f"=== 1-2 네이버지도 탐색 PoC {status} ===")
    print(f"결과: {out}")
    print(f"장소 수: {len(result['places'])}")
    for place in result["places"]:
        print(
            "- "
            f"{place['name']} ({place['placeId']}): "
            f"사진 {place['photoCount']}개, 리뷰 {place['reviewCount']}개, "
            f"오류 {len(place['errors'])}개"
        )
    if result["errors"]:
        print("오류:")
        for error in result["errors"]:
            print(f"- {error}")
    return 0 if result["ok"] else 1


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
