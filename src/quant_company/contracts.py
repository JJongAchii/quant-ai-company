"""Typed proposals: model output cannot directly execute a privileged action."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ReasoningEffort = Literal["low", "medium", "high", "xhigh", "max"]
DIRECTOR_MODEL = "gpt-6-astra"


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
                  "finance_compute", "data_quality", "staff_status", "news_status", "briefing_status", "research_control"]
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


class ProviderSession(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    previous_request_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")


class ResearchStageOutput(StrictModel):
    """A private file read or artifact; the service constructs the AgentDecision."""

    action: Literal["read", "complete"]
    read_path: str | None = Field(default=None, min_length=1, max_length=500)
    read_offset: int | None = Field(default=None, ge=0, strict=True)
    artifact_json: str | None = Field(default=None, min_length=1, max_length=60000)

    @model_validator(mode="after")
    def one_action(self):
        if self.action == "read":
            if self.read_path is None or self.read_offset is None or self.artifact_json is not None:
                raise ValueError("A research read requires only a path and offset")
        elif self.read_path is not None or self.read_offset is not None or self.artifact_json is None:
            raise ValueError("Research completion requires only a serialized artifact")
        return self


class ProviderRequest(StrictModel):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    model: str = Field(min_length=1, max_length=80)
    # None preserves already-frozen requests from before explicit effort support.
    reasoning_effort: ReasoningEffort | None = None
    prompt: str = Field(min_length=1, max_length=90000)
    web_search: bool = False
    # Service-owned continuation, resolved against durable runtime receipts.
    session: ProviderSession | None = None
    output_contract: Literal["agent_decision", "quant_brief_v1", "quant_brief_v2", "quant_brief_v3", "quant_brief_v4",
                             "quant_critique_v1", "quant_critique_v2", "quant_search_v1",
                             "research_stage_v1"] = "agent_decision"

    @model_validator(mode="after")
    def scoped_output(self):
        if self.output_contract == "research_stage_v1":
            try:
                canonical_id = str(UUID(self.request_id))
            except ValueError:
                raise ValueError("Research output requires a canonical company turn ID") from None
            if canonical_id != self.request_id or self.web_search or self.session is not None:
                raise ValueError("Research structured output requires a tool-free company turn")
        elif self.output_contract == "quant_search_v1":
            if not self.request_id.startswith("quant-feed-") or not self.web_search:
                raise ValueError("Quant discovery output requires a native-search Quant request")
        elif self.output_contract != "agent_decision" and (
                not self.request_id.startswith("quant-feed-") or self.web_search):
            raise ValueError("Quant structured output requires a tool-free Quant request")
        return self


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
    reasoning_effort: ReasoningEffort = "high"
    instructions: str
    tools: list[str]
    can_delegate_to: list[str]
    active: bool = False
    version: str = "1"
