"""Read-only saved-input replay and five completed KRX trading days of content evidence."""

from datetime import timedelta

from ..company import as_json
from . import schedule
from .contracts import BriefProposal, BriefReview, SourcePlan
from .editor import FORMAT_VERSION, render, validate, validate_review, validate_source_notes
from .planning import apply_plan


def replay(row):
    try:
        if not row["quality"] or row["quality"].get("format_version") != FORMAT_VERSION:
            return {"ok": False, "reason": "unqualified_format_version"}
        if row["bundle"].get("candidate_documents"):
            selected = apply_plan(row["bundle"], SourcePlan.model_validate(row["bundle"].get("source_plan")))
            if selected["documents"] != row["bundle"]["documents"]:
                return {"ok": False, "reason": "source_selection_changed"}
        proposal = BriefProposal.model_validate(row["proposal"]) if row["proposal"] else None
        review = BriefReview.model_validate(row["review"]) if row["review"] else None
        if proposal and review:
            validate_source_notes(proposal, row['bundle'])
            validate_review(review, proposal, row["bundle"])
        rejected = validate(proposal, row["bundle"]) if proposal else {}
        parts, _ = render(proposal, row["bundle"], fallback=row["quality"].get("fallback"),
                          rejected=row["quality"].get("rejected"), review_reduced=bool(review and review.verdict == "reduce"))
        ok = not rejected and parts == row["rendered"]
        return {"ok": ok, "reason": None if ok else "saved_input_or_render_mismatch"}
    except (ValueError, KeyError, TypeError):
        return {"ok": False, "reason": "saved_input_contract_rejected"}


def qualify(company, *, at=None):
    from .store import BriefStore

    at = at or schedule.utcnow()
    s = company.settings
    current_policy = BriefStore(company).policy()
    changes = schedule.overrides(s)
    days, expected = [], []
    for offset in range(40):
        day = at.astimezone(schedule.KST).date()-timedelta(days=offset)
        definitions = schedule.editions(day, s.briefing_channel_id, s.briefing_owner_user, changes)
        pm = next((e for e in definitions if e.kind == "pm"), None)
        if pm and pm.due_at+timedelta(minutes=10) <= at:
            days.append(day)
            expected.extend(definitions)
            if len(days) == 5:
                break
    with company.db.transaction() as conn:
        rows = conn.execute("SELECT * FROM brief_editions WHERE channel=%s AND owner_user=%s AND day=ANY(%s)",
                            (s.briefing_channel_id, s.briefing_owner_user, days)).fetchall()
        calls = conn.execute("""SELECT c.edition_id,c.phase,c.state,c.response->>'provider' AS provider
            FROM brief_calls c JOIN brief_editions e ON e.id=c.edition_id
            WHERE e.channel=%s AND e.owner_user=%s AND e.day=ANY(%s)""",
                             (s.briefing_channel_id, s.briefing_owner_user, days)).fetchall()
    by_id = {str(row["id"]): row for row in rows}
    checks = []
    for definition in expected:
        row = by_id.get(definition.id)
        reasons = []
        observed = {"due_at": as_json(definition.due_at), "committed_at": None,
                    "delay_seconds": None, "core_quote_assurance": {}, "missing_core": [],
                    "collection_error_count": 0, "data_diagnostics": [], "missing_source_topics": []}
        if not row or not row["committed_at"]:
            reasons.append("edition_not_committed")
        else:
            quality = row["quality"] or {}
            observed.update({
                "committed_at": as_json(row["committed_at"]),
                "delay_seconds": round((row["committed_at"]-definition.due_at).total_seconds(), 1),
                "core_quote_assurance": {key: quality.get("assurance", {}).get(key)
                                         for key in (["sp500", "nasdaq"] if definition.kind == "am"
                                                     and definition.us_session else ["kospi", "kosdaq"]
                                                     if definition.kind == "pm" else [])},
                "missing_core": quality.get("missing_core", []),
                "collection_error_count": len((row["bundle"] or {}).get("collection_errors", [])),
                "data_diagnostics": quality.get("data_diagnostics", []),
                "missing_source_topics": (row["bundle"] or {}).get("source_coverage", {}).get("missing_topics", []),
            })
            if row["committed_at"] > definition.due_at+timedelta(minutes=10):
                reasons.append("late_brief")
            if row["policy_digest"] != current_policy:
                reasons.append("editorial_policy_changed")
            result = replay(row)
            if not result["ok"]:
                reasons.append(result["reason"])
            if quality.get("reduced") or quality.get("quote_conflicts"):
                reasons.append("reduced_or_conflicting_coverage")
            if (not quality.get('substantive') or quality.get('rejected')
                    or (row['review'] or {}).get('verdict') != 'publish'
                    or not all((row['review'] or {}).get('checks', {}).values())
                    or len((row['review'] or {}).get('checks', {})) != 12):
                reasons.append("content_quality_not_verified")
            core = ["sp500", "nasdaq"] if definition.kind == "am" and definition.us_session else (
                ["kospi", "kosdaq"] if definition.kind == "pm" else [])
            if any(quality.get("assurance", {}).get(k) not in {"collected", "corroborated"} for k in core):
                reasons.append("core_quotes_not_collected_or_corroborated")
            edition_calls = [c for c in calls if str(c["edition_id"]) == definition.id]
            real_phases = {c["phase"] for c in edition_calls
                           if c["state"] == "completed" and c["provider"] == "codex"}
            required_phases = {"write", "review"}
            if (row["bundle"] or {}).get("candidate_documents"):
                required_phases.add("plan")
                if not (row["bundle"] or {}).get("source_plan"):
                    reasons.append("source_selection_not_reviewed")
            if quality.get("revision_used") or any(c["phase"] in {"revise", "final_review"} for c in edition_calls):
                required_phases |= {"revise", "final_review"}
                if (row['bundle'] or {}).get('source_notes_repair', {}).get('before_independent_review'):
                    required_phases.discard('review')
            if not required_phases <= real_phases:
                reasons.append("real_codex_not_verified")
            if s.fixture_mode or any(d.get("receipt", {}).get("synthetic") for d in row["bundle"].get("documents", [])):
                reasons.append("synthetic_input")
            if any(d.get("kind") == "dataset" and len(d.get("receipt", {}).get("qdata_code_commit") or "") != 40
                   for d in row["bundle"].get("documents", [])):
                reasons.append("unqualified_data_reader")
        checks.append({"day": definition.day, "kind": definition.kind, "id": definition.id,
                       "passed": not reasons, "reasons": reasons, "observed": observed})
    return as_json({"checked_at": at, "completed_trading_days": days, "required_days": 5,
        "editions": checks, "ready_for_slack_acceptance": len(days) == 5 and bool(checks) and all(c["passed"] for c in checks),
        "publishing_changed": False, "slack_acceptance": "separate real delivery verification required",
        "note": "Saved-input replay is not a fresh inference or proof of independent editorial correctness."})
