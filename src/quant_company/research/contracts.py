from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from ..contracts import StrictModel

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Commit = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
RecipeId = Literal["kr-etf-p11-replay-v1"]


class Recipe(StrictModel):
    id: RecipeId
    title: str
    kind: Literal["equivalent_replay"]
    worker_id: Literal["worker"] = "worker"
    hostname: Literal["DESKTOP-5T00NAF"] = "DESKTOP-5T00NAF"
    gpu: Literal["NVIDIA GeForce RTX 3070"] = "NVIDIA GeForce RTX 3070"
    code_commit: Commit
    evidence_commit: Commit
    lake_id: str
    config_files: dict[str, Digest]
    input_files: dict[str, Digest]
    expected_outputs: dict[str, Digest]
    audit_path: str
    audit_sha256: Digest
    audit_receipt_sha256: Digest
    objective_digest: str
    scope_digest: str
    scope_files: dict[str, Digest]
    description: str


class Assignment(StrictModel):
    job_id: UUID
    project_id: UUID
    revision: int = Field(ge=1)
    recipe_id: RecipeId
    manifest_digest: Digest
    approval_event_id: str
    lease_token: Digest
    action: Literal["run", "reconcile", "cancel"]


class WorkerPoll(StrictModel):
    worker_id: Literal["worker"]


class WorkerUpdate(StrictModel):
    lease_token: Digest
    sequence: int = Field(ge=1)
    state: Literal["running", "cancelled", "failed", "uncertain"]
    reason: str = Field(default="", max_length=500)


class ExecutionReceipt(StrictModel):
    job_id: UUID
    project_id: UUID
    revision: int = Field(ge=1)
    recipe_id: RecipeId
    manifest_digest: Digest
    approval_event_id: str
    worker_id: Literal["worker"]
    hostname: Literal["DESKTOP-5T00NAF"]
    gpu: Literal["NVIDIA GeForce RTX 3070"]
    code_commit: Commit
    company_commit: Commit
    input_files: dict[str, Digest]
    config_files: dict[str, Digest]
    output_files: dict[str, Digest]
    started_at: str
    completed_at: str
    execution_count: Literal[1]
    qualification_passed: bool
    sealed_read: Literal[False]
    scientific_trials_added: Literal[0]


class ResearchTool(StrictModel):
    action: Literal["catalog", "request", "status", "cancel"]
    recipe_id: RecipeId | None = None
    job_id: UUID | None = None
