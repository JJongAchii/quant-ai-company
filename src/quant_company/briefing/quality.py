"""Reconcile identical instruments/sessions without averaging conflicting sources."""

from decimal import Decimal

from .contracts import BriefProposal, MarketObservation, SourceDocument


def reconcile(proposal, bundle):
    groups = {}
    for item in proposal.observations:
        groups.setdefault(item.instrument, []).append(item)
    locked = {v["instrument"]: MarketObservation.model_validate(v) for v in bundle.get("locked_observations", [])}
    selected, conflicts = [], []
    for instrument in sorted(set(groups) | set(locked)):
        offered = groups.get(instrument, [])
        authoritative = locked.get(instrument)
        if authoritative:
            offered = [authoritative, *[v for v in offered if v.id != authoritative.id]]
        offered.sort(key=lambda v: (v.basis == "close", v.as_of), reverse=True)
        target = authoritative or offered[0]
        comparable = [v for v in offered if v.basis == target.basis and v.session_date == target.session_date
                      and (v.basis == "close" or v.as_of == target.as_of)]
        tolerance = Decimal("0.01") if target.unit == "pt" else Decimal("0.001")
        disagreement = [v for v in comparable if abs(v.value-target.value) > tolerance]
        if disagreement:
            conflicts.append({"instrument": instrument, "session": str(target.session_date),
                "resolution": "collected_data_retained" if authoritative else "withheld",
                "values": [{"id": v.id, "value": str(v.value),
                            "sources": [e.source_id for e in v.evidence]} for v in comparable]})
            if not authoritative:
                continue
        if not authoritative and len(comparable) > 1:
            # Keep independent corroboration attached to the chosen value for the reviewer.
            proof = {e.source_id: e for v in comparable for e in v.evidence}
            target = target.model_copy(update={"evidence": list(proof.values())[:4]})
        selected.append(target.model_dump(mode="json"))
    raw = proposal.model_dump(mode="json")
    raw["observations"] = selected
    removed = {item.id for item in proposal.observations} - {item["id"] for item in selected}
    for note in raw["source_notes"]:
        note["item_ids"] = [identity for identity in note["item_ids"] if identity not in removed]
        for fact in note["material_facts"]:
            fact["main_item_ids"] = [identity for identity in fact["main_item_ids"] if identity not in removed]
    return BriefProposal.model_validate(raw), conflicts


def assurance(proposal, bundle):
    docs = {d["id"]: SourceDocument.model_validate(d) for d in bundle.get("documents", [])}
    result = {}
    for item in proposal.observations if proposal else []:
        sources = [docs[e.source_id] for e in item.evidence]
        origins = {d.origin_group or d.publisher for d in sources}
        result[item.instrument] = ("collected" if any(d.kind == "dataset" for d in sources)
                                   else "corroborated" if len(origins) >= 2 else "single_source")
    return result
