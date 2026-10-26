"""Repair retries (owner rule, 2026-10-12): "재시도 시 잘못된 부분만 올바르게 잘 쓰면 되는거 아니야. 그럼 만료시각까지
쓰다가 그때까지의 글 기준으로 그냥 하는걸로 해."

A completed phase whose draft fails a server check is asked again, as long as the edition's final time leaves room
(execution.final_at). When the failure can be fixed from the draft itself, the retry carries that draft and the exact
rule it broke and asks for only that fix; otherwise it repeats the phase prompt with a note naming the check. At the
final time the latest accepted text ships (store.flush).
"""
import json
import re

from pydantic import ValidationError

from .inventory import facts, qualifier_text

PROMPT_LIMIT = 90000  # ProviderRequest.prompt
DRAFT_LIMIT = 80000
SCHEMA = "brief_output_schema"
EMPTY_DRAFT = "brief_draft_empty"
# A write or revise draft without these has no body to keep (2026-10-26 PM: only the calendar was filled; two in-place
# repairs carried the empty body into the edition), so the retry is the full phase again.
BODY = ("summary", "overview", "observations", "issues")
RULES = {
    EMPTY_DRAFT: "the previous draft left summary, overview, observations and issues empty. Write the complete "
                 "briefing from the inputs above.",
    "inventory_placement_accounting": "fact_placements must hold exactly one entry for every committed fact ID "
                                      "(none missing, none repeated, none invented).",
    "inventory_supplement_accounting": "supplemental_source_notes must hold exactly one note for every new source "
                                       "listed below and for no other source.",
    "inventory_qualifier_not_in_fact": "every qualifier of a material fact must appear verbatim in that fact's text.",
    SCHEMA: "the draft must match the output schema exactly; the failing locations are listed below.",
}
GENERIC = "Follow the output schema exactly and keep every required list complete."
# Failures a model can fix from its own draft plus the supplied detail. Others need the full phase context.
DRAFT_LOCAL = {"write": {"inventory_placement_accounting", "inventory_supplement_accounting", SCHEMA},
               "revise": {"inventory_placement_accounting", "inventory_supplement_accounting", SCHEMA},
               "inventory": {"inventory_qualifier_not_in_fact", SCHEMA},
               "plan": {SCHEMA}, "review": {SCHEMA}, "final_review": {SCHEMA}}
REPAIR = """You are Analyst repairing your previous complete draft of this phase after a server check rejected it.
Change only what the check requires. Keep every other field, sentence, number, citation, quote and ID exactly as in
the previous draft. Return the complete corrected JSON object in the same output schema, not a patch or a summary."""


def retry_note(label):
    return (f"\n\nRETRY: the previous draft of this phase was rejected by the server check '{label}'. "
            f"{RULES.get(label, GENERIC)} Return the complete JSON object again; do not shorten other parts.")


def hollow(phase, draft):
    if phase not in {"write", "revise"} or not draft:
        return False
    try:
        value = json.loads(draft, strict=False)
    except ValueError:
        return False
    return isinstance(value, dict) and not any(value.get(key) for key in BODY)


def failure(exc, response):
    """(label, draft, detail) for a rejected response. Only fixed server-check labels and schema locations travel;
    the draft is the model's own output."""
    detail = None
    if isinstance(exc, ValidationError):
        label = SCHEMA
        detail = [{"loc": ".".join(map(str, e.get("loc", ()))), "type": e.get("type")} for e in exc.errors()[:20]]
    else:
        label = str(exc) if re.fullmatch(r"[a-z][a-z0-9_]{2,63}", str(exc)) else None
    try:
        draft = response.decision.artifacts[0].content
    except (AttributeError, IndexError, TypeError):
        draft = None
    if not isinstance(draft, str) or len(draft) > DRAFT_LIMIT:
        draft = None
    return label, draft, detail


def placement_detail(bundle, draft):
    _, indexed = facts(bundle)
    placements = [p.get("fact_id") for p in json.loads(draft, strict=False).get("fact_placements", [])
                  if isinstance(p, dict)]
    seen = set(placements)
    missing = [key for key in indexed if key not in seen]
    return {"all_fact_ids": list(indexed), "missing": missing,
            "repeated": sorted({p for p in placements if placements.count(p) > 1}, key=str),
            "unknown": sorted(seen - indexed.keys(), key=str),
            "missing_facts": [{"id": key, "source_id": indexed[key][0].source_id, "fact": indexed[key][2].fact,
                               "quote": indexed[key][2].quote,
                               "qualifiers": [qualifier_text(q) for q in indexed[key][2].qualifiers]}
                              for key in missing]}


def supplement_detail(bundle, draft):
    inventory, _ = facts(bundle)
    committed = {s.source_id for s in inventory.sources}
    expected = sorted({d["id"] for d in bundle["documents"] if d["kind"] not in {"calendar", "dataset"}} - committed)
    given = [s.get("source_id") for s in json.loads(draft, strict=False).get("supplemental_source_notes", [])
             if isinstance(s, dict)]
    return {"new_source_ids": expected, "given": given}


def repair_prompt(bundle, phase, draft, label, detail=None):
    """The previous draft and the check it failed, or None when the failure needs the full phase context or the
    request would not fit; the caller then repeats the phase prompt with retry_note."""
    if not draft or label not in DRAFT_LOCAL.get(phase, ()) or hollow(phase, draft):
        return None
    lines = [REPAIR, f"\nREJECTED BY: {label}", f"RULE: {RULES[label]}"]
    try:
        if label == "inventory_placement_accounting":
            lines.append("Map each missing fact to the main item that already states it. If no item states it, add "
                         "the fact with its numbers and exact qualifier phrases to the most relevant existing main "
                         "item, citing its source with the given quote, and map it there. Merge a repeated entry into "
                         "one. Remove entries for unknown IDs.")
            detail = placement_detail(bundle, draft)
        elif label == "inventory_supplement_accounting":
            detail = supplement_detail(bundle, draft)
    except (ValueError, KeyError, AttributeError, TypeError):
        return None
    if detail is not None:
        lines.append("DETAIL: " + json.dumps(detail, ensure_ascii=False))
    lines.append("\nPREVIOUS_DRAFT:\n" + draft)
    text = "\n".join(lines)
    return text if len(text) <= PROMPT_LIMIT else None
