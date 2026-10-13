"""Deterministic candidate pre-checks (T1-T9). Shares ProgramStore rules; no model calls, no writes."""

from ..company import PolicyError, fingerprint
from .data_evidence import require_admissible_gaps
from .library import require_current, verify_citations
from .planning_contracts import (
    GapRequest,
    PlanningCandidate,
    TriageCheck,
    TriageResult,
    engine_card,
    engine_card_key,
    summarize,
    task_proposal,
)
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
