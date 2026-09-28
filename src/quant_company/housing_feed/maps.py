"""Best-effort public area map previews; never identify an unverified parcel."""

import re
import time

import httpx

from .contracts import HousingNotice, MapLocation

GEOCODE = "https://api.mapmap.ai/geocode"
STATIC_MAP = "https://mapmap.ai/api/static-map"
USER_AGENT = "quant-company-housing-feed/0.1 (+https://github.com/JJongAchii)"
ADMIN = re.compile(r"[가-힣]+(?:시|군|구|동|읍|면)$")


def administrative_queries(address, region):
    parts = address.split()
    expected = "서울특별시" if region == "서울" else "경기도"
    if len(parts) < 2 or parts[0] != expected:
        return []
    names = []
    for part in parts[1:]:
        if not ADMIN.fullmatch(part):
            break
        names.append(part)
        if part.endswith(("동", "읍", "면")):
            break
    return [(" ".join([expected, *names[:length]]), names[length - 1])
            for length in range(len(names), 0, -1)]


def geocode(address, region, *, transport=None):
    queries = administrative_queries(address, region)
    if not queries:
        return None
    with httpx.Client(timeout=10, follow_redirects=False, transport=transport,
                      headers={"User-Agent": USER_AGENT}) as client:
        for index, (query, name) in enumerate(queries):
            if index:
                time.sleep(1)
            try:
                response = client.get(GEOCODE, params={"q": query, "limit": 1})
                if response.status_code == 429 or response.status_code >= 500:
                    return None
                response.raise_for_status()
                feature = response.json().get("features", [])[0]
                properties = feature["properties"]
                longitude, latitude = feature["geometry"]["coordinates"]
                expected_city = "서울특별시" if region == "서울" else query.split()[1]
                if (properties.get("countrycode") != "KR" or properties.get("name") != name
                        or (name.endswith(("동", "읍", "면")) and properties.get("city") != expected_city)
                        or properties.get("city") not in (None, expected_city)
                        or (region == "경기" and properties.get("state") != "경기도")
                        or (region == "서울" and not (126.7 <= longitude <= 127.2 and 37.4 <= latitude <= 37.8))
                        or (region == "경기" and not (125.5 <= longitude <= 128.5 and 36.5 <= latitude <= 38.6))):
                    continue
                return MapLocation(longitude=longitude, latitude=latitude, label=query,
                                   level="neighborhood" if name.endswith(("동", "읍", "면"))
                                   else "municipality")
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                continue
    return None


def enrich(receipt, previous, today, *, resolver=geocode):
    if not receipt.get("ok"):
        return receipt
    entries = []
    for entry in receipt["entries"]:
        notice = HousingNotice.model_validate(entry)
        if notice.address:
            old = previous.get(notice.id)
            if old and old.address == notice.address and (old.map_location or old.map_checked_on == today):
                notice = notice.model_copy(update={"map_location": old.map_location,
                                                   "map_checked_on": old.map_checked_on})
            else:
                try:
                    location = resolver(notice.address, notice.region)
                except Exception:  # The public map service must not suppress an official notice.
                    location = None
                notice = notice.model_copy(update={"map_location": location, "map_checked_on": today})
        entries.append(notice.model_dump(mode="json"))
    return {**receipt, "entries": entries}


def image_url(location):
    point = f"{location.longitude:.7f},{location.latitude:.7f}"
    zoom = 13 if location.level == "neighborhood" else 11
    return f"{STATIC_MAP}?center={point}&zoom={zoom}&size=640x360&markers={point},H&lang=local"


def image_blocks(message, location):
    sections, current = [], ""
    for line in message.splitlines(keepends=True):
        if len(current) + len(line) > 3000 and current:
            sections.append({"type": "section", "text": {"type": "mrkdwn", "text": current.rstrip()}})
            current = ""
        current += line
    if current:
        sections.append({"type": "section", "text": {"type": "mrkdwn", "text": current.rstrip()}})
    sections.append({"type": "image", "image_url": image_url(location),
                     "alt_text": f"{location.label} 중심 참고 지도. 분양 대지의 정확한 위치가 아닙니다."})
    return sections
