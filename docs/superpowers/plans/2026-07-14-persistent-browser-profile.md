# Persistent Browser Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live graph runner reuse a dedicated Playwright Chrome profile while preserving the existing isolated browser mode for all other callers.

**Architecture:** `BrowserService` receives an optional `Path` for a user data directory and selects either the existing `launch()`/`new_context()` flow or `launch_persistent_context()`. The live runner creates the service through a small factory that always supplies `~/.cache/datespot-agent/chrome-profile`; unit tests exercise both launch paths without opening a real browser.

**Tech Stack:** Python 3.13, Playwright async API, `unittest`, `uv`

## Global Constraints

- Do not add browser fingerprint masking, User-Agent spoofing, CAPTCHA solving, or proxy rotation.
- Keep `BrowserService(user_data_dir=None)` behavior unchanged.
- Keep locale `ko-KR`, timezone `Asia/Seoul`, and viewport `1440x1000` in both launch modes.
- Use a separate automation profile; never use Chrome's default user profile.
- Reuse an existing persistent-context page before creating a new page.

---

### Task 1: BrowserService persistent context selection

**Files:**
- Modify: `tests/test_browser_service.py`
- Modify: `src/datespot_agent/browser/service.py`
- Modify: `docs/superpowers/specs/2026-07-14-persistent-browser-profile-design.md`

**Interfaces:**
- Consumes: `BrowserService(headless, browser_channel, pacer, log)` and Playwright `runtime.chromium`
- Produces: `BrowserService(..., user_data_dir: Path | None = None)`, `_launch_browser_context(runtime)`, and `_initial_page(context)`

- [ ] **Step 1: Correct the startup-error statement in the design**

Replace the startup-error bullet with:

```markdown
- 프로필 잠금이나 Chrome 시작 실패 시 기존 `start_session()` 정리 흐름으로 열린 자원을
  닫은 뒤 원본 오류를 전파한다.
```

- [ ] **Step 2: Write failing launch-path tests**

Add `Path`, `TemporaryDirectory`, and focused fakes for a context, browser, Chromium launcher, and runtime. Add these two tests to `BrowserServiceTests`:

```python
async def test_default_launch_uses_isolated_context_and_new_page(self):
    context = FakeLaunchContext()
    browser = FakeLaunchBrowser(context)
    chromium = FakeChromium(browser, FakeLaunchContext())
    service = BrowserService(headless=True)

    launched_browser, launched_context = await service._launch_browser_context(
        FakeRuntime(chromium)
    )
    page = await service._initial_page(launched_context)

    self.assertIs(launched_browser, browser)
    self.assertEqual(chromium.launch_calls, [{"headless": True}])
    self.assertEqual(chromium.persistent_calls, [])
    self.assertEqual(
        context.context_options,
        {
            "locale": "ko-KR",
            "timezone_id": "Asia/Seoul",
            "viewport": {"width": 1440, "height": 1000},
        },
    )
    self.assertIs(page, context.pages[0])
    self.assertEqual(context.new_page_calls, 1)


async def test_persistent_launch_reuses_existing_page_and_profile(self):
    existing_page = object()
    persistent_browser = object()
    context = FakeLaunchContext(
        pages=[existing_page],
        browser=persistent_browser,
    )
    browser = FakeLaunchBrowser(FakeLaunchContext())
    chromium = FakeChromium(browser, context)

    with TemporaryDirectory() as temp_dir:
        profile_dir = Path(temp_dir) / "chrome-profile"
        service = BrowserService(
            headless=False,
            browser_channel="chrome",
            user_data_dir=profile_dir,
        )
        launched_browser, launched_context = (
            await service._launch_browser_context(FakeRuntime(chromium))
        )
        page = await service._initial_page(launched_context)

        self.assertTrue(profile_dir.is_dir())
        self.assertIs(launched_browser, persistent_browser)
        self.assertEqual(chromium.launch_calls, [])
        self.assertEqual(
            chromium.persistent_calls,
            [
                (
                    profile_dir,
                    {
                        "headless": False,
                        "channel": "chrome",
                        "locale": "ko-KR",
                        "timezone_id": "Asia/Seoul",
                        "viewport": {"width": 1440, "height": 1000},
                    },
                )
            ],
        )
        self.assertIs(page, existing_page)
        self.assertEqual(context.new_page_calls, 0)
```

The fakes expose exactly the attributes used by the tests:

```python
class FakeLaunchContext:
    def __init__(self, *, pages=None, browser=None) -> None:
        self.pages = list(pages or [])
        self.browser = browser
        self.new_page_calls = 0
        self.context_options = None

    async def new_page(self):
        self.new_page_calls += 1
        page = object()
        self.pages.append(page)
        return page


class FakeLaunchBrowser:
    def __init__(self, context: FakeLaunchContext) -> None:
        self.context = context

    async def new_context(self, **options):
        self.context.context_options = options
        return self.context


class FakeChromium:
    def __init__(self, browser, persistent_context) -> None:
        self.browser = browser
        self.persistent_context = persistent_context
        self.launch_calls = []
        self.persistent_calls = []

    async def launch(self, **options):
        self.launch_calls.append(options)
        return self.browser

    async def launch_persistent_context(self, user_data_dir, **options):
        self.persistent_calls.append((user_data_dir, options))
        return self.persistent_context


class FakeRuntime:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
uv run python -m unittest \
  tests.test_browser_service.BrowserServiceTests.test_default_launch_uses_isolated_context_and_new_page \
  tests.test_browser_service.BrowserServiceTests.test_persistent_launch_reuses_existing_page_and_profile -v
```

Expected: both tests fail because `user_data_dir`, `_launch_browser_context`, and `_initial_page` do not exist.

- [ ] **Step 4: Implement the minimal BrowserService branch**

Add `user_data_dir: Path | None = None` to the constructor and store it. Add:

```python
async def _launch_browser_context(
    self,
    runtime: Playwright,
) -> tuple[Browser | None, BrowserContext]:
    launch_options: dict[str, object] = {"headless": self._headless}
    if self._browser_channel is not None:
        launch_options["channel"] = self._browser_channel
    context_options: dict[str, object] = {
        "locale": "ko-KR",
        "timezone_id": "Asia/Seoul",
        "viewport": {"width": 1440, "height": 1000},
    }
    if self._user_data_dir is not None:
        self._user_data_dir.mkdir(parents=True, exist_ok=True)
        context = await runtime.chromium.launch_persistent_context(
            self._user_data_dir,
            **launch_options,
            **context_options,
        )
        return context.browser, context
    browser = await runtime.chromium.launch(**launch_options)
    context = await browser.new_context(**context_options)
    return browser, context


@staticmethod
async def _initial_page(context: BrowserContext) -> Page:
    if context.pages:
        return context.pages[0]
    return await context.new_page()
```

Replace the inline launch code in `start_session()` with:

```python
browser, context = await self._launch_browser_context(runtime)
page = await self._initial_page(context)
```

- [ ] **Step 5: Run focused and service tests and verify GREEN**

Run `uv run python -m unittest tests.test_browser_service -v`.

Expected: all `BrowserServiceTests` pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/datespot_agent/browser/service.py tests/test_browser_service.py \
  docs/superpowers/specs/2026-07-14-persistent-browser-profile-design.md
git commit -m "feat: support persistent browser profiles"
```

---

### Task 2: Live runner dedicated profile

**Files:**
- Create: `tests/test_run_graph_live.py`
- Modify: `tests/run_graph_live.py`

**Interfaces:**
- Consumes: `BrowserService(..., user_data_dir: Path | None)` from Task 1
- Produces: `BROWSER_USER_DATA_DIR: Path` and `build_browser_service(default_headless: bool) -> BrowserService`

- [ ] **Step 1: Write the failing live-runner configuration test**

Create `tests/test_run_graph_live.py`:

```python
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tests" / "run_graph_live.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_graph_live", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("graph live module spec 생성 실패")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GraphLiveBrowserConfigTests(unittest.TestCase):
    def test_build_browser_service_uses_dedicated_persistent_profile(self):
        module = load_module()

        service = module.build_browser_service(default_headless=True)

        self.assertFalse(service._headless)
        self.assertEqual(service._browser_channel, "chrome")
        self.assertEqual(
            service._user_data_dir,
            Path.home() / ".cache" / "datespot-agent" / "chrome-profile",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test and verify RED**

Run `uv run python -m unittest tests.test_run_graph_live -v`.

Expected: FAIL because `build_browser_service` does not exist.

- [ ] **Step 3: Configure the dedicated live profile**

Add to `tests/run_graph_live.py`:

```python
BROWSER_USER_DATA_DIR = (
    Path.home() / ".cache" / "datespot-agent" / "chrome-profile"
)


def build_browser_service(default_headless: bool) -> BrowserService:
    return BrowserService(
        headless=False if HEADED else default_headless,
        browser_channel=BROWSER_CHANNEL,
        user_data_dir=BROWSER_USER_DATA_DIR,
        log=log_line,
    )
```

Replace the inline service construction in `run()` with:

```python
browser_service=build_browser_service(settings.headless),
```

- [ ] **Step 4: Run live-runner and browser tests and verify GREEN**

Run `uv run python -m unittest tests.test_run_graph_live tests.test_browser_service -v`.

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add tests/run_graph_live.py tests/test_run_graph_live.py
git commit -m "test: reuse browser profile in live graph run"
```

---

### Task 3: Regression verification

**Files:**
- Verify: `src/datespot_agent/browser/service.py`
- Verify: `tests/run_graph_live.py`
- Verify: all tests

**Interfaces:**
- Consumes: completed Tasks 1 and 2
- Produces: verified persistent-profile implementation

- [ ] **Step 1: Run formatting and diff checks**

Run `git diff --check HEAD~2`.

Expected: no output and exit code 0.

- [ ] **Step 2: Run the complete automated test suite**

Run `uv run python -m unittest discover -s tests -v`.

Expected: all tests pass with zero failures and zero errors.

- [ ] **Step 3: Inspect final repository state**

Run:

```bash
git status --short
git log -4 --oneline
```

Expected: the implementation files are committed and the worktree contains no unexpected changes.

