"""Deterministic candidate pre-checks (T1-T9). Shares ProgramStore rules; no model calls, no writes."""

from collections import Counter
from typing import Literal
from uuid import UUID

from ..company import PolicyError, fingerprint
from .data_evidence import require_admissible_gaps
from .library import require_current, verify_citations
from .mission_contracts import MissionModel
from .planning_contracts import (
    Estimand,
    ExecutionTiming,
    GapRequest,
    PlanningCandidate,
    TriageCheck,
    TriageResult,
    engine_card,
    engine_card_key,
    summarize,
    task_proposal,
)
from .program_contracts import ResearchProgram
from .programs import CONDITIONAL_SOURCE, DUPLICATE_TASK, MISSING_PACKET, ProgramStore, proposal_mode_error
from .scientific_lineages import LINEAGE_EXHAUSTED, exhausted, require_authority, usage


def passed(code, message=""):
    return TriageCheck(code=code, status="pass", message=message[:600])


def failed(code, outcome, message, gap=None):
    return TriageCheck(code=code, status="fail", outcome=outcome, message=str(message)[:600], gap=gap)


def skipped(code, message, status="not_applicable"):
    return TriageCheck(code=code, status=status, message=message[:600])


def gap(kind, subject, resolver, required, recheck):
    return GapRequest(kind=kind, subject=subject[:300], resolver=resolver, required_evidence=required,
                      recheck_on=recheck)


def result(checks, basis, *, bound):
    ordered = [checks[f"T{n}"] for n in range(1, 10)]
    return TriageResult(outcome=summarize(ordered, bound=bound), checks=ordered, basis=basis)


def triage(company, conn, value, *, admission, program_id=None, evidence_set_digest=None):
    candidate = PlanningCandidate.model_validate(value)
    checks, basis = {}, {"evidence_set_digest": evidence_set_digest, "triage_version": 1}
    outside = sorted(set(candidate.source_ids) - set(admission))
    if outside:
        checks["T1"] = failed("T1", "invalid", "Candidate cites sources outside the frozen evidence set: "
                              + ", ".join(outside[:5]))
    else:
        try:
            require_current(conn, candidate.source_ids)
            checks["T1"] = passed("T1")
        except PolicyError as exc:
            checks["T1"] = failed("T1", "stale", exc, gap("source_currency", "sources:" + ",".join(
                sorted(candidate.source_ids)), "employee", "Current versions of every cited original",
                "A new run on the corrected originals"))
    try:
        verify_citations(conn, candidate)
        checks["T2"] = passed("T2")
    except PolicyError as exc:
        checks["T2"] = failed("T2", "invalid", exc)
    if program_id is None or candidate.envelope is None:
        for code in ("T3", "T4", "T5", "T6", "T7", "T8", "T9"):
            checks[code] = skipped(code, "No bound program envelope; no experiment path")
        return result(checks, basis, bound=False)
    store, subject = ProgramStore(company), f"program:{program_id}/envelope:{candidate.envelope}"
    try:
        project, program, spec = store.locked(conn, program_id, active=True)
        envelope = next((e for e in spec.envelopes if e.name == candidate.envelope), None)
        if envelope is None:
            raise PolicyError("Task selects an unapproved envelope")
    except PolicyError as exc:
        checks["T3"] = failed("T3", "needs_program", exc, gap("program_missing", subject, "owner",
            "An active owner-approved program containing this envelope", "Owner approval of such a program"))
        for code in ("T4", "T5", "T6", "T7", "T8", "T9"):
            checks[code] = skipped(code, "Program authority unavailable")
        return result(checks, basis, bound=True)
    checks["T3"] = passed("T3")
    basis["program_manifest_digest"] = program["manifest_digest"]
    template = envelope.template
    error = proposal_mode_error(envelope, candidate.mode)
    if error is None and store.conditional_source(conn, candidate.source_ids) and candidate.mode != "novel_hypothesis":
        error = CONDITIONAL_SOURCE
    checks["T4"] = failed("T4", "out_of_scope", error, gap("data_policy", subject + "/mode:" + candidate.mode, "owner",
        "A program whose signed data policy admits this mode", "Owner approval of a new data policy")) \
        if error else passed("T4")
    metric = template.objective.metric
    checks["T5"] = passed("T5", metric) if candidate.estimand == metric else failed(
        "T5", "needs_evaluator", f"Candidate estimand {candidate.estimand} differs from the envelope objective {metric}",
        gap("evaluator", subject + "/estimand:" + candidate.estimand, "owner",
            "An approved envelope whose evaluator measures this estimand", "Owner approval of that evaluator"))
    if template.kind != "strategy":
        checks["T6"] = skipped("T6", "Non-strategy evaluations have no fill rule")
    elif (card := engine_card(template)) is None:
        checks["T6"] = failed("T6", "needs_evaluator", f"No verified engine rule card for {engine_card_key(template)}",
            gap("execution_timing", subject + "/engine:" + engine_card_key(template), "operator",
                "A versioned engine rule card from the frozen evaluator source", "A new engine rule card version"))
    elif candidate.execution_timing not in card.supported_execution_timings:
        checks["T6"] = failed("T6", "needs_evaluator", f"Engine {card.id} supports "
            f"{', '.join(card.supported_execution_timings)}, not {candidate.execution_timing}",
            gap("execution_timing", subject + "/timing:" + candidate.execution_timing, "owner",
                "An approved evaluator with this execution timing", "Owner approval of that evaluator"))
    else:
        checks["T6"] = passed("T6", f"{card.id}@{card.version}")
        basis["engine_card"] = f"{card.id}@{card.version}"
    try:
        if store.packet_required(envelope, exploratory=template.data.policy is not None):
            packet = store.envelope_packet(program, envelope)
            if packet is None:
                raise PolicyError(MISSING_PACKET)
            basis["packet_digest"] = fingerprint(packet.model_dump(mode="json"))
            require_admissible_gaps(packet, template)
            checks["T7"] = passed("T7")
        else:
            checks["T7"] = skipped("T7", "Fixture profile requires no packet")
    except PolicyError as exc:
        checks["T7"] = failed("T7", "needs_data", exc, gap("data_packet", subject + f"/packet:{basis.get('packet_digest')}",
            "operator", "A verified packet without blocking or unacknowledged gaps", "A new packet digest for this envelope"))
    identity, _ = store.task_identity(program_id, task_proposal(candidate).model_dump(mode="json"))
    authority = template.scientific_lineage
    if conn.execute("SELECT 1 FROM research_program_tasks WHERE id=%s", (identity,)).fetchone():
        checks["T8"] = failed("T8", "duplicate", DUPLICATE_TASK)
    else:
        try:
            require_authority(conn, project["id"], program_id, envelope)
            if authority is not None:
                row = conn.execute("SELECT * FROM research_scientific_lineages WHERE id=%s", (authority.id,)).fetchone()
                if row is None:
                    raise PolicyError("Scientific lineage is not authorized")
                basis["lineage_usage"] = usage(conn, authority.id)
                if exhausted(conn, row, authority):
                    raise PolicyError(LINEAGE_EXHAUSTED)
            checks["T8"] = passed("T8")
        except PolicyError as exc:
            checks["T8"] = failed("T8", "needs_program", exc, gap("lineage_budget",
                subject + f"/lineage:{authority.id if authority else None}", "owner",
                "A signed lineage authority with remaining trials", "Owner approval of a new lineage authority"))
    error = store.capacity_error(conn, program_id, spec)
    checks["T9"] = failed("T9", "needs_program", error, gap("program_budget", f"program:{program_id}/capacity", "owner",
        "Program budget or concurrency capacity", "A finished mission or an approved budget")) if error else passed("T9")
    return result(checks, basis, bound=True)


class ReplayPacket(MissionModel):
    envelope: str
    packet_present: bool | None = None
    blocking_gaps: list[str] | None = None
    provenance: str


class ReplayTask(MissionModel):
    id: UUID
    state: str
    proposal: dict
    data_decision: str | None = None
    selection_decision: str | None = None
    created_at: str
    declared_estimand: Estimand | None = None
    declared_execution_timing: ExecutionTiming | None = None
    declaration_provenance: str = ""


class ReplayBundle(MissionModel):
    schema_version: Literal[1] = 1
    program_id: UUID
    program_manifest_digest: str
    program: dict
    packets: list[ReplayPacket]
    tasks: list[ReplayTask]
    provenance: list[str]


def replay(value):
    """Re-judge recorded legacy tasks from recorded facts only. Unrecorded facts stay not_declared."""
    bundle = ReplayBundle.model_validate(value)
    spec = ResearchProgram.model_validate(bundle.program)
    if fingerprint(spec.model_dump(mode="json")) != bundle.program_manifest_digest:
        raise PolicyError("Replay program differs from its manifest digest")
    envelopes, packets = {e.name: e for e in spec.envelopes}, {p.envelope: p for p in bundle.packets}
    rows = []
    for task in sorted(bundle.tasks, key=lambda item: (item.created_at, str(item.id))):
        proposal, checks = task.proposal, {}
        checks["T1"] = passed("T1", "Source currency was enforced when the task was registered")
        checks["T2"] = passed("T2", "Citations were verified when the task was registered")
        envelope = envelopes.get(proposal.get("envelope"))
        if envelope is None:
            checks["T3"] = failed("T3", "needs_program", "Task selects an unapproved envelope")
            for code in ("T4", "T5", "T6", "T7", "T8", "T9"):
                checks[code] = skipped(code, "Program authority unavailable")
        else:
            template = envelope.template
            checks["T3"] = passed("T3", "Program was active when the task was registered")
            error = proposal_mode_error(envelope, proposal["mode"])
            checks["T4"] = failed("T4", "out_of_scope", error) if error else passed("T4")
            if task.declared_estimand is None:
                checks["T5"] = skipped("T5", "Legacy proposals do not declare an estimand", "not_declared")
            elif task.declared_estimand == template.objective.metric:
                checks["T5"] = passed("T5", task.declaration_provenance)
            else:
                checks["T5"] = failed("T5", "needs_evaluator", f"Declared estimand {task.declared_estimand} differs "
                                      f"from {template.objective.metric} ({task.declaration_provenance})")
            card = engine_card(template)
            if template.kind != "strategy":
                checks["T6"] = skipped("T6", "Non-strategy evaluations have no fill rule")
            elif task.declared_execution_timing is None:
                checks["T6"] = skipped("T6", "Legacy proposals do not declare execution timing", "not_declared")
            elif card is None or task.declared_execution_timing not in card.supported_execution_timings:
                checks["T6"] = failed("T6", "needs_evaluator", f"Declared {task.declared_execution_timing} is not "
                                      f"supported by {engine_card_key(template)} ({task.declaration_provenance})")
            else:
                checks["T6"] = passed("T6", task.declaration_provenance)
            fact = packets.get(envelope.name)
            if fact is None or fact.packet_present is None:
                checks["T7"] = skipped("T7", "Packet state at proposal time is not recorded", "not_declared")
            elif not fact.packet_present:
                checks["T7"] = failed("T7", "needs_data", f"{MISSING_PACKET} ({fact.provenance})")
            elif fact.blocking_gaps:
                checks["T7"] = failed("T7", "needs_data", "Data evidence packet still records blocking gaps: "
                                      + "; ".join(fact.blocking_gaps) + f" ({fact.provenance})")
            else:
                checks["T7"] = passed("T7", fact.provenance)
            checks["T8"] = skipped("T8", "Registration rejected duplicates; lineage usage then is not reconstructed",
                                   "not_declared")
            checks["T9"] = skipped("T9", "Program capacity at proposal time is not reconstructed", "not_declared")
        judged = result(checks, {"replay": 1}, bound=True)
        rows.append({"task_id": str(task.id), "title": str(proposal.get("title", ""))[:200], "actual_state": task.state,
                     "data_decision": task.data_decision, "selection_decision": task.selection_decision,
                     "replay_outcome": judged.outcome, "stopped_before_data_stage": judged.outcome != "eligible",
                     "checks": [check.model_dump(mode="json") for check in judged.checks]})
    stopped = [row for row in rows if row["stopped_before_data_stage"]]
    return {"schema_version": 1, "program_id": str(bundle.program_id), "manifest_digest": bundle.program_manifest_digest,
            "summary": {"tasks": len(rows), "stopped_before_data_stage": len(stopped),
                        "actual_data_blocked": sum(row["data_decision"] == "blocked" for row in rows),
                        "blocked_and_stopped": sum(row["data_decision"] == "blocked" for row in stopped),
                        "by_outcome": dict(sorted(Counter(row["replay_outcome"] for row in rows).items()))},
            "tasks": rows, "provenance": bundle.provenance,
            "scope": "Deterministic replay of recorded facts. Undeclared or unrecorded facts are not inferred."}
