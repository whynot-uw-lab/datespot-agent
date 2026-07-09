# Naver Map Playwright PoC Routing Notes

Date: 2026-07-09

## Goal

- Search `신사역`.
- Move map/search context to the station.
- Select `카이센동 우니도 본점`.
- Extract category, internal photos, and latest visitor reviews.

## Confirmed Flow

1. Open Naver Map.
   - URL: `https://map.naver.com/`
   - Loaded route example: `https://map.naver.com/p?c=15.00,0,0,0,dh`

2. Search `신사역`.
   - Input: main page combobox for Naver Map search.
   - Result URL:
     `https://map.naver.com/p/search/%EC%8B%A0%EC%82%AC%EC%97%AD?c=15.00,0,0,0,dh`
   - Note: role-based Playwright click on the combobox timed out once. DOM node click worked.

3. Select station result.
   - Search result list is inside `#searchIframe`.
   - First station result: `신사역 신분당선`.
   - Result URL:
     `https://map.naver.com/p/search/%EC%8B%A0%EC%82%AC%EC%97%AD/subway-station/1907?c=15.00,0,0,0,dh`
   - Note: frame role click was intercepted by a child span. DOM node click worked.

4. Select place result.
   - Target: `카이센동 우니도 본점`.
   - Place ID: `1720070048`.
   - Map shell URL:
     `https://map.naver.com/p/search/%EC%8B%A0%EC%82%AC%EC%97%AD/place/1720070048?...`
   - Entry iframe ID: `#entryIframe`.
   - Entry iframe source:
     `https://pcmap.place.naver.com/place/1720070048?...`

## Place Data

- Name: `카이센동 우니도 본점`
- Category: `일식당`
- Rating: `4.87`
- Review count observed: `5,970`
- Address: `서울 강남구 압구정로2길 15 1층 우니도`
- Phone: `0507-1401-0517`

## Useful Direct Routes

The map shell is useful for user-visible navigation, but direct `pcmap` routes are more stable for extraction.

- Home:
  `https://pcmap.place.naver.com/restaurant/1720070048/home?...`
- Internal photos:
  `https://pcmap.place.naver.com/restaurant/1720070048/photo?...&filterType=AI%20View&subFilter=INTERIOR`
- Visitor reviews:
  `https://pcmap.place.naver.com/restaurant/1720070048/review/visitor?...`
- Latest visitor reviews:
  `https://pcmap.place.naver.com/restaurant/1720070048/review/visitor?...&reviewSort=recent`

## Extraction Notes

- Search/list data:
  - Use `#searchIframe`.
  - The list exposes result names, categories, distance, address, and action buttons.

- Place detail data:
  - Use `#entryIframe` from the map shell.
  - For extraction-heavy steps, open the corresponding `pcmap.place.naver.com/restaurant/{placeId}/...` URL directly.

- Internal photos:
  - Click `사진`.
  - Click `내부`, or route directly with `filterType=AI View&subFilter=INTERIOR`.
  - Use page asset inventory and filter image URLs containing `search.pstatic.net/common`, `ldb-phinf`, or `blogfiles.pstatic.net`.
  - Exclude map tiles and icons.

- Latest reviews:
  - Direct route to visitor reviews.
  - Select `최신순`.
  - Confirm URL includes `reviewSort=recent`.
  - Click first `펼쳐서 더보기` to load 20 reviews.

## Issues

- Naver Map uses multiple dynamic iframes.
- Role/text clicks often hit pointer interception from child spans or sticky headers.
- DOM node click is more reliable for visible navigation.
- Direct `pcmap` route is more reliable for extraction than the embedded map shell.
- Photo asset lists include unrelated map tiles and ad/icon assets, so URL filtering is required.

