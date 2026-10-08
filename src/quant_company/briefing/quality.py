"""Reconcile identical instruments/sessions without averaging conflicting sources."""

import re
from datetime import datetime
from decimal import Decimal

from .contracts import BriefProposal, Evidence, MarketObservation, SourceDocument
from .data_reader import US_CLOSE_REGISTRATION
from .numeric import numbers

# Names an article sentence must carry for its exact close to match a locked snapshot value.
# VIX has none: a short value can recur by chance, so it locks only beside a writer's article observation.
SNAPSHOT_NAMES = {"sp500": ("s&p 500", "s&p500", "에스앤피"), "nasdaq": ("nasdaq", "나스닥"),
                  "dow": ("dow jones", "다우"), "sox": ("philadelphia semiconductor", "필라델피아 반도체")}


def tolerance(item):
    return Decimal("0.01") if item.unit == "pt" else Decimal("0.001")


def snapshot_match(snapshot, offered, docs, close_at):
    """A confirmed close snapshot locks only beside a post-close article reporting the same close.
    Returns (article evidence, disagreeing observations); any disagreement withholds the number."""
    articles = [v for v in offered if v.basis == "close" and v.session_date == snapshot.session_date
                and not any(e.source_id in docs and docs[e.source_id].kind == "dataset" for e in v.evidence)]
    copies = [v for v in offered if v.id == snapshot.id]
    against = [v for v in articles if abs(v.value-snapshot.value) > tolerance(snapshot)]
    rejected = {v.id for v in against}
    proof = {e.source_id: e for v in [*copies, *articles] if v.id not in rejected for e in v.evidence
             if e.source_id in docs and docs[e.source_id].kind != "dataset" and close_at
             and docs[e.source_id].published_at and docs[e.source_id].published_at >= close_at
             and v.value in numbers(e.quote)}
    if not proof and not against and close_at:
        # The writer may leave a locked value to server insertion; an exact close in a named
        # sentence of a post-close article is then the match.
        for doc in docs.values():
            if doc.kind in {"dataset", "calendar"} or not doc.published_at or doc.published_at < close_at:
                continue
            for sentence in re.split(r"(?<=[.!?])\s+|\n+", doc.content):
                text = " ".join(sentence.split())
                if (10 <= len(text) <= 650 and any(n in text.lower() for n in SNAPSHOT_NAMES.get(snapshot.instrument, ()))
                        and snapshot.value in numbers(text)):
                    return [Evidence(source_id=doc.id, quote=text)], against
    return list(proof.values()), against


def reconcile(proposal, bundle):
    groups = {}
    for item in proposal.observations:
        groups.setdefault(item.instrument, []).append(item)
    locked = {v["instrument"]: MarketObservation.model_validate(v) for v in bundle.get("locked_observations", [])}
    snapshots = {d["id"] for d in bundle.get("documents", []) if d.get("registration") == US_CLOSE_REGISTRATION}
    docs = {d["id"]: SourceDocument.model_validate(d) for d in bundle["documents"]} if snapshots else {}
    close_at = bundle.get("exchange_closes", {}).get("US")
    close_at = datetime.fromisoformat(close_at) if close_at else None
    selected, conflicts = [], []
    for instrument in sorted(set(groups) | set(locked)):
        offered = groups.get(instrument, [])
        authoritative = locked.get(instrument)
        if authoritative and any(e.source_id in snapshots for e in authoritative.evidence):
            proof, against = snapshot_match(authoritative, offered, docs, close_at)
            if against:
                # Never the collected_data_retained path: the number is withheld with both values recorded.
                conflicts.append({"instrument": instrument, "session": str(authoritative.session_date),
                    "resolution": "withheld", "diagnostic": "us_close_snapshot_article_disagreement",
                    "values": [{"id": v.id, "value": str(v.value), "sources": [e.source_id for e in v.evidence]}
                               for v in [authoritative, *against]]})
                continue
            if proof:
                evidence = [*authoritative.evidence, *proof][:4]
                selected.append(authoritative.model_copy(update={"evidence": evidence}).model_dump(mode="json"))
                continue
            # Unmatched snapshot: the existing two-outlet rule applies to the article values alone.
            authoritative = None
            offered = [v for v in offered if not any(e.source_id in snapshots for e in v.evidence)]
            if not offered:
                continue
        if authoritative:
            offered = [authoritative, *[v for v in offered if v.id != authoritative.id]]
        offered.sort(key=lambda v: (v.basis == "close", v.as_of), reverse=True)
        target = authoritative or offered[0]
        comparable = [v for v in offered if v.basis == target.basis and v.session_date == target.session_date
                      and (v.basis == "close" or v.as_of == target.as_of)]
        disagreement = [v for v in comparable if abs(v.value-target.value) > tolerance(target)]
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
