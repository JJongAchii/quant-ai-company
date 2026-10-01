"""Operator-provisioned, hash-bound data evidence for approved research programs."""

import hashlib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, ValidationError, model_validator

from ..company import PolicyError, fingerprint
from .adaptive_contracts import digest_model
from .builds import profile_for
from .contracts import Digest
from .mission_contracts import MissionModel, Text, relative_path
from .policy_contracts import SCOPE_GAPS, ScopedRecord, require_scope


class EvidenceFile(MissionModel):
    path: Path
    sha256: Digest


class DataEvidenceGap(MissionModel):
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")]
    description: Text
    report_names: Annotated[list[str], Field(min_length=1, max_length=12)]
    input_files: Annotated[dict[str, Digest], Field(min_length=1)]


class DataEvidencePacket(ScopedRecord):
    schema_version: Literal[1, 2] = 1
    program_digest: Digest
    envelope: str
    execution_profile_digest: Digest
    lake_id: str
    input_files: dict[str, EvidenceFile]
    engine: EvidenceFile
    reports: Annotated[dict[str, EvidenceFile], Field(min_length=1, max_length=12)]
    blocking_gaps: Annotated[list[Text], Field(max_length=20)] = []
    gaps: Annotated[list[DataEvidenceGap] | None, Field(max_length=20)] = Field(
        default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def gap_version(self):
        if (self.schema_version == 2) != (self.gaps is not None):
            raise ValueError("Typed data gaps require packet version 2")
        if self.gaps is not None:
            if len({gap.code for gap in self.gaps}) != len(self.gaps):
                raise ValueError("Duplicate evidence gap code")
            inputs = {name: item.sha256 for name, item in self.input_files.items()}
            for gap in self.gaps:
                if gap.input_files != inputs or not set(gap.report_names) <= self.reports.keys():
                    raise ValueError("Evidence gap must cite verified reports and exact input hashes")
        return self


class DataEvidenceRegistry(MissionModel):
    schema_version: Literal[1] = 1
    packets: Annotated[list[DataEvidencePacket], Field(max_length=24)]


def _bytes(root: Path, entry: EvidenceFile, *, limit: int) -> bytes:
    path = entry.path
    if (not path.is_absolute() or path.is_symlink() or not path.is_file()
            or not path.resolve().is_relative_to(root) or path.stat().st_size > limit):
        raise PolicyError("Data evidence file is outside the managed store or exceeds its limit")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != entry.sha256:
        raise PolicyError("Data evidence file changed")
    try:
        data.decode("utf-8")
    except UnicodeError as exc:
        raise PolicyError("Data evidence file is not UTF-8 text") from exc
    return data


def load_packets(company, program, envelopes):
    """Validate operator files against the exact approved program; never infer readiness."""
    path = company.settings.research_data_evidence_file
    if path is None:
        return {}
    root = (company.settings.research_artifact_dir / "provisioned" / "data-evidence").resolve()
    if (not path.is_absolute() or path.is_symlink() or not path.is_file()
            or not path.resolve().is_relative_to(root) or path.stat().st_size > 1_000_000):
        raise PolicyError("Research data evidence registry is unavailable")
    try:
        registry = DataEvidenceRegistry.model_validate_json(path.read_bytes())
    except (ValidationError, ValueError) as exc:
        raise PolicyError("Research data evidence registry is invalid") from exc
    result = {}
    profiles = {}
    contents = {}

    def checked(entry, limit):
        key = (entry.path, entry.sha256, limit)
        if key not in contents:
            contents[key] = _bytes(root, entry, limit=limit)
        return contents[key]

    for packet in registry.packets:
        if packet.program_digest != program["manifest_digest"] or packet.envelope not in envelopes:
            continue
        if packet.envelope in result:
            raise PolicyError("Duplicate data evidence packet for an envelope")
        envelope = envelopes[packet.envelope]
        spec = envelope.template
        try:
            require_scope(spec, packet)
        except ValueError as exc:
            raise PolicyError(str(exc)) from exc
        profile_key = (spec.execution_profile, spec.execution_profile_digest, spec.code.base_commit,
                       tuple(spec.code.write_paths), tuple(sorted(spec.data.input_files)))
        if profile_key not in profiles:
            profiles[profile_key] = profile_for(company, spec)
        profile = profiles[profile_key]
        if (packet.execution_profile_digest != digest_model(profile.public_profile)
                or packet.lake_id != spec.data.lake_id
                or {name: item.sha256 for name, item in packet.input_files.items()} != spec.data.input_files
                or packet.engine.sha256 != profile.public_profile.entrypoint_sha256):
            raise PolicyError("Data evidence packet differs from the approved program")
        try:
            for name in (*packet.input_files, *packet.reports):
                relative_path(name)
        except ValueError as exc:
            raise PolicyError("Data evidence file name is invalid") from exc
        if any("/" in name for name in packet.reports):
            raise PolicyError("Data evidence report names must be flat")
        files = {"engine": checked(packet.engine, limit=1_000_000)}
        files.update({"inputs/" + name: checked(item, limit=32_000_000)
                      for name, item in packet.input_files.items()})
        files.update({"reports/" + name: checked(item, limit=1_000_000)
                      for name, item in packet.reports.items()})
        if spec.data_policy is not None:
            for ref in spec.data_policy.evidence_refs:
                report = packet.reports.get(ref.name)
                if report is None or report.sha256 != ref.sha256:
                    raise PolicyError("Data policy evidence differs from the verified packet")
        result[packet.envelope] = (packet, files)
    return result


def attach_packet(backend, directory, packet, files):
    """Copy exact checked bytes to immutable stage files and return the read index."""
    packet_digest = fingerprint(packet.model_dump(mode="json"))
    root = directory / "data-evidence" / packet_digest
    identity = {
        "schema_version": 1, "program_digest": packet.program_digest,
        "envelope": packet.envelope, "packet_digest": packet_digest,
        "execution_profile_digest": packet.execution_profile_digest,
        "lake_id": packet.lake_id,
        "blocking_gaps": packet.blocking_gaps,
        "verified_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
        "scope": "File identity and approved-contract binding only; source claims and data readiness need review.",
    }
    if packet.research_scope is not None:
        identity.update(schema_version=2, research_scope=packet.research_scope.model_dump(mode="json"),
                        gaps=[gap.model_dump(mode="json") for gap in packet.gaps])
    mappings = {"identity.json": backend._entry(backend._blob(root, "identity", identity))}
    for name, data in files.items():
        mappings[name] = backend._entry(backend._file(root / (hashlib.sha256(data).hexdigest() + ".txt"), data))
    required = ["identity.json", "engine", *("reports/" + name for name in sorted(packet.reports))]
    return mappings, required, packet_digest


def require_admissible_gaps(packet, spec):
    """Only owner-signed, evidence-bound scope codes can remain in a conditional packet."""
    if packet.blocking_gaps:
        raise PolicyError("Data evidence packet still records blocking gaps")
    if spec.data_policy is None:
        return
    codes = {gap.code for gap in packet.gaps}
    allowed = set(spec.data_policy.acknowledged_gap_codes)
    if not codes <= SCOPE_GAPS or codes != allowed:
        raise PolicyError("Unclassified or unresolved typed data evidence gap")
    refs = {ref.name for ref in spec.data_policy.evidence_refs}
    if any(not set(gap.report_names) <= refs for gap in packet.gaps):
        raise PolicyError("Scope limitations require owner-bound evidence references")
