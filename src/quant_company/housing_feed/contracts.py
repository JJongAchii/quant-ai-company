from datetime import date
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from ..contracts import StrictModel

SOURCES = {
    "applyhome-apt": "청약홈 · 아파트",
    "applyhome-remndr": "청약홈 · 무순위·잔여세대",
    "lh-sale": "LH · 공공분양",
    "sh-sale": "SH · 주택분양",
}
REGIONS = {"서울", "경기"}
HOSTS = {"www.applyhome.co.kr", "static.applyhome.co.kr", "apply.lh.or.kr", "www.i-sh.co.kr"}


def official_url(value):
    parts = urlsplit(value)
    if (parts.scheme != "https" or parts.hostname not in HOSTS or parts.username or parts.password
            or parts.port not in (None, 443) or any(c in value for c in "<>|\r\n")):
        raise ValueError("housing_unregistered_url")
    return value


class ApplicationWindow(StrictModel):
    label: str = Field(min_length=1, max_length=100)
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("housing_reversed_dates")
        return self


class MapLocation(StrictModel):
    longitude: float = Field(ge=125.5, le=128.5)
    latitude: float = Field(ge=36.5, le=38.6)
    label: str = Field(min_length=1, max_length=100)
    level: Literal["neighborhood", "municipality"]


class HousingNotice(StrictModel):
    id: str = Field(min_length=1, max_length=120)
    source: Literal["applyhome-apt", "applyhome-remndr", "lh-sale", "sh-sale"]
    title: str = Field(min_length=1, max_length=300)
    region: Literal["서울", "경기"]
    category: str = Field(max_length=100)
    url: str
    document_url: str | None = None
    published: date
    address: str = Field(default="", max_length=500)
    supply: str = Field(default="", max_length=100)
    windows: list[ApplicationWindow] = Field(default_factory=list, max_length=30)
    price_summary: str = Field(default="", max_length=1000)
    result_date: date | None = None
    # Board closure is not necessarily the application deadline.
    board_close: date | None = None
    schedule_note: str = Field(default="접수 시간·자격은 모집공고문 확인", max_length=300)
    map_location: MapLocation | None = None
    map_checked_on: date | None = None

    @field_validator("url", "document_url")
    @classmethod
    def validate_url(cls, value):
        return official_url(value) if value else value

    @model_validator(mode="after")
    def source_identity(self):
        if not self.id.startswith(self.source + ":"):
            raise ValueError("housing_source_identity_mismatch")
        return self

    def active(self, today):
        if self.published > today:
            return False
        if self.windows:
            return max(w.end for w in self.windows) >= today
        if self.board_close:
            return self.board_close >= today
        return (today - self.published).days <= 14
