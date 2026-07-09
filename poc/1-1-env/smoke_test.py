"""1-1 환경 검증 스모크 테스트.

실행: uv run python poc/1-1-env/smoke_test.py

확인 항목:
  1. 핵심 패키지 import (playwright, anthropic, langgraph, pydantic)
  2. 설정 모델 로드 (Settings, SearchConfig)
  3. Playwright Chromium 헤드리스 실행 + 간단한 페이지 로드

결과는 poc/1-1-env/output/smoke_result.json 에 저장된다.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "output"


def check_imports() -> str:
    import anthropic  # noqa: F401
    import langgraph  # noqa: F401
    import playwright  # noqa: F401
    import pydantic  # noqa: F401

    return "핵심 패키지 import"


def check_config() -> str:
    from datespot_agent.config import SearchConfig, get_settings

    settings = get_settings()
    cfg = SearchConfig(location="강남역")
    assert cfg.max_places == 30
    assert abs(cfg.weights.photo + cfg.weights.review - 1.0) < 1e-6
    return (
        f"설정 로드 (model={settings.model}, headless={settings.headless}, "
        f"max_places={cfg.max_places}, weights={cfg.weights.photo}/{cfg.weights.review})"
    )


def check_playwright() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("about:blank")
        page.set_content("<h1 id='t'>datespot</h1>")
        text = page.text_content("#t")
        browser.close()
    assert text == "datespot"
    return "Playwright Chromium 헤드리스 실행"


CHECKS = [
    ("imports", check_imports),
    ("config", check_config),
    ("playwright", check_playwright),
]


def save_result(results: list[dict], passed: bool) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / "smoke_result.json"
    out.write_text(
        json.dumps(
            {
                "ran_at": datetime.now(timezone.utc).isoformat(),
                "passed": passed,
                "checks": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


def main() -> int:
    print("=== 1-1 환경 스모크 테스트 ===")
    results: list[dict] = []
    passed = True
    for name, fn in CHECKS:
        try:
            detail = fn()
            print(f"  [OK] {detail}")
            results.append({"name": name, "ok": True, "detail": detail})
        except Exception as e:  # noqa: BLE001
            passed = False
            msg = f"{type(e).__name__}: {e}"
            print(f"  [FAIL] {name}: {msg}")
            results.append({"name": name, "ok": False, "detail": msg})
            break

    out = save_result(results, passed)
    print(f"=== {'전체 통과 ✅' if passed else '실패 ❌'} (결과: {out}) ===")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
