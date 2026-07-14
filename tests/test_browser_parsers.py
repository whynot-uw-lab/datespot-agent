from __future__ import annotations

import unittest

from datespot_agent.browser.errors import BrowserExtractionError
from datespot_agent.browser.parsers import (
    first_interior_urls,
    normalize_review_bodies,
    parse_candidate_rows,
    parse_home_text,
    parse_zoom,
)


class BrowserParserTests(unittest.TestCase):
    def test_candidate_rows_drop_ads_missing_ids_and_duplicates(self):
        rows = [
            {
                "domIndex": 0,
                "name": "광고집",
                "rawText": "광고집 광고",
                "href": "/restaurant/1",
            },
            {
                "domIndex": 1,
                "name": "치보 신사점",
                "rawText": "치보 신사점 일식당",
                "href": "/restaurant/1150149433",
            },
            {
                "domIndex": 2,
                "name": "ID 없음",
                "rawText": "ID 없음 일식당",
                "href": "",
            },
            {
                "domIndex": 3,
                "name": "치보 신사점",
                "rawText": "치보 신사점",
                "href": "/restaurant/1150149433",
            },
        ]

        candidates, targets = parse_candidate_rows(rows, [])

        self.assertEqual([item.place_id for item in candidates], ["1150149433"])
        self.assertEqual(targets["1150149433"].dom_index, 1)

    def test_candidate_rows_can_use_apollo_id_by_normalized_name(self):
        candidates, _ = parse_candidate_rows(
            [
                {
                    "domIndex": 4,
                    "name": "카이센동 우니도 본점예약",
                    "rawText": "일식당",
                    "href": "",
                }
            ],
            [{"id": "1720070048", "name": "카이센동 우니도 본점"}],
        )

        self.assertEqual(candidates[0].place_id, "1720070048")

    def test_home_photo_review_and_zoom_parsers(self):
        metadata = parse_home_text(
            [
                "치보 신사점",
                "일식당",
                "서울 강남구 도산대로 15",
                "방문자 리뷰 1,234",
            ],
            "치보 신사점",
        )
        photos = first_interior_urls(
            [
                {"alt": f"INTERIOR_{index}", "url": f"https://img/{index}.jpg"}
                for index in range(7)
            ]
            + [{"alt": "FOOD_0", "url": "https://img/food.jpg"}]
        )
        reviews = normalize_review_bodies(
            [" 조용해요 ", "조용해요", " 음식이 좋아요 "]
        )

        self.assertEqual(
            (metadata.category, metadata.address, metadata.review_count),
            ("일식당", "서울 강남구 도산대로 15", 1234),
        )
        self.assertEqual(len(photos), 5)
        self.assertEqual(reviews, ["조용해요", "음식이 좋아요"])
        self.assertEqual(
            first_interior_urls(
                [
                    {"alt": "INTERIOR_4", "url": "https://img/4.jpg"},
                    {"alt": "INTERIOR_1", "url": "https://img/1.jpg"},
                ]
            ),
            ["https://img/4.jpg", "https://img/1.jpg"],
        )
        self.assertEqual(first_interior_urls([]), [])
        self.assertEqual(normalize_review_bodies([]), [])
        self.assertEqual(
            parse_zoom("https://map.naver.com/?c=127.0,37.5,15,0,0,0,dh"),
            15,
        )
        self.assertEqual(parse_zoom("https://map.naver.com/?c=15.00,0,0,0,dh"), 15)

    def test_home_parser_accepts_current_rating_review_header(self):
        metadata = parse_home_text(
            [
                "이전 페이지",
                "네기다이닝라운지",
                "저장",
                "페이지 닫기",
                "플레이스 플러스",
                "네기다이닝라운지",
                "이자카야",
                "별점",
                "4.76리뷰 1,495",
                "주소",
                "서울 강남구 도산대로15길 18 4층 네기다이닝라운지",
            ],
            "네기다이닝라운지",
        )

        self.assertEqual(metadata.category, "이자카야")
        self.assertEqual(
            metadata.address,
            "서울 강남구 도산대로15길 18 4층 네기다이닝라운지",
        )
        self.assertEqual(metadata.review_count, 1495)

    def test_home_parser_splits_category_and_compact_review_count(self):
        metadata = parse_home_text(
            [
                "쿄코코 신논현점",
                "페이지 닫기",
                "쿄코코 신논현점",
                "일식당리뷰 1.6만",
                "주소",
                "서울 강남구 강남대로106길 23 지하1층",
            ],
            "쿄코코 신논현점",
        )

        self.assertEqual(metadata.category, "일식당")
        self.assertEqual(metadata.review_count, 16_000)

    def test_home_parser_reads_non_seoul_address_after_label(self):
        metadata = parse_home_text(
            [
                "아이노쇼텐",
                "이자카야리뷰 1,625",
                "주소",
                "경기 성남시 분당구 정자일로 136 엠코헤리츠 3단지 113호",
            ],
            "아이노쇼텐",
        )

        self.assertEqual(
            metadata.address,
            "경기 성남시 분당구 정자일로 136 엠코헤리츠 3단지 113호",
        )

    def test_missing_review_count_is_extraction_failure_input(self):
        with self.assertRaises(ValueError):
            parse_home_text(["치보 신사점", "일식당"], "치보 신사점")

    def test_error_keeps_run_step_and_place_context(self):
        error = BrowserExtractionError(
            "리뷰 수 파싱 실패",
            run_id="run-1",
            step="home",
            place_id="1150149433",
        )

        self.assertIn("run-1", str(error))
        self.assertEqual(error.step, "home")


if __name__ == "__main__":
    unittest.main()
