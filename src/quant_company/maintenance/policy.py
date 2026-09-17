import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from ..contracts import StrictModel

ROOT = ""
WORKFLOW = ".github/workflows/quant-company-ci.yml"
PROTECTED = {
    "api.py", "cli.py", "config.py", "contracts.py", "db.py", "schema.sql", "slack.py", "socket_mode.py",
}
SECRET = re.compile(
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"postgres(?:ql)?://[^\s]+:[^\s]+@|Bearer\s+[A-Za-z0-9._-]{20,}", re.I,
)


class MaintenanceConfig(StrictModel):
    repository: Literal["JJongAchii/quant-ai-company"] = "JJongAchii/quant-ai-company"
    base: Literal["main"] = "main"
    app_id: int = Field(gt=0)
    installation_id: int = Field(gt=0)
    private_key_file: Path
    allowed_owners: list[str] = Field(min_length=1)
    # Shared with the company's total budget; this is an additional, smaller cap.
    max_daily_calls: int = Field(default=6, ge=1, le=12)
    poll_seconds: int = Field(default=300, ge=30, le=3600)
    observe_seconds: int = Field(default=600, ge=60, le=86400)
    enabled: bool = False


class Expectations(StrictModel):
    """Observable decision properties, not a model's opinion of its own answer quality."""

    status: Literal["complete", "continue", "wait"] | None = None
    delegates: list[str] | None = Field(default=None, max_length=4)
    tools: list[str] | None = Field(default=None, max_length=3)
    min_artifacts: int | None = Field(default=None, ge=1, le=3)
    source_ids: list[str] | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def measurable(self):
        if all(value is None for value in self.model_dump().values()):
            raise ValueError("at_least_one_observable_expectation_required")
        return self


class ReplayCase(StrictModel):
    purpose: Literal["target", "control"]
    request_key: str = Field(pattern=r"^turn:[a-f0-9-]{36}$")
    expected: Expectations


class EvaluationPlan(StrictModel):
    mode: Literal["regression", "prompt_replay", "documentation", "design_only"]
    success_criterion: str = Field(min_length=10, max_length=1500)
    cases: list[ReplayCase] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def paired_replay(self):
        if self.mode == "prompt_replay":
            if ({case.purpose for case in self.cases} != {"target", "control"}
                    or len({case.request_key for case in self.cases}) != 2):
                raise ValueError("distinct_target_and_control_required")
        elif self.cases:
            raise ValueError("replay_cases_only_for_prompt_changes")
        return self


class Finding(StrictModel):
    problem_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{4,100}$")
    title: str = Field(min_length=5, max_length=160)
    problem: str = Field(min_length=10, max_length=2000)
    reproduction: str = Field(min_length=10, max_length=2000)
    expected: str = Field(min_length=10, max_length=2000)
    evidence_keys: list[str] = Field(min_length=1, max_length=20)
    category: Literal["platform_defect", "bot_behavior", "collaboration", "organization"]
    hypothesis: str = Field(min_length=10, max_length=2000)
    evaluation: EvaluationPlan
    paths: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def repair_or_design(self):
        if bool(self.paths) != (self.evaluation.mode != "design_only"):
            raise ValueError("repair_requires_paths_design_uses_generated_document")
        return self


class Triage(StrictModel):
    finding: Finding | None = None
    reason: str = Field(min_length=1, max_length=1000)


class Edit(StrictModel):
    path: str = Field(max_length=250)
    old: str = Field(max_length=10000)
    new: str = Field(max_length=10000)


class Patch(StrictModel):
    summary: str = Field(min_length=10, max_length=2000)
    edits: list[Edit] = Field(min_length=1, max_length=8)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def writable(path: str, *, new=False) -> bool:
    parts = PurePosixPath(path).parts
    if (path.startswith("/") or not path or not path.startswith(ROOT) or any(p in {".", ".."} or p.startswith(".") for p in parts)
            or "\\" in path or str(PurePosixPath(path)) != path):
        return False
    relative = path[len(ROOT):]
    if new:
        return bool(re.fullmatch(r"tests/test_maintenance_regression_[a-z0-9_]+\.py", relative)
                    or re.fullmatch(r"docs/improvements/[a-f0-9-]{36}\.md", relative))
    if relative.startswith("docs/"):
        return relative.endswith(".md") and relative not in {"docs/deployment.md", "docs/codex-runtime.md"}
    if not relative.startswith("src/quant_company/"):
        return False
    module = relative.removeprefix("src/quant_company/")
    return ("/" not in module and module not in PROTECTED and module.endswith((".py", "roles.json")))


def apply_patch(plan: Patch, originals: dict[str, str], case_id: str) -> dict[str, str]:
    """Exact text edits only; no model-selected shell, paths outside the service or file deletion."""
    changes = {}
    required_test = ROOT + "tests/test_maintenance_regression_" + case_id.replace("-", "") + ".py"
    for edit in plan.edits:
        new = edit.path not in originals
        if not writable(edit.path, new=new) or (new and edit.path != required_test):
            raise ValueError("protected_or_unknown_path")
        text = changes.get(edit.path, originals.get(edit.path, ""))
        if new and edit.path not in changes:
            if edit.old:
                raise ValueError("new_file_requires_empty_old")
            changed = edit.new
        else:
            if not edit.old or text.count(edit.old) != 1:
                raise ValueError("edit_must_match_exactly_once")
            changed = text.replace(edit.old, edit.new, 1)
        if not changed.strip() or SECRET.search(changed):
            raise ValueError("empty_file_or_possible_secret")
        changes[edit.path] = changed
    changes = {p: s for p, s in changes.items() if s != originals.get(p)}
    if not changes or sum(len(s.encode()) for s in changes.values()) > 150000:
        raise ValueError("empty_or_oversized_patch")
    if any(p.endswith(".py") and not p.startswith(ROOT + "tests/") for p in changes):
        if required_test not in changes:
            raise ValueError("code_change_requires_new_regression_test")
    role_path = ROOT + "src/quant_company/roles.json"
    if role_path in changes:
        before, after = json.loads(originals[role_path]), json.loads(changes[role_path])
        # Prompt changes may be proposed; permissions, activation and model spend are operator policy.
        def fixed(roles):
            return [{k: v for k, v in r.items() if k not in {"mission", "instructions"}} for r in roles]
        if fixed(before) != fixed(after):
            raise ValueError("role_permission_or_model_change")
    return changes
