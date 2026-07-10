# Photo Vision PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 1-3 사진 비전 분석 PoC를 실행 가능한 스크립트와 결과 JSON으로 검증한다.

**Architecture:** `poc/1-3-photo-vision/analyze_photos.py`가 1-2 결과 JSON에서 장소와 내부 사진 URL을 읽고 Claude Messages API에 이미지 URL 블록을 전달한다. 응답은 JSON으로 파싱해 사진 점수, 판단 근거, 소개팅 적합/부적합 신호를 `output/photo_vision_result.json`에 저장한다.

**Tech Stack:** Python, Anthropic SDK 0.116.0, uv, unittest, JSON artifacts.

## Global Constraints

- 입력은 기본적으로 `poc/1-2-naver-map-flow/output/naver_map_flow_result.json`를 사용한다.
- 기본 분석 대상은 첫 번째 장소의 내부 사진 최대 3장이다.
- 점수 척도는 0~10점, 소수점 1자리까지 허용한다.
- 1-3 범위는 사진 분석만 포함하고 리뷰 분석은 1-4에서 처리한다.
- API 키가 없거나 API 호출 실패 시 실패 사유를 JSON에 남긴다.

---

### Task 1: Add Vision PoC Helpers And Tests

**Files:**
- Create: `tests/test_photo_vision.py`
- Create: `poc/1-3-photo-vision/analyze_photos.py`

**Interfaces:**
- Produces: `load_photo_input(path: Path, place_index: int, max_photos: int) -> dict`
- Produces: `build_message_content(place_name: str, photo_urls: list[str], criteria: str) -> list[dict]`
- Produces: `parse_json_response(text: str) -> dict`

- [ ] Write tests for loading first-place photo URLs from a sample result.
- [ ] Write tests for image URL message block construction.
- [ ] Write tests for parsing strict and fenced JSON model responses.
- [ ] Implement helpers until tests pass.

### Task 2: Add Claude Vision Execution

**Files:**
- Modify: `poc/1-3-photo-vision/analyze_photos.py`

**Interfaces:**
- Consumes: `ANTHROPIC_API_KEY`, `DATESPOT_MODEL`
- Produces: `run_analysis(...) -> dict`
- Produces: `output/photo_vision_result.json`

- [ ] Call `anthropic.Anthropic().messages.create(...)` with image URL blocks.
- [ ] Save `ok`, `model`, `place`, `photoUrls`, `analysis`, `rawText`, `errors`.
- [ ] Return exit code 0 only when valid analysis JSON has a 0~10 `photoScore`.

### Task 3: Update PoC Docs And Roadmap

**Files:**
- Create: `poc/1-3-photo-vision/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: successful `output/photo_vision_result.json`
- Produces: roadmap status and usage docs.

- [ ] Document goal, input, command, output, and caveats.
- [ ] Mark root roadmap 1-3 complete only after a successful run.
- [ ] Run tests and the script once for final verification.
