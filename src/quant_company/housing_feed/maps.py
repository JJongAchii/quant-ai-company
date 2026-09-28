"""Verified area centers for interactive map links; never infer an apartment parcel."""

import re
import time
from urllib.parse import urlencode

import httpx

from .contracts import HousingNotice, MapLocation

GEOCODE = "https://api.mapmap.ai/geocode"
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
                except Exception:  # Public geocoding must not suppress an official notice.
                    location = None
                notice = notice.model_copy(update={"map_location": location, "map_checked_on": today})
        entries.append(notice.model_dump(mode="json"))
    return {**receipt, "entries": entries}


def map_url(location, *, embed=False):
    lat, lon = location.latitude, location.longitude
    zoom = 14 if location.level == "neighborhood" else 11
    if embed:
        radius = 0.012 if location.level == "neighborhood" else 0.12
        bbox = f"{lon - radius:.6f},{lat - radius:.6f},{lon + radius:.6f},{lat + radius:.6f}"
        query = urlencode({"bbox": bbox, "layer": "mapnik", "marker": f"{lat:.6f},{lon:.6f}"})
        return "https://www.openstreetmap.org/export/embed.html?" + query
    return f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map={zoom}/{lat:.6f}/{lon:.6f}"


def work_object(notice):
    location = notice.map_location
    if not location:
        return None
    return {"url": notice.url, "external_ref": {"id": notice.id, "type": "housing_notice"},
            "entity_type": "slack#/entities/item",
            "entity_payload": {"attributes": {
                "title": {"text": notice.title[:150]}, "product_name": "주택 청약 피드",
                "display_type": "분양 공고",
                "full_size_preview": {"is_supported": True, "mime_type": "application/vnd.slack-embed"}},
                "custom_fields": [
                    {"key": "official_address", "label": "공고상 주소", "type": "string",
                     "value": notice.address or location.label},
                    {"key": "map_area", "label": "지도 표시", "type": "string",
                     "value": location.label + " 행정구역 중심"},
                ]}}


def details(notice):
    item = work_object(notice)
    item["entity_payload"]["attributes"]["full_size_preview"]["preview_url"] = map_url(
        notice.map_location, embed=True)
    return item
