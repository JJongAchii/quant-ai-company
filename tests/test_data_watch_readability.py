"""Render real sanitized lake metadata; these checks do not send Slack messages."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from quant_company.data_watch.coverage import NAMES
from quant_company.data_watch.reporting import incident_text, list_text, status_text


@pytest.fixture
def snapshot():
    root = Path(__file__).resolve().parents[1]
    before = json.loads((root / "docs/project/evidence/data-watch-readability-20261006/before.json").read_text())
    at = datetime.fromisoformat(before["checked_at"])
    return {"checked_at": at,
            "inventory": {"receipt": {"ok": True}, "checked_at": before["inventories"][0]["checked_at"]},
            "next_inventory_at": datetime.fromtimestamp((int(at.timestamp()) // 1800 + 1) * 1800, UTC),
            "datasets": before["datasets"], "core_checks": []}


def test_real_source_status_is_short_and_actions_do_not_mix(snapshot):
    text = status_text(snapshot)
    assert len(text) < 1300
    assert "수집·반영 경로 점검" in text and "2026년 2분기 자료 미반영" in text
    assert "갱신 일정 확인 필요" in text and "5일째 새 날짜 없음" in text
    assert "읽기·날짜 확인 문제" in text and "미국 전종목 가격*: 파일 정보 읽기 한도 초과" in text
    assert text.count("미국 재무 공시(SEC)") == text.count("한국시장 주식·ETF·수급·지수") == 1
    assert "2026년 8월" in text
    assert "고정 연구 입력 6개" not in text and "파일 정보 확인 37/38" not in text
    assert "파일 교체 2026" not in text
    assert all(row["dataset"] not in text for row in snapshot["datasets"])
    us_line = next(line for line in text.splitlines() if "미국 가격·공매도량" in line)
    assert "10/01 거래분까지" in us_line and "미반영" not in us_line


def test_real_list_keeps_all_38_human_names_and_separate_file_dates(snapshot):
    text = list_text(snapshot)
    assert len(text) <= 3000
    assert len(snapshot["datasets"]) == 38
    assert all(NAMES[row["dataset"]] + ":" in text for row in snapshot["datasets"])
    assert "한국 주식 가격: 10/02 거래분까지 · 파일 10/05" in text
    assert "미국 전종목 가격: 날짜 확인 실패 · 파일 10/05" in text
    assert "고정·이력 자료" in text and "경제지표" in text
    assert "나머지" not in text


def test_a_lagging_or_failed_group_member_stays_visible(snapshot):
    changed = deepcopy(snapshot)
    index = next(row for row in changed["datasets"] if row["dataset"] == "krx_index_prices")
    index["date_bounds"]["date"]["max"] = "2026-09-28"
    text = status_text(changed)
    assert "한국시장 주식·ETF·수급·지수" not in text
    assert "한국 지수 가격*: 09/28 거래분까지" in text
    assert "한국 주식 가격: 10/02 거래분까지" in text
    index["problem"], index["date_bounds"] = "parquet_footer_limit", {}
    text = status_text(changed)
    assert "한국 지수 가격*: 파일 정보 읽기 한도 초과" in text
    assert "한국 주식 가격: 10/02 거래분까지" in text


def test_sec_pair_is_combined_only_when_its_dates_and_problems_agree(snapshot):
    changed = deepcopy(snapshot)
    values = next(row for row in changed["datasets"] if row["dataset"] == "sec_fundamental")
    values["date_bounds"]["filed"]["max"] = "2026-03-30"
    text = status_text(changed)
    assert "미국 재무 공시(SEC)" not in text
    assert "미국 재무 공시 제출목록*: 03/31 제출분까지" in text
    assert "미국 재무 공시 재무값*: 03/30 제출분까지" in text


def test_failed_and_initial_inventory_do_not_present_cached_dates_as_current(snapshot):
    snapshot["inventory"]["receipt"]["ok"] = False
    snapshot["last_successful_inventory_at"] = "2026-10-05T00:00:00+00:00"
    text = status_text(snapshot)
    assert "아래는 10/05 09:00 KST 마지막 성공 기록" in text
    assert "현재 상태는 재검사 대기" in text and "(이전 기록)" in text
    snapshot["inventory"], snapshot["last_successful_inventory_at"] = None, None
    text = status_text(snapshot)
    assert "첫 데이터 목록을 아직 받지 못했습니다" in text and "10/02 거래분" not in text


def test_recovery_text_describes_recovered_access_without_old_failure_impact():
    detail = {"problem": None, "impact": "최신 레이크 전체 목록 확인 불가",
              "checked_at": "2026-10-06T01:00:00+00:00", "next_check": "2026-10-06T01:30:00+00:00"}
    text = incident_text("전체 목록 접근", "recovered", detail, detail["checked_at"])
    assert "검사 복구" in text and "최신 자료 날짜는 `상태`" in text
    assert "확인 불가" not in text
