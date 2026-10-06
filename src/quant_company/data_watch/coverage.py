"""Human-facing dates from bounded lake metadata, never object upload times."""

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class DateRule:
    column: str | None
    meaning: str
    group: str
    attention_after_days: int | None = None


@dataclass(frozen=True)
class SourceCheckpoint:
    first_date: date
    check_after: datetime
    explanation: str


# A bound is useful only when its date axis has a known meaning. In particular,
# future ex/pay dates and SEC period end dates are not proof of recent collection.
RULES = {
    "binance_usdtm_klines": DateRule("event_time", "거래시각", "이력 자료"),
    "cusip_map": DateRule(None, "정적 매핑", "이력 자료"),
    "dart_fundamental": DateRule("effective_dt", "공시 반영일", "공시", 90),
    "ecos": DateRule("date", "관측일", "거시", 21),
    "fred": DateRule("date", "관측일", "거시", 21),
    "krx_etf": DateRule("date", "거래일", "한국시장", 5),
    "krx_etf_meta": DateRule("date", "거래일", "한국시장", 5),
    "krx_etf_profile": DateRule("asof", "스냅샷일", "한국시장", 14),
    "krx_flows": DateRule("date", "거래일", "한국시장", 5),
    "krx_fundamental": DateRule("date", "거래일", "한국시장", 5),
    "krx_index": DateRule("date", "거래일", "한국시장", 5),
    "krx_index_fundamental": DateRule("date", "거래일", "한국시장", 5),
    "krx_index_master": DateRule("asof", "스냅샷일", "한국시장", 14),
    "krx_index_prices": DateRule("date", "거래일", "한국시장", 5),
    "krx_prices": DateRule("date", "거래일", "한국시장", 5),
    "krx_sector": DateRule("date", "거래일", "한국시장", 5),
    "krx_stock_master": DateRule("asof", "스냅샷일", "한국시장", 14),
    "krx_security_continuity_coverage": DateRule("window_end", "이력 종료일", "이력 자료"),
    "krx_security_continuity_events": DateRule("effective_date", "이력 사건일", "이력 자료"),
    "krx_security_continuity_legs": DateRule("successor_available_date", "이력 사건일", "이력 자료"),
    "oecd_cli": DateRule("period", "기준월", "거시", 75),
    "prices": DateRule("date", "거래일", "미국시장", 3),
    "sec_13f": DateRule("filed", "제출일", "미국 공시", 150),
    "sec_filings": DateRule("filed", "제출일", "미국 공시", 45),
    "sec_fundamental": DateRule("filed", "제출일", "미국 공시", 45),
    "sec_insider": DateRule("filed", "제출일", "미국 공시", 45),
    "sec_nport": DateRule("filed", "제출일", "미국 공시", 120),
    "sec_nport_fund": DateRule("filed", "제출일", "미국 공시", 120),
    "sec_tickers": DateRule("asof", "스냅샷일", "미국 공시", 60),
    "us_dividends": DateRule("asof", "스냅샷일", "미국시장", 14),
    "us_prices": DateRule("date", "거래일", "미국시장", 3),
    "us_shortvol": DateRule("date", "거래일", "미국시장", 3),
    "us_splits": DateRule("asof", "스냅샷일", "미국시장", 14),
    "us_symbols": DateRule("date", "스냅샷일", "미국시장", 14),
    "us_ticker_details": DateRule("asof", "스냅샷일", "미국시장", 45),
    "us_ticker_events": DateRule("asof", "스냅샷일", "미국시장", 90),
    "us_ticker_history": DateRule("valid_from", "이력 변경일", "이력 자료"),
    "us_tickers": DateRule("asof", "스냅샷일", "미국시장", 14),
}

SUMMARY_GROUPS = (
    ("한국시장", ("krx_prices", "krx_etf", "krx_flows", "krx_index_prices")),
    ("미국 일별", ("prices", "us_prices", "us_shortvol")),
    ("거시", ("fred", "ecos", "oecd_cli")),
    ("공시", ("dart_fundamental", "sec_filings", "sec_13f", "sec_nport")),
    ("미국 기업행사", ("us_dividends", "us_splits")),
)

# Product labels describe the data; internal lake keys remain in the status API.
NAMES = {
    "binance_usdtm_klines": "가상자산 선물 가격 이력",
    "cusip_map": "미국 증권 식별번호 매핑",
    "dart_fundamental": "한국 기업 공시(DART)",
    "ecos": "한국 경제지표(ECOS)",
    "fred": "미국 경제지표(FRED)",
    "krx_etf": "한국 ETF 가격",
    "krx_etf_meta": "한국 ETF 거래 정보",
    "krx_etf_profile": "한국 ETF 기본 정보",
    "krx_flows": "한국 투자자별 수급",
    "krx_fundamental": "한국 주식 투자지표",
    "krx_index": "한국 지수 정보",
    "krx_index_fundamental": "한국 지수 투자지표",
    "krx_index_master": "한국 지수 목록",
    "krx_index_prices": "한국 지수 가격",
    "krx_prices": "한국 주식 가격",
    "krx_sector": "한국 업종 분류",
    "krx_stock_master": "한국 종목 목록",
    "krx_security_continuity_coverage": "한국 종목 연결 이력 범위",
    "krx_security_continuity_events": "한국 종목 변경 사건 이력",
    "krx_security_continuity_legs": "한국 종목 승계 연결 이력",
    "oecd_cli": "경기선행지수(OECD)",
    "prices": "미국 ETF·주가",
    "sec_13f": "미국 기관 보유내역(13F)",
    "sec_filings": "미국 재무 공시 제출목록",
    "sec_fundamental": "미국 재무 공시 재무값",
    "sec_insider": "미국 내부자 거래",
    "sec_nport": "미국 펀드 보유내역",
    "sec_nport_fund": "미국 펀드 기본 정보",
    "sec_tickers": "SEC 상장사 목록",
    "us_dividends": "미국 배당 정보",
    "us_prices": "미국 전종목 가격",
    "us_shortvol": "미국 공매도 거래량",
    "us_splits": "미국 주식분할 정보",
    "us_symbols": "미국 종목 코드 목록",
    "us_ticker_details": "미국 종목 상세 정보",
    "us_ticker_events": "미국 종목 변경 정보",
    "us_ticker_history": "미국 종목 코드 변경 이력",
    "us_tickers": "미국 상장 종목 목록",
}

UPDATE_LABELS = {
    "prices": "9/25 거래분",
    "us_shortvol": "9/25 거래분",
    "sec_filings": "2026년 2분기 자료",
    "sec_fundamental": "2026년 2분기 자료",
    "sec_insider": "2026년 2분기 자료",
    "sec_13f": "2026년 6~8월 자료",
    **dict.fromkeys(("krx_prices", "krx_etf", "krx_flows", "krx_index_prices"), "9/28 거래분"),
}

# Reviewed source checkpoints are reporting facts, not a rolling publication
# calendar or a trigger for incident recovery. A later dataset-wide maximum
# clears a checkpoint but cannot establish per-ticker completeness.
CHECKPOINTS = {
    "prices": SourceCheckpoint(date(2026, 9, 25), datetime(2026, 9, 28, 11, tzinfo=KST),
                               "9/25 미국 거래일분이 아직 레이크 최대 거래일에 반영되지 않음"),
    "us_shortvol": SourceCheckpoint(date(2026, 9, 25), datetime(2026, 9, 28, 11, tzinfo=KST),
                                    "FINRA가 9/25분을 공개한 뒤에도 레이크 최대 거래일에 반영되지 않음"),
    "sec_filings": SourceCheckpoint(date(2026, 4, 1), datetime(2026, 9, 28, tzinfo=KST),
                                    "SEC 분기 FSDS의 2026 Q2 공개본이 레이크 최대 제출일에 반영되지 않음"),
    "sec_fundamental": SourceCheckpoint(date(2026, 4, 1), datetime(2026, 9, 28, tzinfo=KST),
                                        "SEC 분기 FSDS의 2026 Q2 공개본이 레이크 최대 제출일에 반영되지 않음"),
    "sec_13f": SourceCheckpoint(date(2026, 6, 1), datetime(2026, 9, 1, tzinfo=KST),
                                "SEC 2026년 6~8월 13F 공개본이 레이크 최대 제출일에 반영되지 않음"),
    "sec_insider": SourceCheckpoint(date(2026, 4, 1), datetime(2026, 9, 28, tzinfo=KST),
                                    "SEC 분기 Form 3·4·5의 2026 Q2 공개본이 레이크 최대 제출일에 반영되지 않음"),
    **{name: SourceCheckpoint(date(2026, 9, 28), datetime(2026, 9, 28, 20, tzinfo=KST),
                              "9/28 한국 거래일분이 저녁 수집 확인 시각 이후에도 반영되지 않음")
       for name in ("krx_prices", "krx_etf", "krx_flows", "krx_index_prices")},
}


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def dataset_date(row):
    rule = RULES.get(row["dataset"])
    if not rule or rule.column is None:
        return None
    bound = row.get("date_bounds", {}).get(rule.column, {})
    return _day(bound.get("max")) if bound.get("statistics_complete") is True else None


def observed(row, checked_at):
    """A conservative triage hint; only registered contracts can certify lateness."""
    rule = RULES.get(row["dataset"])
    latest = dataset_date(row)
    if isinstance(checked_at, str):
        checked_at = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    today = checked_at.astimezone(KST).date()
    age = (today - latest).days if latest else None
    contract_state = row.get("freshness", {}).get("state")
    checkpoint = CHECKPOINTS.get(row["dataset"])
    if row.get("problem") and row["problem"] != "data_late":
        state = "unreadable"
    elif contract_state == "stale" or row.get("problem") == "data_late":
        state = "late"
    elif checkpoint and checked_at >= checkpoint.check_after and latest and latest < checkpoint.first_date:
        state = "source_gap"
    elif rule and rule.column is None:
        state = "reference"
    elif latest is None:
        state = "undated"
    elif rule and rule.attention_after_days is not None and age > rule.attention_after_days:
        state = "attention"
    else:
        state = "observed"
    return {"rule": rule, "date": latest, "age_days": age, "state": state,
            "object_modified": _day(row.get("source", {}).get("last_modified")),
            "contract_state": contract_state, "checkpoint": checkpoint}


def short_date(value):
    return value.isoformat() if value else "미확인"


def item_text(row, checked_at, *, verbose=False):
    info = observed(row, checked_at)
    rule = info["rule"]
    meaning = rule.meaning if rule else "데이터 기준일 미정"
    latest = "해당 없음" if info["state"] == "reference" else short_date(info["date"])
    flag = "⚠ " if info["state"] in {"late", "source_gap", "attention", "unreadable", "undated"} else ""
    text = f"{flag}{row['dataset']} {meaning} {latest}"
    if info["state"] == "late":
        text += " · 등록 기준보다 늦음"
    elif info["state"] == "source_gap":
        text += f" · 공개/예정일 {info['checkpoint'].first_date} 미도달"
    elif info["state"] == "attention":
        text += f" · {info['age_days']}일 경과, 갱신 확인 필요"
    elif info["state"] in {"unreadable", "undated"}:
        text += " · 데이터 날짜 확인 불가"
    if verbose:
        text += f" · 파일 교체 {short_date(info['object_modified'])}"
    return text
