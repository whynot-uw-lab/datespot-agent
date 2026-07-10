"""1-4 리뷰 LLM 분석 PoC.

실행: uv run python poc/1-4-review-llm/analyze_reviews.py

1-2 네이버지도 PoC 결과의 방문자 리뷰를 OpenAI 저가 모델에 전달하고,
소개팅 장소 적합도 관점의 리뷰 점수와 근거를 JSON으로 저장한다.
"""

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
