import ast
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
    "api.py", "cli.py", "config.py", "db.py", "schema.sql", "socket_mode.py",
    "owner_controls.py", "state_schema.sql", "web_fetch.py", "finance_sources.py",
    "maintenance/policy.py", "maintenance/github.py", "maintenance/applications.py",
    "maintenance/releases.py", "maintenance/schema.sql",
    "staff/cases.py", "staff/store.py", "staff/runner.py", "staff/workflow.py", "staff/schema.sql", "staff/packs.py",
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
    # Zero disables the additional daily quota. Subscription backoff still applies.
    max_daily_calls: int = Field(default=0, ge=0, le=10000)
    poll_seconds: int = Field(default=300, ge=30, le=3600)
    observe_seconds: int = Field(default=600, ge=60, le=86400)
    max_investigation_rounds: int = Field(default=4, ge=1, le=8)
    max_patch_attempts: int = Field(default=3, ge=1, le=5)
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
    category: Literal["platform_defect", "feature_request", "bot_behavior", "collaboration", "organization"]
    hypothesis: str = Field(min_length=10, max_length=2000)
    evaluation: EvaluationPlan
    paths: list[str] = Field(default_factory=list, max_length=8)
    new_paths: list[str] = Field(default_factory=list, max_length=4)
    blocking_decisions: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def repair_or_design(self):
        if bool(self.paths or self.new_paths) != (self.evaluation.mode != "design_only"):
            raise ValueError("repair_requires_paths_design_uses_generated_document")
        if self.new_paths and self.evaluation.mode != "regression":
            raise ValueError("new_modules_require_runtime_regression")
        return self


class CodeQuery(StrictModel):
    path: str | None = Field(default=None, max_length=250)
    query: str | None = Field(default=None, min_length=2, max_length=160)
    start_line: int = Field(default=1, ge=1)
    line_count: int = Field(default=160, ge=1, le=250)

    @model_validator(mode="after")
    def needs_target(self):
        if not self.path and not self.query:
            raise ValueError("code_inspection_needs_path_or_query")
        return self


class Triage(StrictModel):
    finding: Finding | None = None
    reason: str = Field(min_length=1, max_length=1000)
    inspect: list[CodeQuery] = Field(default_factory=list, max_length=4)
    research_query: str | None = Field(default=None, min_length=2, max_length=1000)
    read_urls: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def inspect_before_finding(self):
        if (self.inspect or self.research_query or self.read_urls) and self.finding:
            raise ValueError("inspect_before_finalizing_finding")
        return self


class Edit(StrictModel):
    path: str = Field(max_length=250)
    old: str = Field(max_length=12000)
    new: str = Field(max_length=40000)


class Patch(StrictModel):
    summary: str = Field(min_length=10, max_length=2000)
    edits: list[Edit] = Field(min_length=1, max_length=24)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def writable(path: str, *, new=False) -> bool:
    parts = PurePosixPath(path).parts
    if (path.startswith("/") or not path or not path.startswith(ROOT) or any(p in {".", ".."} or p.startswith(".") for p in parts)
            or "\\" in path or str(PurePosixPath(path)) != path):
        return False
    relative = path[len(ROOT):]
    if new:
        if re.fullmatch(r"tests/test_maintenance_regression_[a-z0-9_]+\.py", relative):
            return True
        if relative.startswith("docs/"):
            return relative.endswith(".md") and relative not in {"docs/deployment.md", "docs/codex-runtime.md"}
    if relative.startswith("docs/"):
        return relative.endswith(".md") and relative not in {"docs/deployment.md", "docs/codex-runtime.md"}
    if not relative.startswith("src/quant_company/"):
        return False
    module = relative.removeprefix("src/quant_company/")
    if module.startswith("staff/playbooks/"):
        return module.endswith(".md") and len(PurePosixPath(module).parts) == 3
    return (module not in PROTECTED and not module.startswith("providers/")
            and (module.endswith(".py") or module == "roles.json"))


def apply_patch(plan: Patch, originals: dict[str, str], case_id: str, *, new_paths=()) -> dict[str, str]:
    """Exact text edits only; no model-selected shell, paths outside the service or file deletion."""
    changes = {}
    required_test = ROOT + "tests/test_maintenance_regression_" + case_id.replace("-", "") + ".py"
    for edit in plan.edits:
        new = edit.path not in originals
        generated_test = edit.path == required_test
        if ((not generated_test and not writable(edit.path, new=new))
                or (new and edit.path not in {*new_paths, required_test})):
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
    for path, content in changes.items():
        if path.endswith(".py"):
            try:
                ast.parse(content, filename=path)
            except SyntaxError as exc:
                raise ValueError("candidate_python_syntax_error") from exc
    if any(p.endswith(".py") and not p.startswith(ROOT + "tests/") for p in changes):
        if required_test not in changes and required_test not in originals:
            raise ValueError("code_change_requires_new_regression_test")
    role_path = ROOT + "src/quant_company/roles.json"
    if role_path in changes:
        before, after = json.loads(originals[role_path]), json.loads(changes[role_path])
        # A code-backed tool registration is reviewable. Identity, delegation, activation and model spend stay fixed.
        def fixed(roles):
            return [{k: v for k, v in r.items() if k not in {"mission", "instructions", "tools"}} for r in roles]
        if fixed(before) != fixed(after):
            raise ValueError("role_permission_or_model_change")
        if [r["tools"] for r in before] != [r["tools"] for r in after]:
            if not any(p.endswith(".py") and p.startswith("src/") for p in changes):
                raise ValueError("role_tools_require_runtime_implementation")
            from ..contracts import ToolRequest

            allowed = set(ToolRequest.model_fields["name"].annotation.__args__)
            contract = changes.get("src/quant_company/contracts.py")
            if contract:
                parsed = ast.parse(contract)
                tool = next((node for node in parsed.body if isinstance(node, ast.ClassDef) and node.name == "ToolRequest"), None)
                field = next((node for node in tool.body if isinstance(node, ast.AnnAssign)
                              and isinstance(node.target, ast.Name) and node.target.id == "name"), None) if tool else None
                if not field or not isinstance(field.annotation, ast.Subscript):
                    raise ValueError("explicit_tool_contract_required")
                values = field.annotation.slice
                literals = values.elts if isinstance(values, ast.Tuple) else [values]
                if not all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in literals):
                    raise ValueError("explicit_tool_contract_required")
                allowed = {v.value for v in literals}
            if any(not set(r["tools"]) <= allowed for r in after):
                raise ValueError("role_tool_missing_from_contract")
    return changes
