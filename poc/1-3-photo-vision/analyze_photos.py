"""1-3 사진 비전 분석 PoC.

실행: uv run python poc/1-3-photo-vision/analyze_photos.py

1-2 네이버지도 PoC 결과의 내부 사진 URL을 Claude 비전 모델에 전달하고,
소개팅 장소 적합도 관점의 사진 점수와 근거를 JSON으로 저장한다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from datespot_agent.config import get_settings

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "poc" / "1-2-naver-map-flow" / "output" / "naver_map_flow_result.json"
OUTPUT_DIR = Path(__file__).parent / "output"
DEFAULT_OUTPUT = OUTPUT_DIR / "photo_vision_result.json"
DEFAULT_MAX_PHOTOS = 3
DEFAULT_MAX_TOKENS = 1200
DEFAULT_CRITERIA = "어둡고 차분한 분위기, 넓은 좌석 간격, 대화하기 좋은 구조"

SYSTEM_PROMPT = (
    "너는 소개팅 장소를 사진으로 평가하는 공간 분석가다. "
    "실내 사진에서 조명, 좌석 간격, 소음/혼잡 추정, 대화 적합성을 평가한다. "
    "확실히 보이는 것과 추정은 구분하고, 반드시 JSON만 출력한다."
)


def load_photo_input(path: Path, place_index: int, max_photos: int) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    places = data.get("places") or []
    if place_index < 0 or place_index >= len(places):
        raise ValueError(f"place_index out of range: {place_index} / {len(places)}")

    place = places[place_index]
    photo_urls = [url for url in (place.get("photoUrls") or []) if url]
    if not photo_urls:
        raise ValueError(f"photoUrls empty for place_index={place_index}")

    return {
        "sourcePath": str(path),
        "place": {
            "name": place.get("name", ""),
            "placeId": place.get("placeId", ""),
            "category": place.get("category", ""),
            "address": place.get("address", ""),
        },
        "photoUrls": photo_urls[:max_photos],
    }


def build_prompt(place_name: str, criteria: str) -> str:
    return f"""
장소명: {place_name}
사진 평가 기준: {criteria}

아래 JSON schema에 맞춰 JSON object 하나만 출력해.
마크다운, 설명 문장, 코드펜스는 출력하지 마.

{{
  "photoScore": 0.0,
  "summary": "사진 기반 한 문단 요약",
  "positiveSignals": ["소개팅에 유리한 시각 단서"],
  "negativeSignals": ["소개팅에 불리하거나 확인 어려운 단서"],
  "lighting": "조명 평가",
  "seating": "좌석 간격/배치 평가",
  "conversationFit": "대화 적합성 평가",
  "confidence": "low|medium|high"
}}

점수 기준:
- 0~3: 소개팅 장소로 부적합해 보임
- 4~6: 일부 장점은 있으나 불확실하거나 평범함
- 7~8: 소개팅에 적합한 분위기 단서가 분명함
- 9~10: 조명/좌석/공간감이 매우 좋고 근거가 명확함
""".strip()


def build_message_content(place_name: str, photo_urls: list[str], criteria: str) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {"type": "image", "source": {"type": "url", "url": url}}
        for url in photo_urls
    ]
    content.append({"type": "text", "text": build_prompt(place_name, criteria)})
    return content


def extract_text_from_response(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []):
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "\n".join(parts).strip()


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
    if "photoScore" not in analysis:
        raise ValueError("photoScore missing")
    score = float(analysis["photoScore"])
    if score < 0 or score > 10:
        raise ValueError(f"photoScore out of range: {score}")
    analysis["photoScore"] = round(score, 1)

    for key in ("summary", "positiveSignals", "negativeSignals", "confidence"):
        if key not in analysis:
            raise ValueError(f"{key} missing")
    if analysis["confidence"] not in {"low", "medium", "high"}:
        raise ValueError(f"invalid confidence: {analysis['confidence']}")
    return analysis


def create_client(api_key: str) -> Anthropic:
    return Anthropic(api_key=api_key)


def call_claude_vision(
    *,
    api_key: str,
    model: str,
    place_name: str,
    photo_urls: list[str],
    criteria: str,
    max_tokens: int,
) -> tuple[dict[str, Any], str]:
    client = create_client(api_key)
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": build_message_content(place_name, photo_urls, criteria),
            }
        ],
    )
    raw_text = extract_text_from_response(message)
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
        "photoUrls": [],
        "analysis": None,
        "rawText": "",
        "errors": [],
    }

    try:
        loaded = load_photo_input(args.input, args.place_index, args.max_photos)
        result["place"] = loaded["place"]
        result["photoUrls"] = loaded["photoUrls"]
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"input: {type(e).__name__}: {e}")
        return result

    if args.dry_run:
        result["ok"] = True
        result["analysis"] = {
            "photoScore": 0.0,
            "summary": "dry-run: API 호출 없이 입력 구성만 검증함",
            "positiveSignals": [],
            "negativeSignals": [],
            "lighting": "",
            "seating": "",
            "conversationFit": "",
            "confidence": "low",
        }
        return result

    if not settings.anthropic_api_key:
        result["errors"].append("ANTHROPIC_API_KEY is empty")
        return result

    try:
        analysis, raw_text = call_claude_vision(
            api_key=settings.anthropic_api_key,
            model=model,
            place_name=result["place"]["name"],
            photo_urls=result["photoUrls"],
            criteria=args.criteria,
            max_tokens=args.max_tokens,
        )
        result["analysis"] = analysis
        result["rawText"] = raw_text
        result["ok"] = True
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"anthropic: {type(e).__name__}: {e}")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="1-3 사진 비전 분석 PoC")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--place-index", type=int, default=0)
    parser.add_argument("--max-photos", type=int, default=DEFAULT_MAX_PHOTOS)
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
    print(f"=== 1-3 사진 비전 분석 PoC {status} ===")
    print(f"결과: {out}")
    print(f"모델: {result['model']}")
    if result["place"]:
        print(f"장소: {result['place'].get('name')} ({result['place'].get('placeId')})")
    print(f"사진 수: {len(result['photoUrls'])}")
    if result.get("analysis"):
        print(f"사진 점수: {result['analysis'].get('photoScore')}")
        print(f"신뢰도: {result['analysis'].get('confidence')}")
    if result["errors"]:
        print("오류:")
        for error in result["errors"]:
            print(f"- {error}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
