"""Service-owned output shapes and time budgets for the Analyst lane only."""

import re
from datetime import timedelta
from time import time as wall_time

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

EXECUTION_VERSION = 7
OUTPUT_MODELS = {
    "brief_plan_v1": SourcePlan,
    "brief_inventory_v1": FactInventory,
    "brief_inventory_v2": FragmentFactInventory,
    "brief_compose_v1": BriefComposition,
    "brief_write_v1": BriefProposal,
    "brief_review_v1": BriefReview,
    "brief_review_v2": BriefReview,
    "brief_conditions_v1": ConditionPatch,
    "brief_facts_v1": MaterialFactPatch,
    "brief_editorial_v1": EditorialPatch,
    "brief_editorial_v2": EditorialPatch,
    "brief_source_notes_v1": SourceNotesPatch,
}
# Legacy requests retain the original phase budgets. Versioned contracts below
# reserve longer slots for full-original inventory, composition and correction.
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
# A retried phase (one per phase, see store.MAX_PHASE_ATTEMPTS) carries a -r<attempt> suffix.
IDENTITY = re.compile(r"^news-brief-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-"
                      r"(plan|inventory|write|review|revise|final_review)(?:-r[1-9])?$")


def output_contract(phase, bundle):
    if phase == "search":
        return "agent_decision"
    if phase in {"review", "final_review"}:
        return "brief_review_v2" if bundle.get('combined_editorial_repair') else "brief_review_v1"
    if phase == "plan":
        return "brief_plan_v1"
    if phase == "inventory":
        return "brief_inventory_v2" if bundle.get("fact_inventory_version") == 2 else "brief_inventory_v1"
    mode = bundle.get("revision_feedback", {}).get("repair_mode")
    if mode == "editorial_patch" and bundle.get("editorial_patch_version") == 2:
        return "brief_editorial_v2"
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
            contract in {"brief_review_v1", "brief_review_v2"} if phase in {"review", "final_review"} else
            contract in {"brief_write_v1", "brief_compose_v1"} if phase == "write" else
            contract not in {"brief_plan_v1", "brief_inventory_v1", "brief_inventory_v2", "brief_review_v1", "brief_review_v2"})


def timeout_seconds(request):
    # Legacy requests keep their original generic timeout and digest.
    if valid_request(request.request_id, request.output_contract):
        if deadline := getattr(request, 'brief_deadline_unix', None):
            remaining = deadline-wall_time()
            if remaining <= 0:
                from ..contracts import ProviderFault

                raise ProviderFault('timeout', 'The frozen briefing deadline has passed; do not replay it.')
            return remaining
        if request.output_contract == "brief_review_v2":
            return PHASE_SECONDS['review']
        if request.output_contract == "brief_editorial_v2":
            return COMPOSE_SECONDS
        if request.output_contract == "brief_inventory_v2":
            return FRAGMENT_INVENTORY_SECONDS
        if request.output_contract == "brief_compose_v1" and IDENTITY.fullmatch(request.request_id)[2] == "write":
            return COMPOSE_SECONDS
        return PHASE_SECONDS[IDENTITY.fullmatch(request.request_id)[2]]
    return None


def remaining_seconds(phase, bundle):
    if bundle.get('combined_editorial_repair'):
        # A second independent full-original review is the same workload as the
        # first. Reserve the one correction and both full reviews for new editions.
        correction = COMPOSE_SECONDS if bundle.get('editorial_patch_version') == 2 else PHASE_SECONDS['revise']
        repair = correction + PHASE_SECONDS['review']
        if phase == 'revise':
            return repair
        if phase == 'final_review':
            return PHASE_SECONDS['review']
        return remaining_seconds(phase, {**bundle, 'combined_editorial_repair': False}) + repair
    if phase == "revise" and bundle.get("source_notes_repair", {}).get("review_phase") == "review":
        return PHASE_SECONDS["revise"] + PHASE_SECONDS["review"]
    if bundle.get("fact_inventory_version") == 2 and phase in {"plan", "inventory"}:
        return (PHASE_SECONDS["plan"] if phase == "plan" else 0) + FRAGMENT_INVENTORY_SECONDS + COMPOSE_SECONDS + PHASE_SECONDS["review"]
    if phase == "write" and bundle.get("fact_inventory_required"):
        return COMPOSE_SECONDS + PHASE_SECONDS["review"]
    return REMAINING_SECONDS[phase]


def phase_deadline(phase, bundle, due_at):
    """Share the edition window, reserving downstream review and delivery time.

    The former phase seconds are planning reserves, not per-call kill timers.
    A fast phase gives the next phase its unused time. The absolute deadline is
    frozen with the request so queueing or recovery cannot restart its clock.
    """
    if bundle.get('deadline_budget_version') != 1 or phase == 'search':
        return None
    next_phase = {'plan':'inventory', 'inventory':'write', 'write':'review',
                  'review':'revise', 'revise':'final_review', 'final_review':None}[phase]
    downstream = remaining_seconds(next_phase, bundle) if next_phase else 0
    return due_at+timedelta(minutes=10)-timedelta(seconds=downstream+60)


def has_runway(phase, bundle, at, due_at):
    deadline = phase_deadline(phase, bundle, due_at)
    return at < deadline if deadline else at+timedelta(seconds=remaining_seconds(phase, bundle)+60) < due_at+timedelta(minutes=10)


def direct_instruction(text):
    """Drop the obsolete action envelope without changing any editorial rules."""
    return re.sub(r"Return (?:AgentDecision\(status=complete,say=''\) with |one )[^\n]*\n"
                  r"(?:envelope source_ids=\[\]\. |source_ids=\[\]\. |and\nsource_ids=\[\]\. )?",
                  "Return the requested JSON object directly, matching the output schema.\n", text, count=1)
