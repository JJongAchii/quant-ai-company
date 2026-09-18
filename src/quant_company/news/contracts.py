import json
import posixpath
from importlib.resources import files
from typing import Literal
from urllib.parse import unquote, urlsplit

from pydantic import Field, field_validator, model_validator

from ..contracts import StrictModel
from ..web_fetch import public_url


class NewsSource(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,60}$")
    publisher: str = Field(min_length=1, max_length=100)
    origin_group: str = Field(min_length=1, max_length=100)
    kind: Literal["official", "media"]
    feed_url: str
    article_hosts: list[str] = Field(min_length=1, max_length=8)
    article_path_prefixes: list[str] = Field(default_factory=list, max_length=8)
    undated_publication: Literal["reject", "page_metadata"] = "reject"
    license_url: str | None = None
    license_name: str = Field(default="", max_length=160)
    poll_seconds: int = Field(default=600, ge=300, le=86400)
    enabled: bool = False
    use_for_summary: bool = False
    usage_note: str = Field(min_length=1, max_length=1000)

    @field_validator("feed_url")
    @classmethod
    def feed_is_public(cls, value):
        return public_url(value)

    @field_validator("article_hosts")
    @classmethod
    def hosts_are_public(cls, value):
        if any(urlsplit(public_url("https://" + host)).hostname != host for host in value):
            raise ValueError("Article hosts must be exact public hostnames")
        return value

    @field_validator("license_url")
    @classmethod
    def license_is_public(cls, value):
        return public_url(value) if value else None

    @field_validator("article_path_prefixes")
    @classmethod
    def paths_are_explicit(cls, value):
        if any(not p.startswith("/") or not p.endswith("/") or ".." in p or "%" in p for p in value):
            raise ValueError("Article prefixes must be absolute directory paths")
        return value

    def allows_article(self, value):
        url = urlsplit(public_url(value))
        path = "/" + posixpath.normpath(unquote(url.path)).lstrip("/")
        return (url.hostname in self.article_hosts
                and (not self.article_path_prefixes or any(path.startswith(p) for p in self.article_path_prefixes)))


class Evidence(StrictModel):
    article_id: str = Field(min_length=1, max_length=64)
    quote: str = Field(min_length=20, max_length=500)


class NewsProposal(StrictModel):
    disposition: Literal["publish", "hold", "ignore"]
    article_ids: list[str] = Field(min_length=1, max_length=12)
    reason: str = Field(min_length=1, max_length=1000)
    event_id: str | None = None
    category: Literal["거시경제", "세계정세", "무역", "에너지", "산업", "금융"] = "세계정세"
    priority: Literal["major", "urgent"] = "major"
    headline: str = Field(default="", max_length=160)
    facts: str = Field(default="", max_length=900)
    significance: str = Field(default="", max_length=500)
    change: str = Field(default="", max_length=500)
    evidence: list[Evidence] = Field(default_factory=list, max_length=6)
    verification: Literal["insufficient", "official_action", "independent_reports"] = "insufficient"
    independent_origins: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def publish_needs_content(self):
        if self.disposition == "publish" and not (
            self.headline.strip() and self.facts.strip() and self.significance.strip() and self.evidence
        ):
            raise ValueError("Publication requires facts, significance and evidence")
        if len(set(self.article_ids)) != len(self.article_ids):
            raise ValueError("Duplicate article IDs")
        return self


class NewsReview(StrictModel):
    items: list[NewsProposal] = Field(max_length=12)


def load_sources(path=None):
    path = path or files("quant_company.news").joinpath("sources.json")
    result = [NewsSource.model_validate(item) for item in json.loads(path.read_text())]
    if len({item.id for item in result}) != len(result):
        raise ValueError("Duplicate news source IDs")
    return result
