# External Chrome CDP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically launch stock Chrome with a dedicated profile and attach Playwright over CDP while preserving the existing BrowserService launch modes.

**Architecture:** A focused `ChromeCdpLauncher` owns the external process and readiness probe. `BrowserService` optionally consumes that launcher, attaches through `connect_over_cdp`, reuses the default context, and closes every owned resource. The live runner opts into this path and production Naver interactions use native Locator clicks.

**Tech Stack:** Python 3.13, asyncio, Playwright Python 1.61+, unittest

## Global Constraints

- Do not modify `navigator.webdriver` in page JavaScript.
- Do not add stealth plugins, CAPTCHA solving, proxy rotation, or fingerprint spoofing.
- Use a dedicated non-default Chrome profile.
- Preserve existing BrowserService launch behavior when no CDP launcher is supplied.

---

### Task 1: External Chrome launcher

**Files:**
- Create: `src/datespot_agent/browser/chrome_cdp.py`
- Test: `tests/test_chrome_cdp.py`

**Interfaces:**
- Produces: `ChromeCdpLauncher(executable_path, user_data_dir, startup_timeout=10.0)`
- Produces: `await ChromeCdpLauncher.launch() -> ChromeCdpProcess`
- Produces: `ChromeCdpProcess.endpoint_url: str` and `await ChromeCdpProcess.close()`

- [ ] **Step 1: Write failing launcher tests**

Test command construction, readiness success, early exit, timeout, and graceful/forced cleanup with injected process and readiness helpers.

- [ ] **Step 2: Verify RED**

Run: `uv run python -m unittest tests.test_chrome_cdp -v`

Expected: FAIL because `datespot_agent.browser.chrome_cdp` does not exist.

- [ ] **Step 3: Implement minimal launcher**

Use a loopback non-zero port, `asyncio.create_subprocess_exec`, a bounded `/json/version` probe, and deterministic terminate/kill cleanup.

- [ ] **Step 4: Verify GREEN**

Run: `uv run python -m unittest tests.test_chrome_cdp -v`

Expected: all launcher tests pass.

### Task 2: BrowserService CDP ownership

**Files:**
- Modify: `src/datespot_agent/browser/service.py`
- Modify: `src/datespot_agent/browser/__init__.py`
- Test: `tests/test_browser_service.py`

**Interfaces:**
- Consumes: `ChromeCdpLauncher.launch()` and `ChromeCdpProcess.close()`
- Produces: `BrowserService(..., cdp_launcher: ChromeCdpLauncher | None = None)`

- [ ] **Step 1: Write failing CDP connection and cleanup tests**

Verify endpoint forwarding, `no_defaults=True`, `is_local=True`, default context reuse, and external process cleanup.

- [ ] **Step 2: Verify RED**

Run: `uv run python -m unittest tests.test_browser_service -v`

Expected: FAIL because BrowserService does not accept or use `cdp_launcher`.

- [ ] **Step 3: Implement minimal CDP path**

Extend launch result/session ownership with the external process. Keep both existing launch paths unchanged.

- [ ] **Step 4: Verify GREEN**

Run: `uv run python -m unittest tests.test_browser_service -v`

Expected: all BrowserService tests pass.

### Task 3: Trusted locator interactions

**Files:**
- Modify: `src/datespot_agent/browser/naver_map.py`
- Test: `tests/test_naver_map_page.py`

**Interfaces:**
- Produces: station and candidate interactions through `Locator.click()` only

- [ ] **Step 1: Change tests to require Locator click**

Assert click is called once and JavaScript evaluate/element handles are not used.

- [ ] **Step 2: Verify RED**

Run: `uv run python -m unittest tests.test_naver_map_page -v`

Expected: FAIL because current code calls `element.click()`.

- [ ] **Step 3: Remove `_dom_click` and call Locator click**

Use the existing action pacing and timeout without adding fixed sleeps.

- [ ] **Step 4: Verify GREEN**

Run: `uv run python -m unittest tests.test_naver_map_page -v`

Expected: all NaverMapPage tests pass.

### Task 4: Live runner integration and verification

**Files:**
- Modify: `tests/run_graph_live.py`
- Test: `tests/test_run_graph_live.py`

**Interfaces:**
- Consumes: `ChromeCdpLauncher`
- Produces: live runner configured for stock Chrome CDP and the existing dedicated profile

- [ ] **Step 1: Write failing live configuration test**

Require the macOS Chrome executable and dedicated profile to be carried by the configured CDP launcher.

- [ ] **Step 2: Verify RED**

Run: `uv run python -m unittest tests.test_run_graph_live -v`

Expected: FAIL because the live runner still uses `launch_persistent_context`.

- [ ] **Step 3: Configure the CDP launcher**

Construct `ChromeCdpLauncher` in `build_browser_service` and retain headed operation.

- [ ] **Step 4: Run focused and full verification**

Run: `uv run python -m unittest tests.test_chrome_cdp tests.test_browser_service tests.test_naver_map_page tests.test_run_graph_live -v`

Run: `uv run python -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 5: Run local stock Chrome signal smoke test**

Launch stock Chrome with a temporary profile through `ChromeCdpLauncher`, attach with Playwright, navigate to a local/data signal page, and assert `navigator.webdriver is False`. Do not navigate to Naver during this check.

