"""Server-owned, hidden-input comparison of one specialist procedure. No LLM self-grading."""

import copy
import json
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..company import fingerprint
from ..maintenance.policy import digest
from ..providers.codex_runner import strict_json
from ..tools import calculate
from .cases import SUITE_VERSION, grade, make_case
from .packs import STAFF, pack_content, render_pack
from .tools import TOOL_GUIDE, run_tool

PREFIX = "src/quant_company/staff/playbooks/"
PURPOSES = ("target", "transfer", "control")


def employee_for(finding):
    paths = finding["paths"]
    if len(paths) == 1:
        for employee in STAFF:
            if paths == [PREFIX + employee + ".md"]:
                return employee
    raise ValueError("staff_replay_requires_one_specialist_procedure")


def source_run(conn, payload):
    employee = employee_for(payload["finding"])
    run_id = payload["finding"]["evaluation"]["staff_run_id"]
    run = conn.execute("SELECT * FROM staff_runs WHERE id=%s", (run_id,)).fetchone()
    cited = any(o.get("kind") == "staff_assessment" and o.get("run_id") == run_id
                and o["key"] in payload["finding"]["evidence_keys"] for o in payload["observations"])
    if (not cited or not run or run["owner_user"] not in payload["owners"] or run["employee"] != employee
            or run["state"] != "completed" or not run["grade"] or run["grade"].get("objective_passed") is not False
            or run["suite_version"] != SUITE_VERSION
            or fingerprint([run["public_case"], run["answer_key"]]) != run["case_digest"]):
        raise ValueError("staff_replay_requires_current_undisputed_failure")
    return run


def freeze(company, job):
    """Called after reading exact base files, before the repair model sees any candidate feedback."""
    payload = job["payload"]
    with company.db.transaction() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(71350222)")
        run = source_run(conn, payload)
        employee = run["employee"]
        existing = conn.execute("SELECT * FROM staff_comparisons WHERE job_id=%s", (job["id"],)).fetchone()
        if existing:
            validate_record(existing, payload)
            return
        original = payload["originals"][PREFIX + employee + ".md"]
        from ..model_policy import effective_role

        role = effective_role(company, conn, employee)
        if (pack_content(employee, original)["digest"] != run["pack_snapshot"]["digest"]
                or role.model != run["model"]
                or role.reasoning_effort != run["role_snapshot"].get("reasoning_effort")):
            raise ValueError("staff_replay_base_configuration_changed")
        call = conn.execute("SELECT request FROM staff_calls WHERE run_id=%s ORDER BY sequence LIMIT 1",
                            (run["id"],)).fetchone()
        if not call:
            raise ValueError("staff_replay_requires_recorded_request")
        if call["request"].get("reasoning_effort") != run["role_snapshot"].get("reasoning_effort"):
            raise ValueError("staff_replay_request_configuration_changed")
        context = json.loads(call["request"]["prompt"].split("EXERCISE JSON:\n", 1)[1])
        cases = [{"purpose": "target", "public": run["public_case"], "key": run["answer_key"],
                  "exposure": "released_practice"}]
        for purpose, variant in (("transfer", run["family_index"]), ("control", 1-run["family_index"])):
            public, key = make_case(employee, str(uuid4()), variant)
            cases.append({"purpose": purpose, "public": public, "key": key, "exposure": "fresh_parameters"})
        material = {"model": run["model"], "reasoning_effort": call["request"].get("reasoning_effort"),
                    "context": context, "base_pack": run["pack_snapshot"],
                    "max_calls": min(run["max_calls"], 3), "cases": cases, "suite_version": SUITE_VERSION}
        conn.execute("""INSERT INTO staff_comparisons(job_id,run_id,owner_user,employee,base_commit,material,input_digest)
            VALUES(%s,%s,%s,%s,%s,%s,%s)""", (job["id"], run["id"], run["owner_user"], employee,
                payload["snapshot"]["commit"], Jsonb(material), digest(material)))


def validate_record(record, payload):
    path = PREFIX + record["employee"] + ".md"
    if (digest(record["material"]) != record["input_digest"]
            or record["base_commit"] != payload["snapshot"]["commit"]
            or pack_content(record["employee"], payload["originals"][path])["digest"]
               != record["material"]["base_pack"]["digest"]
            or record["material"]["suite_version"] != SUITE_VERSION):
        raise ValueError("staff_comparison_frozen_input_changed")
    if record["candidate_digest"] is not None and record["candidate_digest"] != payload["patch_digest"]:
        raise ValueError("staff_comparison_candidate_changed")


def request_prompt(record, payload, case, variant, progress):
    from .store import ASSESSMENT

    material = record["material"]
    context = copy.deepcopy(material["context"])
    before = material["base_pack"]
    selected = (before if variant == "base" else
                pack_content(record["employee"], payload["changes"][PREFIX + record["employee"] + ".md"]))
    context.update(specialist_procedure=selected, case=case["public"],
                   remaining_calls=material["max_calls"]-len(progress["calls"]),
                   previous_tool_receipts=progress["tools"])
    # Maintainer instructions embed its procedure as well; change both occurrences consistently.
    if record["employee"] == "maintainer":
        header, _, rest = context["role_instructions"].partition("\n")
        embedded, _, instructions = rest.partition("\n")
        if header != "SPECIALIST PROCEDURE JSON:" or json.loads(embedded)["digest"] != before["digest"]:
            raise ValueError("recorded_maintainer_procedure_mismatch")
        context["role_instructions"] = render_pack(selected) + instructions
    return ASSESSMENT + "EXERCISE JSON:\n" + json.dumps(context, ensure_ascii=False)


def check_response(decision, case, context):
    forbidden = (decision.delegations or decision.messages or decision.memories or decision.follow_up
                 or any(t.name not in context["tools"] for t in decision.tools)
                 or any(a.source_ids for a in decision.artifacts))
    if forbidden:
        return [], {"objective_passed": False, "checks": {"exercise_permissions": False}}
    if decision.status == "continue" and decision.tools:
        receipts = []
        for tool in decision.tools:
            if tool.name == "calculate":
                try:
                    if set(tool.arguments) != {"expression"}:
                        raise ValueError("calculate requires expression")
                    value = calculate(tool.arguments["expression"])
                except (ValueError, TypeError) as exc:
                    value = {"ok": False, "error": str(exc)[:200]}
            elif tool.name in TOOL_GUIDE:
                value = run_tool(tool.name, tool.arguments)
            else:
                raise ValueError("unsupported_staff_comparison_tool")
            receipts.append({"request": tool.model_dump(), "receipt": value})
        return receipts, None
    try:
        answer = (strict_json(decision.artifacts[0].content)
                  if decision.status == "complete" and len(decision.artifacts) == 1 else None)
    except ValueError:
        answer = None
    return [], grade(answer, case["key"])


def summary(record, payload):
    results = {purpose: {v: record["results"].get(purpose+":"+v, {}).get("grade")
                        for v in ("base", "candidate")} for purpose in PURPOSES}
    if any(g is None for variants in results.values() for g in variants.values()):
        raise ValueError("staff_comparison_incomplete")
    def passed(p, v):
        return results[p][v].get("objective_passed") is True
    if passed("target", "base") or not passed("control", "base"):
        state = "inconclusive"
    elif not all(passed(p, "candidate") for p in PURPOSES):
        state = "failed"
    else:
        state = "passed"
    return {"mode": "staff_replay", "state": state, "results": results,
            "transfer_advantage": state == "passed" and not passed("transfer", "base"),
            "model": record["material"]["model"], "input_digest": record["input_digest"],
            "plan_digest": payload["evaluation_plan_digest"], "patch_digest": payload["patch_digest"],
            "base": record["base_commit"], "results_digest": digest(record["results"]),
            "scope": "One released failure and two fresh parameter cases from known families, paired by model/tools. "
                     "Objective code grading only; explanations unscored. Passing permits review of this procedure "
                     "repair, not a general expertise or statistical superiority claim. For a passing comparison, "
                     "no transfer advantage means both versions passed the fresh target-family case. "
                     "No model weights were trained."}


async def advance(runner, job):
    payload = job["payload"]
    with runner.company.db.transaction() as conn:
        source_run(conn, payload)
        record = conn.execute("SELECT * FROM staff_comparisons WHERE job_id=%s FOR UPDATE", (job["id"],)).fetchone()
        if not record:
            raise ValueError("staff_comparison_not_frozen")
        validate_record(record, payload)
        conn.execute("UPDATE staff_comparisons SET candidate_digest=%s WHERE job_id=%s",
                     (payload["patch_digest"], job["id"]))
    for case in record["material"]["cases"]:
        for variant in ("base", "candidate"):
            key = case["purpose"] + ":" + variant
            progress = record["results"].setdefault(key, {"calls": [], "tools": [], "grade": None})
            if progress["grade"] is not None:
                continue
            if len(progress["calls"]) >= record["material"]["max_calls"]:
                raise ValueError("staff_comparison_budget_state_invalid")
            prompt = request_prompt(record, payload, case, variant, progress)
            phase = f"staff-{case['purpose']}-{variant}-{len(progress['calls'])+1}"
            response = await runner.response(job, phase, prompt, model=record["material"]["model"],
                                             reasoning_effort=record["material"].get("reasoning_effort"))
            receipts, result = check_response(response.decision, case, record["material"]["context"])
            progress["calls"].append({"request_id": response.request_id,
                                      "response_digest": digest(response.model_dump(mode="json")),
                                      "provider": response.provider})
            progress["tools"].extend(receipts)
            if result is None and len(progress["calls"]) >= record["material"]["max_calls"]:
                result = {"objective_passed": False, "checks": {"answer_within_call_budget": False}}
            progress["grade"] = result
            with runner.company.db.transaction() as conn:
                source_run(conn, payload)  # A disputed/revoked source cannot acquire a new accepted result.
                conn.execute("UPDATE staff_comparisons SET results=%s WHERE job_id=%s",
                             (Jsonb(record["results"]), job["id"]))
            return
    payload["evaluation"] = summary(record, payload)
    passed = payload["evaluation"]["state"] == "passed"
    runner.store.save(job["id"], "publish" if passed else "blocked", payload=payload,
                      error=None if passed else "staff_evaluation_" + payload["evaluation"]["state"])


def verify_receipt(company, job):
    with company.db.transaction() as conn:
        source_run(conn, job["payload"])
        record = conn.execute("SELECT * FROM staff_comparisons WHERE job_id=%s", (job["id"],)).fetchone()
        if not record:
            raise ValueError("staff_comparison_receipt_required")
        validate_record(record, job["payload"])
        expected = summary(record, job["payload"])
        if expected["state"] != "passed" or expected != job["payload"].get("evaluation"):
            raise ValueError("staff_comparison_receipt_changed")
