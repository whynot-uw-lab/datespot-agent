from __future__ import annotations

import unittest
from types import SimpleNamespace

from datespot_agent.analysis import (
    AnalysisInputError,
    AnalysisRequestError,
    AnalysisResponseError,
    ReviewAnalysisAgent,
)
from datespot_agent.models import PlaceDetail, ReviewAnalysis


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


class ReviewAnalysisAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_analyze_uses_criteria_and_at_most_fifty_reviews(self):
        parsed = ReviewAnalysis(review_score=8, reason="조용함 언급")
        responses = FakeResponses(parsed=parsed)
        client = SimpleNamespace(responses=responses)
        agent = ReviewAnalysisAgent(client, model="gpt-5.4-nano", max_output_tokens=700)
        detail = PlaceDetail(
            place_id="1",
            name="우니도",
            category="일식당",
            address="서울 강남구",
            reviews=[f"리뷰 {index}" for index in range(55)],
            review_count=128,
        )

        with self.assertLogs(
            "datespot_agent.analysis.review",
            level="INFO",
        ) as captured:
            result = await agent.analyze(detail, "조용하고 대화하기 좋음")

        self.assertIs(result, parsed)
        self.assertEqual(responses.kwargs["model"], "gpt-5.4-nano")
        self.assertEqual(responses.kwargs["max_output_tokens"], 700)
        self.assertIs(responses.kwargs["text_format"], ReviewAnalysis)
        text = responses.kwargs["input"][0]["content"][0]["text"]
        self.assertIn("조용하고 대화하기 좋음", text)
        self.assertNotIn("matched", text)
        self.assertIn("점수에 반영", text)
        self.assertIn("50. 리뷰 49", text)
        self.assertNotIn("리뷰 50", text)
        self.assertEqual(
            [record.datespot_event for record in captured.records],
            [
                "analysis.review.prepared",
                "analysis.review.requested",
                "analysis.review.completed",
            ],
        )
        self.assertEqual(captured.records[0].datespot_fields["input_count"], 50)
        self.assertEqual(captured.records[-1].datespot_fields["score"], 8)
        serialized_logs = " ".join(record.getMessage() for record in captured.records)
        self.assertNotIn("조용하고 대화하기 좋음", serialized_logs)
        self.assertNotIn("리뷰 49", serialized_logs)

    async def test_empty_reviews_raise_input_error_without_api_call(self):
        responses = FakeResponses()
        agent = ReviewAnalysisAgent(SimpleNamespace(responses=responses), model="model")

        with self.assertRaises(AnalysisInputError):
            await agent.analyze(PlaceDetail(place_id="1", name="우니도"), "조용함")

        self.assertIsNone(responses.kwargs)

    async def test_missing_parsed_output_raises_response_error(self):
        agent = ReviewAnalysisAgent(
            SimpleNamespace(responses=FakeResponses(parsed=None)),
            model="model",
        )
        detail = PlaceDetail(place_id="1", name="우니도", reviews=["조용해요"])

        with self.assertRaises(AnalysisResponseError):
            await agent.analyze(detail, "조용함")

    async def test_request_failure_is_wrapped_with_cause(self):
        original = RuntimeError("network down")
        agent = ReviewAnalysisAgent(
            SimpleNamespace(responses=FakeResponses(error=original)),
            model="model",
        )
        detail = PlaceDetail(place_id="1", name="우니도", reviews=["조용해요"])

        with self.assertLogs(
            "datespot_agent.analysis.review",
            level="ERROR",
        ) as captured:
            with self.assertRaises(AnalysisRequestError) as caught:
                await agent.analyze(detail, "조용함")

        self.assertIs(caught.exception.__cause__, original)
        self.assertEqual(
            captured.records[-1].datespot_event,
            "analysis.review.failed",
        )
        self.assertIsNotNone(captured.records[-1].exc_info)


if __name__ == "__main__":
    unittest.main()
