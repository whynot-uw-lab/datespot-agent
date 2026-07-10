# Review LLM PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 1-4 리뷰 LLM 분석 PoC를 `gpt-5.4-nano` 기반 실행 스크립트와 결과 JSON으로 검증한다.

**Architecture:** `poc/1-4-review-llm/analyze_reviews.py`가 1-2 결과 JSON에서 장소와 리뷰를 읽고 OpenAI Responses API에 전달한다. 응답은 JSON으로 파싱/검증하고 `output/review_llm_result.json`에 저장한다. 테스트는 1-3과 같은 동적 모듈 로딩 패턴을 사용한다.

**Tech Stack:** Python, OpenAI SDK 2.45.0, uv, unittest, JSON artifacts.

## Global Constraints

- 입력은 `poc/1-2-naver-map-flow/output/naver_map_flow_result.json`를 기본값으로 사용한다.
- 기본 분석 대상은 첫 번째 장소(`place_index=0`)다.
- 기본 리뷰 수는 최대 20개다.
- 기본 모델은 `gpt-5.4-nano`다.
- 1-4는 리뷰 분석만 포함하고 사진 점수와 최종 가중합은 이후 단계에서 처리한다.
- 성공 기준은 exit code `0`, `ok=true`, `analysis.reviewScore` 0~10 범위, 필수 필드 존재다.

---

### Task 1: Add Review Input And Parsing Helpers

**Files:**
- Create: `tests/test_review_llm.py`
- Create: `poc/1-4-review-llm/analyze_reviews.py`

**Interfaces:**
- Produces: `load_review_input(path: Path, place_index: int, max_reviews: int) -> dict[str, Any]`
- Produces: `build_prompt(place_name: str, review_count: int, criteria: str) -> str`
- Produces: `build_message_content(place_name: str, reviews: list[str], criteria: str) -> list[dict[str, Any]]`
- Produces: `parse_json_response(text: str) -> dict[str, Any]`
- Produces: `validate_analysis(analysis: dict[str, Any]) -> dict[str, Any]`

- [ ] **Step 1: Write failing helper tests**

Add `tests/test_review_llm.py` with tests for input loading, message content, fenced JSON parsing, and score validation:

```python
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "poc" / "1-4-review-llm" / "analyze_reviews.py"


def load_module():
    spec = importlib.util.spec_from_file_location("review_llm_analyze", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReviewLlmTests(unittest.TestCase):
    def test_load_review_input_selects_place_and_limits_reviews(self):
        module = load_module()
        sample = {
            "places": [
                {
                    "name": "카이센동 우니도 본점",
                    "placeId": "1720070048",
                    "category": "일식당",
                    "address": "서울 강남구 압구정로2길 15",
                    "reviews": ["리뷰1", "리뷰2"],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
            loaded = module.load_review_input(path, place_index=0, max_reviews=1)

        self.assertEqual(loaded["place"]["name"], "카이센동 우니도 본점")
        self.assertEqual(loaded["reviews"], ["리뷰1"])
        self.assertEqual(loaded["reviewCount"], 1)

    def test_build_message_content_uses_input_text(self):
        module = load_module()
        content = module.build_message_content(
            place_name="치보 신사점",
            reviews=["데이트 친구 분위기 좋아요", "음식이 맛있어요"],
            criteria="조용함, 대화하기 좋음",
        )

        self.assertEqual(content[0]["type"], "input_text")
        self.assertIn("치보 신사점", content[0]["text"])
        self.assertIn("1. 데이트 친구 분위기 좋아요", content[0]["text"])
        self.assertIn("조용함, 대화하기 좋음", content[0]["text"])

    def test_parse_json_response_accepts_fenced_json(self):
        module = load_module()
        text = """```json
        {
          "reviewScore": 7.1,
          "summary": "데이트 언급과 친절 평가가 있음",
          "positiveSignals": ["친절"],
          "negativeSignals": ["대기 가능성"],
          "dateFitSignals": ["데이트 방문"],
          "concerns": ["혼잡도 확인 필요"],
          "confidence": "medium"
        }
        ```"""

        parsed = module.parse_json_response(text)

        self.assertEqual(parsed["reviewScore"], 7.1)
        self.assertEqual(parsed["confidence"], "medium")

    def test_validate_analysis_rejects_score_outside_range(self):
        module = load_module()

        with self.assertRaises(ValueError):
            module.validate_analysis({"reviewScore": 12, "summary": "x"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run python -m unittest tests/test_review_llm.py
```

Expected: failure because `poc/1-4-review-llm/analyze_reviews.py` does not exist.

- [ ] **Step 3: Implement helpers**

Create `poc/1-4-review-llm/analyze_reviews.py` with constants, input loading, prompt/content building, JSON parsing, and validation:

```python
"""1-4 리뷰 LLM 분석 PoC."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

from datespot_agent.config import get_settings

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "poc" / "1-2-naver-map-flow" / "output" / "naver_map_flow_result.json"
OUTPUT_DIR = Path(__file__).parent / "output"
DEFAULT_OUTPUT = OUTPUT_DIR / "review_llm_result.json"
DEFAULT_MAX_REVIEWS = 20
DEFAULT_MAX_TOKENS = 1200
DEFAULT_CRITERIA = "조용함, 대화하기 좋음, 친절함, 깔끔함, 대기/혼잡 리스크, 데이트 언급"

SYSTEM_PROMPT = (
    "너는 소개팅 장소를 방문자 리뷰로 평가하는 분석가다. "
    "리뷰에서 조용함, 대화 적합성, 친절함, 대기/혼잡, 데이트 적합성을 평가한다. "
    "확실한 리뷰 근거와 추정은 구분하고, 반드시 JSON만 출력한다."
)


def load_review_input(path: Path, place_index: int, max_reviews: int) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    places = data.get("places") or []
    if place_index < 0 or place_index >= len(places):
        raise ValueError(f"place_index out of range: {place_index} / {len(places)}")

    place = places[place_index]
    reviews = [review for review in (place.get("reviews") or []) if review]
    if not reviews:
        raise ValueError(f"reviews empty for place_index={place_index}")

    selected = reviews[:max_reviews]
    return {
        "sourcePath": str(path),
        "place": {
            "name": place.get("name", ""),
            "placeId": place.get("placeId", ""),
            "category": place.get("category", ""),
            "address": place.get("address", ""),
        },
        "reviews": selected,
        "reviewCount": len(selected),
    }


def build_prompt(place_name: str, review_count: int, criteria: str) -> str:
    return f"""
장소명: {place_name}
분석 리뷰 수: {review_count}
리뷰 평가 기준: {criteria}

아래 JSON schema에 맞춰 JSON object 하나만 출력해.
마크다운, 설명 문장, 코드펜스는 출력하지 마.

{{
  "reviewScore": 0.0,
  "summary": "리뷰 기반 한 문단 요약",
  "positiveSignals": ["소개팅에 유리한 리뷰 단서"],
  "negativeSignals": ["소개팅에 불리하거나 확인 어려운 리뷰 단서"],
  "dateFitSignals": ["데이트/소개팅 적합성 단서"],
  "concerns": ["대기, 혼잡, 소음, 가격, 서비스 등 우려"],
  "confidence": "low|medium|high"
}}

점수 기준:
- 0~3: 리뷰상 소개팅 장소로 부적합해 보임
- 4~6: 맛/친절 장점은 있으나 대화/분위기 근거가 약함
- 7~8: 데이트, 분위기, 친절, 안정적 입장 단서가 분명함
- 9~10: 조용함/분위기/서비스/데이트 적합 근거가 강하고 부정 단서가 적음
""".strip()


def build_message_content(place_name: str, reviews: list[str], criteria: str) -> list[dict[str, Any]]:
    numbered_reviews = "\n".join(f"{index}. {review}" for index, review in enumerate(reviews, start=1))
    text = f"{build_prompt(place_name, len(reviews), criteria)}\n\n리뷰 목록:\n{numbered_reviews}"
    return [{"type": "input_text", "text": text}]


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]

    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("model response is not a JSON object")
    return parsed


def validate_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    if "reviewScore" not in analysis:
        raise ValueError("reviewScore missing")
    score = float(analysis["reviewScore"])
    if score < 0 or score > 10:
        raise ValueError(f"reviewScore out of range: {score}")
    analysis["reviewScore"] = round(score, 1)

    for key in ("summary", "positiveSignals", "negativeSignals", "dateFitSignals", "concerns", "confidence"):
        if key not in analysis:
            raise ValueError(f"{key} missing")
    if analysis["confidence"] not in {"low", "medium", "high"}:
        raise ValueError(f"invalid confidence: {analysis['confidence']}")
    return analysis
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
uv run python -m unittest tests/test_review_llm.py
```

Expected: all tests pass.

### Task 2: Add OpenAI Execution And CLI

**Files:**
- Modify: `poc/1-4-review-llm/analyze_reviews.py`
- Modify: `tests/test_review_llm.py`

**Interfaces:**
- Consumes: helpers from Task 1
- Produces: `create_client(api_key: str) -> OpenAI`
- Produces: `extract_text_from_response(response: Any) -> str`
- Produces: `call_openai_reviews(...) -> tuple[dict[str, Any], str]`
- Produces: `run_analysis(args: argparse.Namespace) -> dict[str, Any]`
- Produces: `main() -> int`

- [ ] **Step 1: Add failing API/CLI tests**

Extend `tests/test_review_llm.py` with OpenAI client, response extraction, and call shape tests:

```python
from types import SimpleNamespace
from unittest.mock import patch


    def test_extract_text_from_openai_response_output_text(self):
        module = load_module()

        class Response:
            output_text = "  {\"reviewScore\": 7.5}  "

        self.assertEqual(module.extract_text_from_response(Response()), '{"reviewScore": 7.5}')

    def test_create_client_passes_explicit_openai_api_key(self):
        module = load_module()

        with patch.object(module, "OpenAI") as openai_cls:
            module.create_client("secret-key")

        openai_cls.assert_called_once_with(api_key="secret-key")

    def test_call_openai_reviews_uses_responses_api(self):
        module = load_module()
        captured = {}

        class Responses:
            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    output_text=json.dumps(
                        {
                            "reviewScore": 7.5,
                            "summary": "데이트 언급과 친절 평가가 있음",
                            "positiveSignals": ["친절"],
                            "negativeSignals": ["대기 가능성"],
                            "dateFitSignals": ["데이트 방문"],
                            "concerns": ["혼잡도 확인 필요"],
                            "confidence": "medium",
                        },
                        ensure_ascii=False,
                    )
                )

        fake_client = SimpleNamespace(responses=Responses())

        with patch.object(module, "create_client", return_value=fake_client):
            analysis, raw_text = module.call_openai_reviews(
                api_key="secret-key",
                model="gpt-5.4-nano",
                place_name="치보 신사점",
                reviews=["데이트 친구 분위기 좋아요"],
                criteria="조용함",
                max_tokens=800,
            )

        self.assertEqual(analysis["reviewScore"], 7.5)
        self.assertIn("데이트 언급", raw_text)
        self.assertEqual(captured["model"], "gpt-5.4-nano")
        self.assertEqual(captured["max_output_tokens"], 800)
        self.assertIn("반드시 JSON만 출력", captured["instructions"])
        self.assertEqual(captured["input"][0]["content"][0]["type"], "input_text")
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run python -m unittest tests/test_review_llm.py
```

Expected: failures because API/CLI functions are not implemented.

- [ ] **Step 3: Implement API execution and CLI**

Append the following functions to `poc/1-4-review-llm/analyze_reviews.py`:

```python
def extract_text_from_response(response: Any) -> str:
    output_text = getattr(response, "output_text", "")
    if output_text:
        return output_text.strip()

    parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "\n".join(parts).strip()


def create_client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key)


def call_openai_reviews(
    *,
    api_key: str,
    model: str,
    place_name: str,
    reviews: list[str],
    criteria: str,
    max_tokens: int,
) -> tuple[dict[str, Any], str]:
    client = create_client(api_key)
    response = client.responses.create(
        model=model,
        instructions=SYSTEM_PROMPT,
        max_output_tokens=max_tokens,
        input=[
            {
                "role": "user",
                "content": build_message_content(place_name, reviews, criteria),
            }
        ],
    )
    raw_text = extract_text_from_response(response)
    analysis = validate_analysis(parse_json_response(raw_text))
    return analysis, raw_text


def save_result(result: dict[str, Any], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def run_analysis(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    model = args.model or settings.model
    result: dict[str, Any] = {
        "ranAt": datetime.now(timezone.utc).isoformat(),
        "ok": False,
        "model": model,
        "criteria": args.criteria,
        "inputPath": str(args.input),
        "place": {},
        "reviewCount": 0,
        "reviews": [],
        "analysis": None,
        "rawText": "",
        "errors": [],
    }

    try:
        loaded = load_review_input(args.input, args.place_index, args.max_reviews)
        result["place"] = loaded["place"]
        result["reviews"] = loaded["reviews"]
        result["reviewCount"] = loaded["reviewCount"]
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"input: {type(e).__name__}: {e}")
        return result

    if args.dry_run:
        result["ok"] = True
        result["analysis"] = {
            "reviewScore": 0.0,
            "summary": "dry-run: API 호출 없이 입력 구성만 검증함",
            "positiveSignals": [],
            "negativeSignals": [],
            "dateFitSignals": [],
            "concerns": [],
            "confidence": "low",
        }
        return result

    if not settings.openai_api_key:
        result["errors"].append("OPENAI_API_KEY is empty")
        return result

    try:
        analysis, raw_text = call_openai_reviews(
            api_key=settings.openai_api_key,
            model=model,
            place_name=result["place"]["name"],
            reviews=result["reviews"],
            criteria=args.criteria,
            max_tokens=args.max_tokens,
        )
        result["analysis"] = analysis
        result["rawText"] = raw_text
        result["ok"] = True
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"openai: {type(e).__name__}: {e}")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="1-4 리뷰 LLM 분석 PoC")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--place-index", type=int, default=0)
    parser.add_argument("--max-reviews", type=int, default=DEFAULT_MAX_REVIEWS)
    parser.add_argument("--model", default="")
    parser.add_argument("--criteria", default=DEFAULT_CRITERIA)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_analysis(args)
    out = save_result(result, args.output)

    status = "성공" if result["ok"] else "실패"
    print(f"=== 1-4 리뷰 LLM 분석 PoC {status} ===")
    print(f"결과: {out}")
    print(f"모델: {result['model']}")
    if result["place"]:
        print(f"장소: {result['place'].get('name')} ({result['place'].get('placeId')})")
    print(f"리뷰 수: {result['reviewCount']}")
    if result.get("analysis"):
        print(f"리뷰 점수: {result['analysis'].get('reviewScore')}")
        print(f"신뢰도: {result['analysis'].get('confidence')}")
    if result["errors"]:
        print("오류:")
        for error in result["errors"]:
            print(f"- {error}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
uv run python -m unittest tests/test_review_llm.py
```

Expected: all review tests pass.

### Task 3: Add Docs, Roadmap, And Real Run

**Files:**
- Create: `poc/1-4-review-llm/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `poc/1-4-review-llm/analyze_reviews.py`
- Produces: documented command and roadmap status

- [ ] **Step 1: Add README for 1-4**

Create `poc/1-4-review-llm/README.md`:

```markdown
# 1-4 리뷰 LLM 분석 PoC

1단계(기술 리스크 검증)의 네 번째 세부 단계. 네이버지도에서 추출한 방문자 리뷰를 OpenAI 저가 모델에 넣어 소개팅 장소 리뷰 점수와 근거가 쓸만한지 확인한다.

## 목표 흐름

```text
1-2 결과 JSON 로드
  → 첫 번째 장소의 리뷰 최대 20개 선택
  → OpenAI Responses API에 리뷰 텍스트 전달
  → 리뷰 점수 / 요약 / 긍정·부정·데이트 적합 단서 JSON 파싱
  → output/review_llm_result.json 저장
```

## 실행

```bash
env -u OPENAI_API_KEY uv run python poc/1-4-review-llm/analyze_reviews.py
```

입력 구성만 확인:

```bash
uv run python poc/1-4-review-llm/analyze_reviews.py --dry-run
```

## 입력

- 기본 입력: `poc/1-2-naver-map-flow/output/naver_map_flow_result.json`
- 기본 대상: 첫 번째 장소
- 기본 리뷰 수: 최대 20개
- 기본 모델: `gpt-5.4-nano`

## 완료 기준

- `uv run python poc/1-4-review-llm/analyze_reviews.py` exit code `0`
- `output/review_llm_result.json`의 `ok=true`
- `analysis.reviewScore`가 0~10 범위
- `summary`, `positiveSignals`, `negativeSignals`, `dateFitSignals`, `concerns`, `confidence` 포함
```

- [ ] **Step 2: Run dry-run**

Run:

```bash
uv run python poc/1-4-review-llm/analyze_reviews.py --dry-run --output /tmp/review_llm_dry_run.json
```

Expected: exit code `0`, model `gpt-5.4-nano`, review count `20`.

- [ ] **Step 3: Run real API call**

Run:

```bash
env -u OPENAI_API_KEY uv run python poc/1-4-review-llm/analyze_reviews.py
```

Expected: exit code `0`, `ok=true`, review score printed.

- [ ] **Step 4: Update root roadmap after successful run**

Change `README.md` 1-4 checkbox from `[ ]` to `[x]` and record `reviewScore` and `confidence` from the successful output.

- [ ] **Step 5: Run final verification**

Run:

```bash
uv run python -m unittest discover -s tests
uv run python - <<'PY'
import json
from pathlib import Path
p = Path("poc/1-4-review-llm/output/review_llm_result.json")
data = json.loads(p.read_text())
analysis = data.get("analysis") or {}
print("ok", data.get("ok"))
print("model", data.get("model"))
print("score", analysis.get("reviewScore"))
print("confidence", analysis.get("confidence"))
print("errors", len(data.get("errors") or []))
PY
```

Expected: all tests pass, `ok True`, `errors 0`.
