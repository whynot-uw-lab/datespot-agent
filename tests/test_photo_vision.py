from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "poc" / "1-3-photo-vision" / "analyze_photos.py"


def load_module():
    spec = importlib.util.spec_from_file_location("photo_vision_analyze", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PhotoVisionTests(unittest.TestCase):
    def test_load_photo_input_selects_place_and_limits_photos(self):
        module = load_module()
        sample = {
            "places": [
                {
                    "name": "카이센동 우니도 본점",
                    "placeId": "1720070048",
                    "category": "일식당",
                    "photoUrls": ["https://example.com/1.jpg", "https://example.com/2.jpg"],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            loaded = module.load_photo_input(path, place_index=0, max_photos=1)

        self.assertEqual(loaded["place"]["name"], "카이센동 우니도 본점")
        self.assertEqual(loaded["photoUrls"], ["https://example.com/1.jpg"])

    def test_build_message_content_uses_url_image_blocks(self):
        module = load_module()

        content = module.build_message_content(
            place_name="치보 신사점",
            photo_urls=["https://example.com/a.jpg", "https://example.com/b.jpg"],
            criteria="어둡고 차분한 분위기",
        )

        self.assertEqual(content[0]["type"], "input_text")
        self.assertIn("치보 신사점", content[0]["text"])
        self.assertIn("어둡고 차분한 분위기", content[0]["text"])
        self.assertEqual(
            content[1],
            {"type": "input_image", "image_url": "https://example.com/a.jpg", "detail": "low"},
        )
        self.assertEqual(content[2]["image_url"], "https://example.com/b.jpg")
        self.assertEqual(content[2]["detail"], "low")

    def test_parse_json_response_accepts_fenced_json(self):
        module = load_module()
        text = """```json
        {
          "photoScore": 8.2,
          "summary": "차분한 조명과 좌석 간격이 보임",
          "positiveSignals": ["조명"],
          "negativeSignals": ["테이블 간격 일부 확인 어려움"],
          "confidence": "medium"
        }
        ```"""

        parsed = module.parse_json_response(text)

        self.assertEqual(parsed["photoScore"], 8.2)
        self.assertEqual(parsed["confidence"], "medium")

    def test_validate_analysis_rejects_score_outside_range(self):
        module = load_module()

        with self.assertRaises(ValueError):
            module.validate_analysis({"photoScore": 12, "summary": "x"})

    def test_extract_text_from_openai_response_output_text(self):
        module = load_module()

        class Response:
            output_text = "  {\"photoScore\": 7.5}  "

        self.assertEqual(module.extract_text_from_response(Response()), '{"photoScore": 7.5}')

    def test_create_client_passes_explicit_openai_api_key(self):
        module = load_module()

        with patch.object(module, "OpenAI") as openai_cls:
            module.create_client("secret-key")

        openai_cls.assert_called_once_with(api_key="secret-key")

    def test_call_openai_vision_uses_responses_api(self):
        module = load_module()
        captured = {}

        class Responses:
            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    output_text=json.dumps(
                        {
                            "photoScore": 7.5,
                            "summary": "차분한 분위기",
                            "positiveSignals": ["조명"],
                            "negativeSignals": ["좌석 간격 일부 확인 어려움"],
                            "confidence": "medium",
                        },
                        ensure_ascii=False,
                    )
                )

        fake_client = SimpleNamespace(responses=Responses())

        with patch.object(module, "create_client", return_value=fake_client):
            analysis, raw_text = module.call_openai_vision(
                api_key="secret-key",
                model="gpt-5.4-nano",
                place_name="치보 신사점",
                photo_urls=["https://example.com/a.jpg"],
                criteria="차분한 분위기",
                max_tokens=800,
            )

        self.assertEqual(analysis["photoScore"], 7.5)
        self.assertIn("차분한 분위기", raw_text)
        self.assertEqual(captured["model"], "gpt-5.4-nano")
        self.assertEqual(captured["max_output_tokens"], 800)
        self.assertIn("반드시 JSON만 출력", captured["instructions"])
        self.assertEqual(captured["input"][0]["content"][1]["type"], "input_image")


if __name__ == "__main__":
    unittest.main()
