"""네이버지도 DOM 값을 모델 데이터로 변환하는 순수 함수."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlparse

from datespot_agent.models import CandidatePlace

TITLE_SUFFIXES = (
    "플레이스 플러스",
    "예약",
    "쿠폰",
    "영업",
    "별점",
    "리뷰",
    "저장",
)
CATEGORY_EXCLUDED_LINES = {
    "저장",
    "페이지 닫기",
    "플레이스 플러스",
}
REVIEW_COUNT_PATTERN = re.compile(
    r"(?:방문자\s*)?리뷰\s*([\d,]+(?:\.\d+)?)\s*(만)?"
)


@dataclass(frozen=True, slots=True)
class CandidateTarget:
    place_id: str
    name: str
    dom_index: int


@dataclass(frozen=True, slots=True)
class HomeMetadata:
    category: str | None
    address: str | None
    review_count: int


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def clean_place_name(value: str) -> str:
    name = normalize_text(value)
    for suffix in TITLE_SUFFIXES:
        index = name.find(suffix)
        if index > 0:
            name = name[:index]
    return normalize_text(name)


def extract_place_id(value: str) -> str | None:
    match = re.search(r"/(?:place|restaurant)/(\d+)", value)
    return match.group(1) if match else None


def parse_candidate_rows(
    rows: list[dict[str, Any]],
    businesses: list[dict[str, Any]],
) -> tuple[list[CandidatePlace], dict[str, CandidateTarget]]:
    apollo_ids = {
        clean_place_name(str(item.get("name", ""))): str(item.get("id", ""))
        for item in businesses
        if item.get("id") and item.get("name")
    }
    apollo_names_by_length = sorted(apollo_ids, key=len, reverse=True)
    candidates: list[CandidatePlace] = []
    targets: dict[str, CandidateTarget] = {}
    for row in rows:
        raw_text = normalize_text(str(row.get("rawText", "")))
        name = clean_place_name(
            str(row.get("title") or row.get("name", ""))
        )
        if not name or "광고" in raw_text:
            continue
        place_id = extract_place_id(str(row.get("href", ""))) or apollo_ids.get(name)
        if not place_id:
            matched_name = next(
                (
                    apollo_name
                    for apollo_name in apollo_names_by_length
                    if name.startswith(apollo_name)
                ),
                None,
            )
            if matched_name is not None:
                name = matched_name
                place_id = apollo_ids[matched_name]
        if not place_id or place_id in targets:
            continue
        target = CandidateTarget(
            place_id=place_id,
            name=name,
            dom_index=int(row["domIndex"]),
        )
        candidates.append(CandidatePlace(place_id=place_id, name=name))
        targets[place_id] = target
    return candidates, targets


def parse_home_text(lines: list[str], place_name: str) -> HomeMetadata:
    normalized = [normalize_text(line) for line in lines if normalize_text(line)]
    category = None
    address = next(
        (
            normalized[index + 1]
            for index, line in enumerate(normalized[:-1])
            if line == "주소"
        ),
        None,
    )
    if address is None:
        address = next(
            (line for line in normalized if line.startswith("서울 ")),
            None,
        )
    for index, line in enumerate(normalized):
        if line == place_name and index + 1 < len(normalized):
            candidate = normalized[index + 1]
            if candidate not in CATEGORY_EXCLUDED_LINES:
                embedded_review = REVIEW_COUNT_PATTERN.search(candidate)
                if embedded_review is not None:
                    candidate = candidate[: embedded_review.start()].strip()
                category = candidate
                break

    review_match = None
    for line in normalized:
        if "리뷰" not in line:
            continue
        review_match = REVIEW_COUNT_PATTERN.search(line)
        if review_match is not None:
            break
    if review_match is None:
        raise ValueError("리뷰 수를 찾지 못함")

    review_count = Decimal(review_match.group(1).replace(",", ""))
    if review_match.group(2) == "만":
        review_count *= 10_000

    return HomeMetadata(
        category=category,
        address=address,
        review_count=int(review_count),
    )


def first_interior_urls(
    images: list[dict[str, str]],
    limit: int = 5,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for image in images:
        match = re.fullmatch(r"INTERIOR_(\d+)", image.get("alt", ""))
        url = image.get("url", "")
        if match and url and url not in seen:
            seen.add(url)
            result.append(url)
    return result[:limit]


def normalize_review_bodies(values: list[str], limit: int = 50) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = normalize_text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result[:limit]


def parse_zoom(url: str) -> int | None:
    values = parse_qs(urlparse(url).query).get("c", [])
    if not values:
        return None
    parts = values[0].split(",")
    candidates = [parts[2], parts[0]] if len(parts) >= 3 else parts
    for value in candidates:
        try:
            zoom = int(float(value))
        except ValueError:
            continue
        if 1 <= zoom <= 21:
            return zoom
    return None
