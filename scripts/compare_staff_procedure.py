"""Pre-freeze fresh paired synthetic cases; compare a procedure change with the same model/tools.

No task, Slack, repository write, memory, live data or research side effects are allowed.
This small development comparison is not statistical evidence of broad expert superiority.
"""

import argparse
import asyncio
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from quant_company.company import fingerprint, load_roles
from quant_company.config import Settings
from quant_company.contracts import ProviderRequest
from quant_company.providers.client import RuntimeClient
from quant_company.providers.codex_runner import CodexRunner, RunnerConfig, strict_json
from quant_company.providers.codex_runtime import create_app
from quant_company.staff.cases import grade, make_case
from quant_company.staff.packs import pack, pack_content
from quant_company.staff.store import ASSESSMENT
from quant_company.staff.tools import TOOL_GUIDE, run_tool
from quant_company.tools import calculate


async def compare(args):
    if os.environ.get("REAL_STAFF_CODEX") != "1":
        raise SystemExit("Opt in with REAL_STAFF_CODEX=1")
    role = load_roles(Settings())[args.employee]
    path = "src/quant_company/staff/playbooks/" + args.employee + ".md"
    original = subprocess.check_output(["git", "show", args.base_commit+":"+path], text=True)
    candidate = pack(args.employee)
    plan_path = args.output.with_suffix(".plan.json")
    if plan_path.exists():
        raise SystemExit("Plan already exists. Preserve old evidence and choose a new output path.")
    plan = {"id": uuid4().hex, "created_at": datetime.now(UTC).isoformat(), "employee": args.employee,
            "model": role.model, "role": role.model_dump(mode="json"), "base_commit": args.base_commit,
            "candidate_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "packs": {"base": pack_content(args.employee, original), "candidate": candidate},
            "success_criterion": "Candidate satisfies all objective checks in all 6 fresh cases. Compare base on "
                                 "the exact same cases; retain every failure. This is development evidence, not statistical proof.",
            "cases": []}
    for index in range(6):
        seed = uuid4().hex
        public, key = make_case(args.employee, seed, index % 2)
        plan["cases"].append({"seed": seed, "public": public, "key": key})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2)+"\n")
    plan_digest = fingerprint(plan)
    runner = CodexRunner(RunnerConfig(codex_home=Path(os.environ["CODEX_HOME"]),
                                     jobs_dir=(args.output.parent/"staff-pair-jobs").resolve(), timeout_seconds=300))
    token = "local-procedure-comparison-not-production"
    client = RuntimeClient("http://staff.test", token, timeout_seconds=360,
                           transport=httpx.ASGITransport(app=create_app(runner=runner, token=token)))
    results = []
    allowed = set(role.tools) & {"calculate", *TOOL_GUIDE}
    try:
        for i, case in enumerate(plan["cases"]):
            for variant in (["base", "candidate"] if i % 2 == 0 else ["candidate", "base"]):
                receipts, calls, result = [], [], None
                for turn in range(1, 4):
                    material = {"employee": role.id, "mission": role.mission, "role_instructions": role.instructions,
                                "specialist_procedure": plan["packs"][variant], "case": case["public"],
                                "tools": {name: TOOL_GUIDE.get(name, "{expression: arithmetic string}") for name in allowed},
                                "remaining_calls": 4-turn, "past_practice_feedback": [], "previous_tool_receipts": receipts}
                    request = ProviderRequest(request_id=f"pair-{plan['id']}-{i}-{variant}-{turn}", model=role.model,
                                              prompt=ASSESSMENT+"EXERCISE JSON:\n"+json.dumps(material, ensure_ascii=False))
                    response = await client.run(request)
                    calls.append({"request": request.model_dump(), "response": response.model_dump(mode="json")})
                    d = response.decision
                    if (d.delegations or d.messages or d.memories or d.follow_up or
                            any(t.name not in allowed for t in d.tools) or any(a.source_ids for a in d.artifacts)):
                        result = {"objective_passed": False, "error": "forbidden_action"}
                        break
                    if d.status == "complete":
                        try:
                            answer = strict_json(d.artifacts[0].content) if len(d.artifacts) == 1 else None
                        except ValueError:
                            answer = None
                        result = {**grade(answer, case["key"]), "answer": answer}
                        break
                    for t in d.tools:
                        value = calculate(t.arguments["expression"]) if t.name == "calculate" else run_tool(t.name, t.arguments)
                        receipts.append({"request": t.model_dump(), "receipt": value})
                result = result or {"objective_passed": False, "error": "call_budget_exhausted"}
                results.append({"case_index": i, "family": case["public"]["family"], "variant": variant,
                                "result": result, "calls": calls, "tool_receipts": receipts})
                args.output.write_text(json.dumps({"plan_digest": plan_digest, "results": results}, ensure_ascii=False, indent=2)+"\n")
                print(json.dumps({"case": i, "variant": variant, "passed": result["objective_passed"]}), flush=True)
    finally:
        await runner.close()
    print(json.dumps({v: sum(r["result"]["objective_passed"] for r in results if r["variant"] == v)
                      for v in ("base", "candidate")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--employee", required=True)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(compare(parser.parse_args()))
