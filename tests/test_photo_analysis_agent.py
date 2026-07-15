from __future__ import annotations

import unittest
from types import SimpleNamespace

from datespot_agent.analysis import (
    AnalysisInputError,
    AnalysisRequestError,
    AnalysisResponseError,
    PhotoAnalysisAgent,
)
from datespot_agent.models import PhotoAnalysis, PlaceDetail


class FakeResponses:
    def __init__(self, parsed=None, error: Exception | None = None):
        self.parsed = parsed
        self.error = error
        self.kwargs = None

    async def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return SimpleNamespace(output_parsed=self.parsed)


class PhotoAnalysisAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_analyze_uses_criteria_and_at_most_five_photos(self):
        parsed = PhotoAnalysis(photo_score=8, reason="차분한 조명")
        responses = FakeResponses(parsed=parsed)
        client = SimpleNamespace(responses=responses)
        agent = PhotoAnalysisAgent(client, model="gpt-5.4-nano", max_output_tokens=700)
        detail = PlaceDetail(
            place_id="1",
            name="우니도",
            category="일식당",
            address="서울 강남구",
            photo_urls=[f"https://example.com/{index}.jpg" for index in range(7)],
        )

        with self.assertLogs(
            "datespot_agent.analysis.photo",
            level="INFO",
        ) as captured:
            result = await agent.analyze(detail, "어둡고 차분한 분위기")

        self.assertIs(result, parsed)
        self.assertEqual(responses.kwargs["model"], "gpt-5.4-nano")
        self.assertEqual(responses.kwargs["max_output_tokens"], 700)
        self.assertIs(responses.kwargs["text_format"], PhotoAnalysis)
        content = responses.kwargs["input"][0]["content"]
        self.assertIn("어둡고 차분한 분위기", content[0]["text"])
        self.assertNotIn("matched", content[0]["text"])
        self.assertIn("점수에 반영", content[0]["text"])
        self.assertEqual(len(content[1:]), 5)
        self.assertTrue(all(block["type"] == "input_image" for block in content[1:]))
        self.assertEqual(
            [record.datespot_event for record in captured.records],
            [
                "analysis.photo.prepared",
                "analysis.photo.requested",
                "analysis.photo.completed",
            ],
        )
        self.assertEqual(captured.records[0].datespot_fields["input_count"], 5)
        self.assertEqual(captured.records[-1].datespot_fields["score"], 8)
        serialized_logs = " ".join(record.getMessage() for record in captured.records)
        self.assertNotIn("어둡고 차분한 분위기", serialized_logs)
        self.assertNotIn("example.com", serialized_logs)

    async def test_empty_photos_raise_input_error_without_api_call(self):
        responses = FakeResponses()
        agent = PhotoAnalysisAgent(SimpleNamespace(responses=responses), model="model")

        with self.assertRaises(AnalysisInputError):
            await agent.analyze(PlaceDetail(place_id="1", name="우니도"), "차분함")

        self.assertIsNone(responses.kwargs)

    async def test_missing_parsed_output_raises_response_error(self):
        agent = PhotoAnalysisAgent(
            SimpleNamespace(responses=FakeResponses(parsed=None)),
            model="model",
        )
        detail = PlaceDetail(
            place_id="1",
            name="우니도",
            photo_urls=["https://example.com/1.jpg"],
        )

        with self.assertRaises(AnalysisResponseError):
            await agent.analyze(detail, "차분함")

    async def test_request_failure_is_wrapped_with_cause(self):
        original = RuntimeError("network down")
        agent = PhotoAnalysisAgent(
            SimpleNamespace(responses=FakeResponses(error=original)),
            model="model",
        )
        detail = PlaceDetail(
            place_id="1",
            name="우니도",
            photo_urls=["https://example.com/1.jpg"],
        )

        with self.assertLogs(
            "datespot_agent.analysis.photo",
            level="ERROR",
        ) as captured:
            with self.assertRaises(AnalysisRequestError) as caught:
                await agent.analyze(detail, "차분함")

        self.assertIs(caught.exception.__cause__, original)
        self.assertEqual(
            captured.records[-1].datespot_event,
            "analysis.photo.failed",
        )
        self.assertIsNotNone(captured.records[-1].exc_info)


if __name__ == "__main__":
    unittest.main()
