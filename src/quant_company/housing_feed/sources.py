"""Read public official pages; no authentication, private endpoints or model calls."""

import hashlib
import re
from datetime import date, timedelta
from urllib.parse import urlencode

import httpx

from .contracts import REGIONS, ApplicationWindow, HousingNotice, official_url
from .html import parse

APPLYHOME = "https://www.applyhome.co.kr/ai/aia/"
LH = "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/"
SH = "https://www.i-sh.co.kr/app/lay2/program/S48T1581C1617/www/brd/m_244/"
MAX_BYTES = 2_000_000
DATE = re.compile(r"(?<!\d)(20\d{2})[.\-/](\d{2})[.\-/](\d{2})(?!\d)")


def dates(text):
    return [date(int(y), int(m), int(d)) for y, m, d in DATE.findall(text)]


def window(label, text):
    found = dates(text)
    if not found:
        return None
    if len(found) > 2:
        raise ValueError("housing_ambiguous_date_range")
    return ApplicationWindow(label=label, start=found[0], end=found[-1])


def cells(row):
    return [cell.text() for cell in row.cells()]


def applyhome_list(body, source):
    root = parse(body)
    if not any("조회결과" in c.text() for c in root.find("caption")):
        raise ValueError("applyhome_list_structure_changed")
    rows = []
    for row in root.find("tr"):
        if "data-pbno" not in row.attrs:
            continue
        values = cells(row)
        apt = source == "applyhome-apt"
        if len(values) < (9 if apt else 7):
            raise ValueError("applyhome_list_columns_changed")
        if values[0] not in REGIONS or (apt and values[2] != "분양주택"):
            continue
        pb, hm = row.attrs["data-pbno"], row.attrs["data-hmno"]
        if not re.fullmatch(r"\d{10}", pb) or not re.fullmatch(r"\d{10}", hm):
            raise ValueError("applyhome_invalid_identifier")
        published = dates(values[6 if apt else 4])
        period = window("청약접수", values[7 if apt else 5])
        if len(published) != 1 or not period:
            raise ValueError("applyhome_missing_dates")
        path = "selectAPTLttotPblancDetail.do" if apt else "selectAPTRemndrLttotPblancDetailView.do"
        rows.append(HousingNotice(
            id=f"{source}:{hm}:{pb}", source=source, title=row.attrs["data-honm"],
            region=values[0], category=values[1], published=published[0], windows=[period],
            url=APPLYHOME + path + "?" + urlencode({"houseManageNo": hm, "pblancNo": pb})))
    row_count = sum("data-pbno" in row.attrs for row in root.find("tr"))
    if not row_count and not any(term in root.text() for term in ["조회된", "검색된", "총게시물 : 0"]):
        raise ValueError("applyhome_unrecognized_empty_page")
    pages = [int(n) for n in re.findall(r"pageIndex=(\d+)", body)]
    return rows, max(pages, default=1)


def applyhome_detail(body, notice):
    root = parse(body)
    tables = list(root.find("table"))
    primary = next((t for t in tables if any(c.text() == "입주자모집공고 주요정보"
                                           for c in t.find("caption"))), None)
    if primary is None or notice.title not in primary.text():
        raise ValueError("applyhome_detail_identity_mismatch")
    fields = {v[0]: v[1] for row in primary.find("tr") if len(v := cells(row)) == 2}
    schedule = next((t for t in tables if any("청약일정" in c.text() for c in t.find("caption"))), None)
    if schedule is None:
        raise ValueError("applyhome_schedule_missing")
    periods, result, groups = [], None, []
    for row in schedule.find("tr"):
        values = cells(row)
        if not values:
            continue
        if "청약접수" in values and "구분" in values:
            groups = values[values.index("구분") + 1:-1]
        elif values[0] in {"특별공급", "일반공급", "1순위", "2순위", "청약접수", "무순위", "임의공급"}:
            dated = [(i, text) for i, text in enumerate(values[1:]) if dates(text)]
            for i, text in dated:
                group = groups[i] if i < len(groups) and groups[i] != "접수기간" else ""
                periods.append(window(" · ".join(filter(None, [values[0], group])), text))
        elif values[0] == "당첨자 발표일" and len(values) > 1:
            found = dates(values[1])
            result = found[0] if found else None
    if not periods:
        raise ValueError("applyhome_application_dates_missing")
    # Use the source's exact unit and highest-price label, not a purported minimum price.
    price = next((t for t in tables if any("공급금액" in h.text() for h in t.find("thead"))), None)
    prices = []
    if price and "최고가" in price.text() and "단위:만원" in root.text().replace(" ", ""):
        for row in price.find("tr"):
            values = cells(row)
            if len(values) >= 2 and re.fullmatch(r"[\d,]+", values[1]):
                prices.append(f"{values[0]}: {values[1]}만원")
    document = next((a.attrs.get("href") for a in root.find("a") if a.text() == "모집공고문 보기"), None)
    return HousingNotice.model_validate({**notice.model_dump(), "address": fields.get("공급위치", ""),
                                        "supply": fields.get("공급규모", ""), "windows": periods,
                                        "result_date": result, "document_url": document,
                                        "price_summary": ("주택형별 최고가 · " + " / ".join(prices))[:1000]
                                        if prices else "분양가: 모집공고문 확인"})


def lh_list(body):
    root = parse(body)
    if not any("공고명" in t.text() and "마감일" in t.text() for t in root.find("thead")):
        raise ValueError("lh_list_structure_changed")
    result = []
    for row in root.find("tr"):
        links = [a for a in row.find("a") if "wrtancInfoBtn" in a.attrs.get("class", "")]
        if not links:
            continue
        values = cells(row)
        if len(values) != 9:
            raise ValueError("lh_list_columns_changed")
        region = {"서울특별시": "서울", "경기도": "경기"}.get(values[3])
        if not region:
            continue
        a = links[0]
        identity = a.attrs["data-id1"]
        if not re.fullmatch(r"\d+", identity):
            raise ValueError("lh_invalid_identifier")
        params = dict(mi="1027", panId=identity, ccrCnntSysDsCd=a.attrs["data-id2"],
                      uppAisTpCd=a.attrs["data-id3"], aisTpCd=a.attrs["data-id4"])
        title = re.sub(r"\s+(?:\d+일전|NEW)$", "", a.text()).strip()
        published, close = dates(values[5]), dates(values[6])
        if len(published) != 1:
            raise ValueError("lh_missing_publication_date")
        result.append(HousingNotice(id="lh-sale:" + identity, source="lh-sale", title=title, region=region,
                                    category=values[1], published=published[0],
                                    board_close=close[0] if close else None,
                                    url=LH + "selectWrtancInfo.do?" + urlencode(params)))
    pages = [int(n) for n in re.findall(r"goPaging\((\d+)\)", body)]
    return result, max(pages, default=1)


def lh_detail(body, notice):
    root = parse(body)
    if notice.title not in root.text():
        raise ValueError("lh_detail_identity_mismatch")
    periods, times, prices = [], [], []
    for table in root.find("table"):
        caption = " ".join(c.text() for c in table.find("caption"))
        if "공급일정" in caption and "신청일시" in caption:
            for row in table.find("tr"):
                values = cells(row)
                if len(values) >= 2 and (period := window(values[0], values[1])):
                    # Announcement/result/contract rows must never become application reminders.
                    if any(x in values[0] for x in ["발표", "계약", "서류", "당첨"]):
                        continue
                    periods.append(period)
                    times.append(values[0] + " " + values[1])
        if "주택형 안내" in caption and "평균분양가격(원)" in caption:
            for row in table.find("tr"):
                values = cells(row)
                if len(values) >= 5 and re.fullmatch(r"[\d,]+", values[4]):
                    prices.append(values[0] + ": " + values[4] + "원")
    return HousingNotice.model_validate({**notice.model_dump(), "windows": periods,
                                        "price_summary": ("주택형별 평균가 · " + " / ".join(prices))[:1000]
                                        if prices else "분양가: 모집공고문 확인",
                                        "schedule_note": " / ".join(times)[:300] if times
                                        else "접수 일정·시간·자격은 모집공고문 확인"})


def sh_list(body):
    root = parse(body)
    if not any("등록일" in t.text() for t in root.find("thead")):
        raise ValueError("sh_list_structure_changed")
    result = []
    for row in root.find("tr"):
        links = [a for a in row.find("a") if "getDetailView(" in a.attrs.get("onclick", "")]
        if not links:
            continue
        a = links[0]
        match = re.search(r"getDetailView\('(\+?\d+)'\)", a.attrs["onclick"])
        if not match:
            raise ValueError("sh_invalid_identifier")
        title = a.text()
        if not re.search(r"입주자.*모집|잔여세대|무순위|분양.*공고|공급.*공고", title):
            continue
        found = dates(row.text())
        if len(found) != 1:
            raise ValueError("sh_missing_publication_date")
        identity = match[1]
        result.append(HousingNotice(id="sh-sale:" + identity, source="sh-sale", title=title,
                                    region="서울", category="주택분양 공고", published=found[0],
                                    url=SH + "view.do?" + urlencode({"seq": identity, "multi_itm_seq": "1"}),
                                    price_summary="분양가: 모집공고문 확인",
                                    schedule_note="접수 일정·시간·자격은 첨부 모집공고문 확인"))
    return result


class Reader:
    def __init__(self, client):
        self.client, self.receipts = client, []

    def get(self, url, params=None):
        official_url(url)
        with self.client.stream("GET", url, params=params) as response:
            response.raise_for_status()
            chunks, length = [], 0
            for chunk in response.iter_bytes():
                length += len(chunk)
                if length > MAX_BYTES:
                    raise ValueError("housing_response_too_large")
                chunks.append(chunk)
            body = b"".join(chunks)
            self.receipts.append({"url": str(response.url), "status": response.status_code,
                                  "bytes": length, "sha256": hashlib.sha256(body).hexdigest()})
            return body.decode("utf-8", errors="strict")


def collect_source(source, today, *, transport=None):
    reader = None
    try:
        with httpx.Client(timeout=20, follow_redirects=False, transport=transport,
                          headers={"User-Agent": "quant-company-housing-feed/0.1 (official public notices)"}) as client:
            reader = Reader(client)
            notices = []
            if source.startswith("applyhome-"):
                path = ("selectAPTLttotPblancListView.do" if source == "applyhome-apt"
                        else "selectAPTRemndrLttotPblancListView.do")
                for region in sorted(REGIONS):
                    page, seen = 1, set()
                    while True:
                        body = reader.get(APPLYHOME + path, {
                            "suplyAreaCode": region, "pageIndex": page,
                            "beginPd": (today - timedelta(days=365)).strftime("%Y%m"), "endPd": today.strftime("%Y%m")})
                        rows, last = applyhome_list(body, source)
                        if page > 1 and rows and {n.id for n in rows} <= seen:
                            raise ValueError("applyhome_repeated_page")
                        seen.update(n.id for n in rows)
                        for notice in rows:
                            if notice.active(today):
                                notices.append(applyhome_detail(reader.get(notice.url), notice))
                        if page >= last:
                            break
                        page += 1
                        if page > 50:
                            raise ValueError("applyhome_pagination_limit")
            elif source == "lh-sale":
                page = 1
                while True:
                    body = reader.get(LH + "selectWrtancList.do", {
                        "mi": "1027", "uppAisTpCd": "053954", "srchUppAisTpCd": "053954", "listCo": "50",
                        "currPage": page, "panStDt": (today - timedelta(days=365)).strftime("%Y%m%d"),
                        "panEdDt": today.strftime("%Y%m%d"), "panSs": "공고중"})
                    rows, last = lh_list(body)
                    for notice in rows:
                        if notice.active(today):
                            notices.append(lh_detail(reader.get(notice.url), notice))
                    if page >= last:
                        break
                    page += 1
                    if page > 20:
                        raise ValueError("lh_pagination_limit")
            elif source == "sh-sale":
                # Low-volume recruitment board. Record a bounded coverage window, not a full-history claim.
                for page in range(1, 4):
                    body = reader.get(SH + "list.do", {"multi_itm_seq": "1", "page": page})
                    notices.extend(n for n in sh_list(body) if n.active(today))
            else:
                raise ValueError("unknown_housing_source")
            unique = {n.id: n for n in notices if n.active(today)}
            return {"ok": True, "entries": [n.model_dump(mode="json") for n in unique.values()],
                    "requests": reader.receipts}
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        # Raw response bodies, credentials and request headers are not diagnostic output.
        return {"ok": False, "error": type(exc).__name__, "requests": reader.receipts if reader else []}
