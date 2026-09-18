"""Typed proposals: model output cannot directly execute a privileged action."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Delegation(StrictModel):
    agent: str = Field(min_length=1, max_length=64)
    instruction: str = Field(min_length=1, max_length=6000)


class PeerMessage(StrictModel):
    agent: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=4000)


class ArtifactDraft(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=60000)
    source_ids: list[str] = Field(default_factory=list, max_length=20)


class ToolRequest(StrictModel):
    name: Literal["calculate", "knowledge_search", "read_source", "lake_catalog", "lake_describe", "lake_sample",
                  "company_history", "maintenance_review", "maintenance_status", "system_status", "repository_read",
                  "task_control", "finance_search", "finance_read", "web_search", "web_read",
                  "finance_compute", "data_quality", "staff_status", "news_status"]
    arguments: dict[str, Any]


class MemoryProposal(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    source_ids: list[str] = Field(min_length=1, max_length=20)


class FollowUp(StrictModel):
    at: datetime
    instruction: str = Field(min_length=1, max_length=4000)

    @field_validator("at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Follow-up time must include a timezone")
        return value


class AgentDecision(StrictModel):
    say: str = Field(max_length=6000)
    status: Literal["complete", "continue", "wait"]
    delegations: list[Delegation] = Field(default_factory=list, max_length=4)
    messages: list[PeerMessage] = Field(default_factory=list, max_length=4)
    artifacts: list[ArtifactDraft] = Field(default_factory=list, max_length=3)
    tools: list[ToolRequest] = Field(default_factory=list, max_length=3)
    memories: list[MemoryProposal] = Field(default_factory=list, max_length=2)
    follow_up: FollowUp | None = None

    @model_validator(mode="after")
    def meaningful_progress(self) -> "AgentDecision":
        if self.delegations and self.status != "wait":
            raise ValueError("Delegation requires waiting for child results")
        if self.tools and self.status != "continue":
            raise ValueError("Tool results must be examined in another turn")
        if self.status == "wait" and not self.delegations:
            raise ValueError("Waiting requires a concrete delegation")
        if self.status == "complete" and not (self.say.strip() or self.artifacts):
            raise ValueError("Completion requires a result")
        return self


class ProviderRequest(StrictModel):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    model: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=90000)
    web_search: bool = False


class WebSearchEvent(StrictModel):
    id: str = Field(max_length=200)
    query: str = Field(default="", max_length=8000)
    action: dict[str, Any] | None = None


class ProviderResponse(StrictModel):
    request_id: str
    decision: AgentDecision
    thread_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    provider: str = "codex"
    web_searches: list[WebSearchEvent] = Field(default_factory=list, max_length=40)


class ProviderFault(Exception):
    def __init__(self, code: str, message: str, retry_after_seconds: int = 0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after_seconds = retry_after_seconds


class HumanRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=10000)
    agent: str = "director"
    project_id: str | None = None


class Role(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    name: str
    mission: str
    model: str
    instructions: str
    tools: list[str]
    can_delegate_to: list[str]
    active: bool = False
    version: str = "1"
