from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
