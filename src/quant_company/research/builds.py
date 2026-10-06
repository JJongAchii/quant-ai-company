"""Operator-provisioned research profiles and scoped, committed engineer patches."""

import hashlib
import json
from pathlib import Path

from pydantic import Field

from ..company import PolicyError
from ..contracts import StrictModel
from .adaptive_contracts import (
    ADAPTIVE_RECIPE,
    RESEARCH_RECIPE,
    AdaptiveExecutionProfile,
    AdaptiveManifest,
    digest_model,
    record_digest,
)
from .contracts import Commit, Digest
from .mission_contracts import MissionSpec, TrialPlan
from .policy_contracts import require_profile_policy, scoped_kwargs
from .worker import atomic_json, read_json, sha_file
from .workspace import PreparedWorkspace, TextPatch, prepare_workspace, safe_relative_path, snapshot_files


class ServerResearchProfile(StrictModel):
    public_profile: AdaptiveExecutionProfile
    source_bundle: Path
    source_bundle_sha256: Digest
    base_commit: Commit
    config_path: str
    config_files: list[str] = Field(min_length=1)
    allowed_write_paths: list[str] = Field(min_length=1)


def profile_for(company, spec: MissionSpec) -> ServerResearchProfile:
    path = company.settings.research_profiles_file
    if path is None or not path.is_file() or path.is_symlink():
        raise PolicyError("Research execution profiles are not provisioned")
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or spec.execution_profile not in value:
        raise PolicyError("Research execution profile is unavailable")
    profile = ServerResearchProfile.model_validate(value[spec.execution_profile])
    public = profile.public_profile
    try:
        require_profile_policy(spec, public)
    except ValueError as exc:
        raise PolicyError(str(exc)) from exc
    if (public.id != spec.execution_profile or digest_model(public) != spec.execution_profile_digest
            or profile.base_commit != spec.code.base_commit
            or set(profile.allowed_write_paths) != set(spec.code.write_paths)
            or set(public.evaluation_input_names) != set(spec.data.input_files)
            or (public.fixture_only and not company.settings.fixture_mode)):
        raise PolicyError("Research execution profile does not match the frozen mission")
    for name in (*profile.allowed_write_paths, *profile.config_files, profile.config_path):
        safe_relative_path(name)
    if profile.config_path not in profile.config_files or not set(profile.config_files) <= set(public.code_paths):
        raise PolicyError("Profile configuration files are outside its committed code closure")
    if not profile.source_bundle.is_absolute() or profile.source_bundle.is_symlink():
        raise PolicyError("Operator source bundle must be an absolute regular file")
    if sha_file(profile.source_bundle) != profile.source_bundle_sha256:
        raise PolicyError("Operator source bundle changed")
    return profile


def _prepared(root, *, expected_source, expected_base, patches):
    """Recover a completed Git build after a lost DB response, using its frozen preimages."""
    manifest_path = root / "manifest.json"
    manifest = read_json(manifest_path)
    wanted = [{"path": patch.path,
               "before_sha256": hashlib.sha256(patch.expected_text.encode()).hexdigest()
               if patch.expected_text is not None else None,
               "after_sha256": hashlib.sha256(patch.replacement_text.encode()).hexdigest()} for patch in patches]
    if (manifest["source_snapshot_sha256"] != expected_source or manifest["base_commit"] != expected_base
            or manifest["patches"] != wanted):
        raise PolicyError("Completed research build does not match its stage")
    worktree, bundle = root / "code", root / "prepared.bundle"
    if (snapshot_files(worktree, manifest["commit"]) != manifest["files"]
            or sha_file(bundle) != manifest["bundle_sha256"]):
        raise PolicyError("Completed research build changed")
    return PreparedWorkspace(worktree, manifest["commit"], bundle, manifest["bundle_sha256"],
                             manifest, manifest_path, sha_file(manifest_path))


def materialize(company, profile, destination, patches=()):
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (destination / "manifest.json").is_file():
        return _prepared(destination, expected_source=profile.source_bundle_sha256,
                         expected_base=profile.base_commit, patches=patches)
    # A partial isolated checkout has no execution authority. Preserve it as failure evidence;
    # a new technical stage attempt gets its own path, rather than overwriting that history.
    return prepare_workspace(profile.source_bundle, profile.source_bundle_sha256, profile.base_commit,
        destination, tuple(profile.allowed_write_paths), tuple(profile.public_profile.protected_paths), patches)


def base_workspace(company, profile):
    destination = company.settings.research_artifact_dir / "profiles" / digest_model(profile.public_profile)
    return materialize(company, profile, destination)


def experiment_signature(root, files, config_files):
    """JSON formatting is not a different scientific configuration."""
    identities = dict(files)

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate-config-key")
            result[key] = value
        return result

    for name in config_files:
        if name.endswith(".json"):
            try:
                value = json.loads((root / name).read_bytes(), object_pairs_hook=unique_pairs)
                encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()
            except (UnicodeError, ValueError, OSError):
                raise PolicyError("Research configuration must be unambiguous finite JSON") from None
            identities[name] = hashlib.sha256(encoded).hexdigest()
    return hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest()


def build_trial(company, row, snapshot):
    spec = MissionSpec.model_validate(snapshot["spec"])
    profile = profile_for(company, spec)
    data = row["result"]
    required = {"patches", "rationale"}
    if row["stage"] == "repair":
        required.add("repair_source_ids")
    if set(data) != required or not isinstance(data["rationale"], str) or not data["rationale"].strip():
        raise PolicyError("Engineer must provide exact patches and a reason")
    if not isinstance(data["patches"], list) or not 1 <= len(data["patches"]) <= 20:
        raise PolicyError("Engineer must make a concrete scoped change")
    patches = []
    for patch in data["patches"]:
        if not isinstance(patch, dict) or set(patch) != {"path", "expected_text", "replacement_text"}:
            raise PolicyError("Invalid engineer patch")
        if patch["path"] not in profile.public_profile.code_paths:
            raise PolicyError("Engineer patch is outside the registered code closure")
        patches.append(TextPatch(**patch))
    root = company.settings.research_artifact_dir / "builds" / str(row["id"]) / str(row["attempt"])
    prepared = materialize(company, profile, root, tuple(patches))
    full_files = prepared.manifest["files"]
    files = {name: full_files[name] for name in profile.public_profile.code_paths}
    if files.get(profile.public_profile.entrypoint) != profile.public_profile.entrypoint_sha256:
        raise PolicyError("Protected evaluator entrypoint changed")
    trial_id = snapshot["stage"]["trial_id"]
    plan = TrialPlan(**scoped_kwargs(spec), trial_id=trial_id, proposal_id=snapshot["stage"]["proposal_id"],
        mission_digest=snapshot["manifest_digest"], execution_profile=spec.execution_profile, implementer="engineer",
        repository=spec.code.repository, code_commit=prepared.commit, changed_paths=[patch.path for patch in patches],
        config_files={name: files[name] for name in profile.config_files}, input_files=spec.data.input_files,
        lake_id=spec.data.lake_id, development=spec.development, worker_id="worker",
        hostname="DESKTOP-5T00NAF", gpu="NVIDIA GeForce RTX 3070")
    manifest = AdaptiveManifest(id=RESEARCH_RECIPE if spec.schema_version >= 2 else ADAPTIVE_RECIPE,
        mission_id=snapshot["id"], mission_digest=snapshot["manifest_digest"],
        trial_id=trial_id, plan_digest=record_digest(plan), plan=plan, spec=spec, code_files=files,
        bundle_sha256=prepared.bundle_sha256, config_path=profile.config_path,
        company_commit=company.settings.company_code_commit)
    # A distinct trial must make a distinct experiment. Commit timestamps or renamed hypotheses
    # alone are not a new scientific configuration.
    signature = experiment_signature(prepared.worktree, files, profile.config_files)
    base = base_workspace(company, profile)
    base_files = {name: base.manifest["files"][name] for name in profile.public_profile.code_paths}
    if signature == experiment_signature(base.worktree, base_files, profile.config_files):
        raise PolicyError("This patch does not change the baseline experiment")
    for attempt in snapshot["attempts"]:
        previous_plan = attempt["plan"]
        if (str(attempt["trial_id"]) != str(trial_id)
                and previous_plan["config_files"] == plan.config_files
                and previous_plan["code_commit"] == plan.code_commit):
            raise PolicyError("This experiment already exists in the mission history")
    receipt = {"manifest": manifest.model_dump(mode="json"), "bundle_path": str(prepared.bundle_path),
               "build_manifest_sha256": prepared.manifest_sha256, "code_signature": signature,
               "engineer_task_id": str(row["task_id"])}
    atomic_json(root / "trial.json", receipt)
    return receipt


def read_stage_file(company, stage, arguments):
    if (not isinstance(arguments, dict) or not {"action", "path"} <= set(arguments) <= {"action", "path", "offset"}
            or arguments["action"] != "read_stage_file"):
        raise PolicyError("Only scoped research evidence reads are allowed")
    name = arguments["path"]
    offset = arguments.get("offset", 0)
    if not isinstance(name, str) or type(offset) is not int or offset < 0:
        raise PolicyError("Invalid research evidence offset")
    entry = stage["context"].get("_private_files", {}).get(name)
    if not entry:
        raise PolicyError("File is outside this research stage")
    path = Path(entry["path"])
    if path.is_symlink() or not path.is_file() or sha_file(path) != entry["sha256"]:
        raise PolicyError("Research evidence changed")
    # All mappings are created by the trusted controller, not accepted from a model path.
    if not path.resolve().is_relative_to(company.settings.research_artifact_dir.resolve()):
        raise PolicyError("Research evidence escapes the managed artifact store")
    content = path.read_bytes().decode("utf-8")
    if offset >= len(content) and not (offset == 0 and not content):
        raise PolicyError("Research evidence offset is past EOF")
    return {"path": name, "sha256": entry["sha256"], "offset": offset, "content": content[offset:offset+12000],
            "next_offset": offset + 12000 if len(content) > offset + 12000 else None}
