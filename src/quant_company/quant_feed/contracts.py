import json
import re
from datetime import date
from importlib.resources import files
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from ..contracts import StrictModel
from ..web_fetch import public_url

QUANT_FEED_AGENT = "quant_scout"
TOPICS = ("alpha_factors", "ml_forecasting", "portfolio_risk", "execution_costs", "research_validity", "replication_data")


class QuantSource(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,60}$")
    publisher: str = Field(min_length=1, max_length=100)
    kind: Literal["atom", "html", "crossref", "seed"]
    url: str
    article_hosts: list[str] = Field(min_length=1, max_length=16)
    paths: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = True
    interval_hours: int = Field(default=1, ge=1, le=168)
    commercial: bool = False
    access_note: str = Field(default="Public originals only; no login or paywall bypass.", max_length=500)

    @field_validator("url")
    @classmethod
    def https(cls, value):
        return public_url(value)

    @field_validator("article_hosts")
    @classmethod
    def hosts(cls, value):
        if any(urlsplit(public_url("https://" + host)).hostname != host for host in value):
            raise ValueError("exact_public_hosts_required")
        return value

    def allows(self, url):
        try:
            target = urlsplit(public_url(url))
            return target.hostname in self.article_hosts
        except (ValueError, UnicodeError):
            return False

    def selects(self, url):
        return self.allows(url) and (not self.paths or any(p in urlsplit(url).path for p in self.paths))


def load_sources(path=None):
    path = path or files("quant_company.quant_feed").joinpath("sources.json")
    sources = [QuantSource.model_validate(s) for s in json.loads(path.read_text())]
    if not sources or len(sources) > 64 or len({s.id for s in sources}) != len(sources):
        raise ValueError("invalid_quant_source_registry")
    return sources


class Evidence(StrictModel):
    claim: str = Field(min_length=1, max_length=800)
    location: str = Field(min_length=1, max_length=50)
    quote: str = Field(min_length=8, max_length=600)
    span_id: str = Field(default="", pattern=r"^(?:p[1-9][0-9]{0,2}-s[1-9][0-9]{0,3})?$")


class ResearchBrief(StrictModel):
    disposition: Literal["publish", "hold", "reject"]
    reason: str = Field(min_length=1, max_length=1000)
    title: str = Field(default="", max_length=200)
    authors: list[str] = Field(default_factory=list, max_length=20)
    published_on: str = Field(default="", max_length=10)
    revised_on: str = Field(default="", max_length=10)
    kind: Literal["empirical", "theory", "methodology", "institutional", "replication", "data_code", "hypothesis"]
    maturity: Literal["peer_reviewed", "working_paper", "institutional_research", "hypothesis", "reproduction_resource"]
    market: str = Field(default="", max_length=160)
    topic: Literal["alpha_factors", "ml_forecasting", "portfolio_risk", "execution_costs", "research_validity", "replication_data"]
    vintage: Literal["recent", "classic", "update"]
    why_read: str = Field(default="", max_length=700)
    idea: str = Field(default="", max_length=1200)
    data_period: str = Field(default="", max_length=600)
    validation: str = Field(default="", max_length=800)
    author_results: str = Field(default="", max_length=1000)
    costs_turnover: str = Field(default="", max_length=600)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    application: str = Field(default="", max_length=700)
    commercial_bias: str = Field(default="", max_length=400)
    change: Literal["new", "cosmetic", "material", "correction", "retraction"] = "new"
    change_summary: str = Field(default="", max_length=700)
    related_urls: list[str] = Field(default_factory=list, max_length=8)
    evidence: list[Evidence] = Field(default_factory=list, max_length=12)

    @field_validator("related_urls")
    @classmethod
    def links(cls, value):
        return [public_url(url) for url in value]

    @model_validator(mode="after")
    def publishable(self):
        if self.disposition != "publish":
            return self
        required = (self.title, self.authors, self.published_on, self.market, self.why_read, self.idea,
                    self.data_period, self.validation, self.author_results, self.costs_turnover,
                    self.limitations, self.application, self.evidence)
        if not all(required) or len(self.evidence) < 2:
            raise ValueError("quant_brief_missing_evidence_or_limitations")
        if self.kind == "institutional" and all(
                value.strip().startswith(("해당 없음", "미기재")) for value in (self.data_period, self.validation)):
            raise ValueError("quant_institutional_without_research_basis")
        for stamp in (self.published_on, self.revised_on):
            if not stamp:
                continue
            if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", stamp):
                raise ValueError("quant_publication_date_format")
            # Partial dates remain partial, never convert retrieval time into publication time.
            date.fromisoformat(stamp + ("-01-01" if len(stamp) == 4 else "-01" if len(stamp) == 7 else ""))
        if self.change in {"material", "correction", "retraction"} and not self.change_summary:
            raise ValueError("quant_change_explanation_required")
        if any(len(v) > 700 for v in self.limitations) or any(len(v) > 160 for v in self.authors):
            raise ValueError("quant_field_too_long")
        return self


class EvidenceReference(StrictModel):
    claim: str = Field(min_length=1, max_length=800)
    span_id: str = Field(pattern=r"^p[1-9][0-9]{0,2}-s[1-9][0-9]{0,3}$")


class ResearchDraft(ResearchBrief):
    """Model selects immutable source spans; only the service writes actual quotations."""

    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=12)


class EvidenceCritique(StrictModel):
    disposition: Literal["pass", "revise", "hold"]
    reason: str = Field(min_length=1, max_length=1200)
    # All checks must pass; unknown is false, not a successful independent replication.
    original_sufficient: bool
    claims_supported: bool
    dates_authors_verified: bool
    limitations_honest: bool
    direct_quant_scope: bool
    substantive_research: bool
    relevance_and_value: bool
    no_investment_advice: bool
    material_change_verified: bool
    issues: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def complete_pass(self):
        if self.disposition == "pass" and (self.issues or not all((self.original_sufficient, self.claims_supported,
                self.dates_authors_verified, self.limitations_honest, self.direct_quant_scope,
                self.substantive_research, self.relevance_and_value,
                self.no_investment_advice))):
            raise ValueError("quant_incomplete_critique")
        return self
