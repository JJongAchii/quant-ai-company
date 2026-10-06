from typing import Literal

from pydantic import Field, model_validator

from ..contracts import StrictModel

TREND_FEED_AGENT = "trend_scout"
GOOGLE_RSS = "https://trends.google.com/trending/rss?geo=KR"
TOPIC_TARGET = 10
GOOGLE_CANDIDATE_LIMIT = 30
MAJOR_ISSUE_LIMIT = 20
CANDIDATE_LIMIT = GOOGLE_CANDIDATE_LIMIT + MAJOR_ISSUE_LIMIT


class TrendEvidence(StrictModel):
    article_id: str = Field(min_length=1, max_length=64)
    quote: str = Field(min_length=12, max_length=300)


class TrendItemDraft(StrictModel):
    member_ids: list[str] = Field(min_length=1, max_length=CANDIDATE_LIMIT)
    category: Literal["사회", "경제", "기술", "문화", "연예", "스포츠", "소비", "기타"]
    background: str = Field(default="", max_length=240)
    evidence: list[TrendEvidence] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def consistent(self):
        if len(set(self.member_ids)) != len(self.member_ids):
            raise ValueError("duplicate_trend_members")
        if bool(self.background.strip()) != bool(self.evidence):
            raise ValueError("trend_background_requires_evidence")
        return self


class TrendBriefDraft(StrictModel):
    items: list[TrendItemDraft] = Field(max_length=CANDIDATE_LIMIT)
