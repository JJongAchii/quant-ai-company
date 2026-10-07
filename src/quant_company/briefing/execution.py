"""Service-owned output shapes and time budgets for the Analyst lane only."""

import re

from .contracts import (
    BriefProposal,
    BriefReview,
    ConditionPatch,
    EditorialPatch,
    MaterialFactPatch,
    SourceNotesPatch,
    SourcePlan,
)

EXECUTION_VERSION = 1
OUTPUT_MODELS = {
    "brief_plan_v1": SourcePlan,
    "brief_write_v1": BriefProposal,
    "brief_review_v1": BriefReview,
    "brief_conditions_v1": ConditionPatch,
    "brief_facts_v1": MaterialFactPatch,
    "brief_editorial_v1": EditorialPatch,
    "brief_source_notes_v1": SourceNotesPatch,
}
# First-pass worst case: 6 + 24 + 12 = 42 minutes, within the 45-minute slot.
# The one confirmed correction and its final review use at most another 12.
PHASE_SECONDS = {"plan": 360, "write": 1440, "review": 720,
                 "revise": 360, "final_review": 360}
REMAINING_SECONDS = {"plan": 2520, "write": 2160, "review": 720,
                     "revise": 720, "final_review": 360}
IDENTITY = re.compile(r"^news-brief-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-"
                      r"(plan|write|review|revise|final_review)$")


def output_contract(phase, bundle):
    if phase == "search":
        return "agent_decision"
    if phase in {"review", "final_review"}:
        return "brief_review_v1"
    if phase == "plan":
        return "brief_plan_v1"
    mode = bundle.get("revision_feedback", {}).get("repair_mode")
    return {"conditions_only": "brief_conditions_v1", "material_append": "brief_facts_v1",
            "editorial_patch": "brief_editorial_v1", "source_notes_patch": "brief_source_notes_v1"}.get(
                mode, "brief_write_v1")


def valid_request(identity, contract):
    match = IDENTITY.fullmatch(identity)
    if not match or contract not in OUTPUT_MODELS:
        return False
    phase = match[2]
    return (contract == "brief_plan_v1" if phase == "plan" else
            contract == "brief_review_v1" if phase in {"review", "final_review"} else
            contract == "brief_write_v1" if phase == "write" else
            contract not in {"brief_plan_v1", "brief_review_v1"})


def timeout_seconds(request):
    # Legacy requests keep their original generic timeout and digest.
    if valid_request(request.request_id, request.output_contract):
        return PHASE_SECONDS[IDENTITY.fullmatch(request.request_id)[2]]
    return None


def direct_instruction(text):
    """Drop the obsolete action envelope without changing any editorial rules."""
    return re.sub(r"Return (?:AgentDecision\(status=complete,say=''\) with |one )[^\n]*\n"
                  r"(?:envelope source_ids=\[\]\. |source_ids=\[\]\. |and\nsource_ids=\[\]\. )?",
                  "Return the requested JSON object directly, matching the output schema.\n", text, count=1)
