"""One bounded real-subscription request shaped like a signal turn; never posts, never writes the database."""

import argparse
import asyncio
import json
import os
import time
from datetime import UTC, date, datetime
from pathlib import Path

from quant_company.briefing.contracts import BRIEFER
from quant_company.company import Company, fingerprint
from quant_company.config import Settings
from quant_company.contracts import ProviderFault, ProviderRequest
from quant_company.execution import provider_for
from quant_company.signals.instructions import instruction
from quant_company.signals.schedule import slots_for
from quant_company.signals.snapshot import snapshot_text
from quant_company.signals.store import parse_proposal, snapshot_source_id


def qualification_request(role, slot, snapshot: dict, edition: str) -> ProviderRequest:
    """The role's instructions, the service's signal instruction and the edition as data. This checks the contract and
    latency; it is not a byte-identical copy of a worker turn, which needs the production database."""
    source_id = snapshot_source_id(slot.day, slot.kind)
    body = dict(model=role.model, reasoning_effort=role.reasoning_effort, output_contract="agent_decision",
                prompt="\n\n".join([role.instructions, instruction(slot, source_id, snapshot_text(snapshot)),
                                    "발간본(자료):\n" + edition]))
    return ProviderRequest(request_id="signal-qualify-" + fingerprint([slot.day, slot.kind, body])[:40], **body)


async def qualify(company, snapshot: dict, edition: str, kind: str, day: date, output: Path, *, provider=None) -> dict:
    settings = company.settings
    if settings.model_provider != "codex" or settings.analyst_signals_enabled:
        raise ValueError("qualification_requires_codex_preview")
    slot = next((item for item in slots_for(day) if item.kind == kind), None)
    if slot is None:
        raise ValueError("qualification_day_has_no_slot")
    role = company.roles[BRIEFER]
    request = qualification_request(role, slot, snapshot, edition)
    source_id = snapshot_source_id(day, kind)
    receipt = {"checked_at": datetime.now(UTC).isoformat(), "request_id": request.request_id,
               "request_sha256": fingerprint(request.model_dump(mode="json")), "kind": kind, "session_day": str(day),
               "model": request.model, "reasoning_effort": request.reasoning_effort,
               "real_model": settings.model_provider == "codex", "slack_writes": False, "database_writes": False}
    started = time.monotonic()
    try:
        response = await (provider or provider_for(company)).run(request)
        receipt["latency_seconds"] = round(time.monotonic() - started, 1)
        proposal = parse_proposal(response, {"kind": kind, "session_day": day}, {source_id}, source_id)
        receipt.update(state="valid", proposal=proposal.model_dump(mode="json"))
    except ProviderFault as exc:
        receipt.update(state="fault", latency_seconds=round(time.monotonic() - started, 1), error=exc.code)
    except ValueError as exc:
        receipt.update(state="invalid", error=str(exc))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    with temporary.open("w") as handle:
        os.chmod(temporary, 0o600)
        handle.write(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(output)
    return receipt


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path, help="JSON from signals.snapshot.read_snapshot")
    parser.add_argument("--edition", required=True, type=Path, help="Edition text, given to the model as data")
    parser.add_argument("--kind", required=True, choices=["intraday", "overnight"])
    parser.add_argument("--day", required=True, type=date.fromisoformat)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    receipt = await qualify(Company(Settings()), json.loads(args.snapshot.read_text()), args.edition.read_text(),
                            args.kind, args.day, args.output)
    print(json.dumps({key: receipt.get(key) for key in ("state", "error", "latency_seconds", "real_model")}))
    if receipt["state"] != "valid":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
