import json
from datetime import datetime
from importlib.resources import files
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from ..contracts import StrictModel
from ..web_fetch import public_url


class TechFeedSource(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,60}$")
    publisher: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=100)
    feed_url: str
    article_hosts: list[str] = Field(min_length=1, max_length=8)
    enabled: bool = True
    categories: list[str] = Field(default_factory=list, max_length=32)
    paths: list[str] = Field(default_factory=list, max_length=8)
    title_prefixes: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("feed_url")
    @classmethod
    def public_feed(cls, value):
        return public_url(value)

    @field_validator("article_hosts")
    @classmethod
    def public_hosts(cls, value):
        if any(urlsplit(public_url("https://" + host)).hostname != host for host in value):
            raise ValueError("Exact public article hostnames required")
        return value

    @field_validator("paths")
    @classmethod
    def explicit_paths(cls, value):
        if any(not p.startswith("/") or not p.endswith("/") or ".." in p or "%" in p for p in value):
            raise ValueError("Absolute directory prefixes required")
        return value

    def allows_article(self, url):
        return urlsplit(public_url(url)).hostname in self.article_hosts

    def selects(self, url, title, categories):
        if self.categories and not set(self.categories).intersection(categories):
            return False
        return (not self.paths and not self.title_prefixes
                or any(urlsplit(url).path.startswith(p) for p in self.paths)
                or any(title.startswith(p) for p in self.title_prefixes))


class TechFeedItem(StrictModel):
    guid: str = Field(min_length=1, max_length=2048)
    url: str
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=160)
    event_at: datetime | None
    time_kind: Literal["published", "updated", "unknown"]


def load_sources(path=None):
    path = path or files("quant_company.tech_feed").joinpath("sources.json")
    result = [TechFeedSource.model_validate(item) for item in json.loads(path.read_text())]
    if len(result) > 64 or len({s.id for s in result}) != len(result):
        raise ValueError("At most 64 uniquely named tech sources required")
    return result
