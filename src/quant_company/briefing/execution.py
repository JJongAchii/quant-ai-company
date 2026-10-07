"""Service-owned output shapes and time budgets for the Analyst lane only."""

import re

from .contracts import (
    BriefComposition,
    BriefProposal,
    BriefReview,
    ConditionPatch,
    EditorialPatch,
    FactInventory,
    FragmentFactInventory,
    MaterialFactPatch,
    SourceNotesPatch,
    SourcePlan,
)

EXECUTION_VERSION = 3
OUTPUT_MODELS = {
    "brief_plan_v1": SourcePlan,
    "brief_inventory_v1": FactInventory,
    "brief_inventory_v2": FragmentFactInventory,
    "brief_compose_v1": BriefComposition,
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
                 "inventory": 480, "revise": 360, "final_review": 360}
# Version1 splits the24-minute writing slot into8 minutes of inventory and
#16 minutes of composition. Frozen requests keep their original budgets.
COMPOSE_SECONDS = 960
# Measured full-original inventory took884 seconds. Version2 has a bounded
#18-minute slot, reserved with composition/review inside the existing deadline.
FRAGMENT_INVENTORY_SECONDS = 1080
REMAINING_SECONDS = {"plan": 2520, "write": 2160, "review": 720,
                     "inventory": 2160, "revise": 720, "final_review": 360}
IDENTITY = re.compile(r"^news-brief-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-"
                      r"(plan|inventory|write|review|revise|final_review)$")


def output_contract(phase, bundle):
    if phase == "search":
        return "agent_decision"
    if phase in {"review", "final_review"}:
        return "brief_review_v1"
    if phase == "plan":
        return "brief_plan_v1"
    if phase == "inventory":
        return "brief_inventory_v2" if bundle.get("fact_inventory_version") == 2 else "brief_inventory_v1"
    mode = bundle.get("revision_feedback", {}).get("repair_mode")
    return {"conditions_only": "brief_conditions_v1", "material_append": "brief_facts_v1",
            "editorial_patch": "brief_editorial_v1", "source_notes_patch": "brief_source_notes_v1"}.get(
        mode, "brief_compose_v1" if bundle.get("fact_inventory_required") else "brief_write_v1")


def valid_request(identity, contract):
    match = IDENTITY.fullmatch(identity)
    if not match or contract not in OUTPUT_MODELS:
        return False
    phase = match[2]
    return (contract == "brief_plan_v1" if phase == "plan" else
            contract in {"brief_inventory_v1", "brief_inventory_v2"} if phase == "inventory" else
            contract == "brief_review_v1" if phase in {"review", "final_review"} else
            contract in {"brief_write_v1", "brief_compose_v1"} if phase == "write" else
            contract not in {"brief_plan_v1", "brief_inventory_v1", "brief_inventory_v2", "brief_review_v1"})


def timeout_seconds(request):
    # Legacy requests keep their original generic timeout and digest.
    if valid_request(request.request_id, request.output_contract):
        if request.output_contract == "brief_inventory_v2":
            return FRAGMENT_INVENTORY_SECONDS
        if request.output_contract == "brief_compose_v1" and IDENTITY.fullmatch(request.request_id)[2] == "write":
            return COMPOSE_SECONDS
        return PHASE_SECONDS[IDENTITY.fullmatch(request.request_id)[2]]
    return None


def remaining_seconds(phase, bundle):
    if bundle.get("fact_inventory_version") == 2 and phase in {"plan", "inventory"}:
        return (PHASE_SECONDS["plan"] if phase == "plan" else 0) + FRAGMENT_INVENTORY_SECONDS + COMPOSE_SECONDS + PHASE_SECONDS["review"]
    if phase == "write" and bundle.get("fact_inventory_required"):
        return COMPOSE_SECONDS + PHASE_SECONDS["review"]
    return REMAINING_SECONDS[phase]


def direct_instruction(text):
    """Drop the obsolete action envelope without changing any editorial rules."""
    return re.sub(r"Return (?:AgentDecision\(status=complete,say=''\) with |one )[^\n]*\n"
                  r"(?:envelope source_ids=\[\]\. |source_ids=\[\]\. |and\nsource_ids=\[\]\. )?",
                  "Return the requested JSON object directly, matching the output schema.\n", text, count=1)
