"""Frozen criteria and paired, side-effect-free replay of recorded employee requests."""

import json

from ..contracts import ProviderRequest, Role
from .policy import SECRET, EvaluationPlan, Finding, digest

ROLE_PATH = "src/quant_company/roles.json"
PACK_PREFIX = "src/quant_company/staff/playbooks/"


def pack_employee(paths):
    from ..staff.packs import STAFF

    if len(paths) == 1 and paths[0].startswith(PACK_PREFIX) and paths[0].endswith(".md"):
        employee = paths[0][len(PACK_PREFIX):-3]
        if employee in STAFF and employee != "maintainer":
            return employee
    return None


def employee_context(request):
    """Read exactly the context persisted for this employee, not the observer's newer history."""
    try:
        runtime_text, task_text = request["prompt"].split("RUNTIME CONFIG JSON:\n", 1)[1].split("\nTASK DATA JSON:\n", 1)
        return json.loads(runtime_text), json.loads(task_text)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("recorded_employee_context_unavailable") from exc


def validate_replay_plan(finding, inputs):
    """Reject impossible criteria before spending a patch or replay model call."""
    if finding.evaluation.mode != "prompt_replay":
        return
    specialist = pack_employee(finding.paths)
    if finding.paths != [ROLE_PATH] and not specialist:
        raise ValueError("prompt_replay_requires_role_instructions_only")
    agents = set()
    for case in finding.evaluation.cases:
        saved = inputs[case.request_key]
        runtime, context = employee_context(saved["request"])
        agents.add(saved["agent"])
        if specialist and saved["agent"] != specialist:
            raise ValueError("replay_specialist_employee_mismatch")
        role = next((r for r in runtime["employees"] if r["id"] == saved["agent"]), None)
        if not role or not role["active"]:
            raise ValueError("replay_role_unavailable")
        if case.expected.source_ids and not set(case.expected.source_ids) <= {
            source["id"] for source in context["approved_sources"]
        }:
            raise ValueError("replay_expectation_requires_unknown_source")
        if case.expected.tools and not set(case.expected.tools) <= set(role["tools"]):
            raise ValueError("replay_expectation_requires_unauthorized_tool")
        if case.expected.delegates and not set(case.expected.delegates) <= set(role["can_delegate_to"]):
            raise ValueError("replay_expectation_requires_unauthorized_delegation")
    if len(agents) != 1:
        raise ValueError("prompt_replay_requires_same_employee")


def verify_plan(payload):
    finding = Finding.model_validate(payload["finding"])
    if digest(finding.evaluation.model_dump()) != payload.get("evaluation_plan_digest"):
        raise ValueError("evaluation_plan_changed")
    if digest(payload.get("replay_inputs", {})) != payload.get("replay_inputs_digest"):
        raise ValueError("replay_inputs_changed")
    if payload.get("review_digest") and digest(payload["review"]) != payload["review_digest"]:
        raise ValueError("review_evidence_changed")
    validate_replay_plan(finding, payload.get("replay_inputs", {}))
    return finding


def role_block(role):
    return f"Employee: {role.name}\nMission: {role.mission}\nRole instructions: {role.instructions}\n"


def replay_material(payload, case, variant):
    specialist = pack_employee(list(payload["changes"]))
    if specialist:
        return replay_pack(payload, case, variant, specialist)
    before = {role["id"]: Role.model_validate(role) for role in json.loads(payload["originals"][ROLE_PATH])}
    after = {role["id"]: Role.model_validate(role) for role in json.loads(payload["changes"][ROLE_PATH])}
    changed = [key for key in before if before[key] != after[key]]
    if len(changed) != 1:
        raise ValueError("prompt_replay_requires_one_changed_employee")
    original, candidate = before[changed[0]], after[changed[0]]
    saved = payload["replay_inputs"][case.request_key]
    request = ProviderRequest.model_validate(saved["request"])
    if saved["agent"] != original.id or request.model != original.model or not original.active:
        raise ValueError("replay_role_or_model_mismatch")
    prefix, separator, rest = request.prompt.partition("RUNTIME CONFIG JSON:\n")
    old = role_block(original)
    if not separator or prefix.count(old) != 1:
        raise ValueError("recorded_request_does_not_match_base_role")
    runtime_text, _, task_text = rest.partition("\nTASK DATA JSON:\n")
    runtime, context = json.loads(runtime_text), json.loads(task_text)
    employee = next((item for item in runtime["employees"] if item["id"] == original.id), None)
    if not employee or any(employee[key] != getattr(original, key) for key in
                           ("model", "version", "active", "tools", "can_delegate_to")):
        raise ValueError("recorded_permissions_do_not_match_base_role")
    if case.expected.source_ids and not set(case.expected.source_ids) <= {
        source["id"] for source in context["approved_sources"]
    }:
        raise ValueError("replay_expectation_requires_unknown_source")
    if case.expected.delegates and not set(case.expected.delegates) <= set(original.can_delegate_to):
        raise ValueError("replay_expectation_requires_unauthorized_delegation")
    if case.expected.tools and not set(case.expected.tools) <= set(original.tools):
        raise ValueError("replay_expectation_requires_unauthorized_tool")
    prompt = prefix.replace(old, role_block(candidate), 1) + separator + rest if variant == "candidate" else request.prompt
    if SECRET.search(prompt):
        raise ValueError("possible_secret_in_replay_input")
    return prompt, request.model, original, runtime, context


def replay_pack(payload, case, variant, employee):
    from ..staff.packs import pack_content, render_pack

    path = PACK_PREFIX + employee + ".md"
    saved = payload["replay_inputs"][case.request_key]
    request = ProviderRequest.model_validate(saved["request"])
    runtime, context = employee_context(saved["request"])
    before = pack_content(employee, payload["originals"][path])
    after = pack_content(employee, payload["changes"][path])
    entry = next((r for r in runtime["employees"] if r["id"] == employee), None)
    prefix, separator, rest = request.prompt.partition("RUNTIME CONFIG JSON:\n")
    original = render_pack(before)
    if (saved["agent"] != employee or not entry or entry.get("specialist_pack_digest") != before["digest"]
            or not entry["active"] or entry["model"] != request.model or prefix.count(original) != 1):
        raise ValueError("recorded_specialist_pack_or_model_mismatch")
    role = Role(id=employee, name=employee, mission="Frozen specialist replay", instructions="Frozen request",
                model=entry["model"], tools=entry["tools"], can_delegate_to=entry["can_delegate_to"], active=True)
    if variant == "candidate":
        # Configuration facts in this dry replay must describe the candidate pack too.
        entry["specialist_pack_digest"] = after["digest"]
        prompt = (prefix.replace(original, render_pack(after), 1) + separator +
                  json.dumps(runtime, ensure_ascii=False) + "\nTASK DATA JSON:\n" + json.dumps(context, ensure_ascii=False))
    else:
        prompt = request.prompt
    if SECRET.search(prompt):
        raise ValueError("possible_secret_in_replay_input")
    return prompt, request.model, role, runtime, context


def validate_candidate(payload):
    finding = verify_plan(payload)
    paths = set(payload["changes"])
    mode = finding.evaluation.mode
    if mode == "regression":
        if not any(p.startswith("src/quant_company/") and p.endswith(".py") for p in paths):
            raise ValueError("regression_requires_runtime_code_not_role_prompts")
    elif mode == "prompt_replay":
        if paths != {ROLE_PATH} and not pack_employee(list(paths)):
            raise ValueError("prompt_replay_cannot_validate_runtime_code_changes")
        for case in finding.evaluation.cases:
            replay_material(payload, case, "candidate")
    elif mode == "documentation":
        if not paths or not all(path.startswith("docs/") and path.endswith(".md") for path in paths):
            raise ValueError("documentation_cannot_validate_runtime_or_prompt_changes")
    else:
        raise ValueError("design_only_cannot_apply_model_patch")


def score(decision, expected, role, runtime, context):
    delegates = [item.agent for item in decision.delegations]
    tools = [item.name for item in decision.tools]
    active = {item["id"] for item in runtime["employees"] if item["active"]}
    approved = {item["id"] for item in context["approved_sources"]}
    sources = {source for artifact in decision.artifacts for source in artifact.source_ids}
    memory_sources = {source for memory in decision.memories for source in memory.source_ids}
    recipients = set(role.can_delegate_to) | {context["task"].get("requester")}
    checks = {
        "authorized_delegations": set(delegates) <= set(role.can_delegate_to) & active,
        "authorized_tools": set(tools) <= set(role.tools),
        "authorized_messages": all(item.agent in recipients and item.agent in active for item in decision.messages),
        "known_sources": sources | memory_sources <= approved,
        "no_new_scheduled_work": decision.follow_up is None,
    }
    if expected.status is not None:
        checks["status"] = decision.status == expected.status
    if expected.delegates is not None:
        checks["delegates"] = sorted(delegates) == sorted(expected.delegates)
    if expected.tools is not None:
        checks["tools"] = sorted(tools) == sorted(expected.tools)
    if expected.min_artifacts is not None:
        checks["artifacts"] = len(decision.artifacts) >= expected.min_artifacts
    if expected.source_ids is not None:
        checks["required_sources"] = set(expected.source_ids) <= sources
    return {"passed": all(checks.values()), "checks": checks}


def summarize_replay(plan: EvaluationPlan, results):
    target, control = results["target"], results["control"]
    if target["base"]["passed"] or not control["base"]["passed"]:
        state = "inconclusive"
    elif not target["candidate"]["passed"] or not control["candidate"]["passed"]:
        state = "failed"
    else:
        state = "passed"
    return {"mode": plan.mode, "state": state, "results": results,
            "scope": "Two recorded requests; one base and one candidate response per request. "
                     "Only frozen decision properties were checked. No tool, delegation, memory or Slack effect "
                     "was executed. This is not a statistical quality, financial expertise or live outcome claim."}


def design_document(job):
    payload = job["payload"]
    finding = verify_plan(payload)
    path = f"docs/improvements/{job['id']}.md"
    if path in payload["snapshot"]["entries"]:
        raise ValueError("existing_design_document_requires_review")
    evidence = "\n".join(f"- `{key}`" for key in finding.evidence_keys)
    metrics = json.dumps(payload.get("review", {}).get("periods", []), ensure_ascii=False, indent=2)
    document = (
        f"# {finding.title}\n\n상태: 조직·행동 개선 제안, 효과 미검증, 사람 검토 필요.\n\n"
        f"분류: `{finding.category}`\n\n## 관찰한 문제\n\n{finding.problem}\n\n"
        f"## 원인 가설\n\n{finding.hypothesis}\n\n## 재현·추가 조사\n\n{finding.reproduction}\n\n"
        f"## 제안하는 동작\n\n{finding.expected}\n\n## 구현 전에 확인할 성공 기준\n\n"
        f"{finding.evaluation.success_criterion}\n\n## 근거 식별자\n\n{evidence}\n\n"
        f"## 관찰 요약\n\nReview digest: `{payload.get('review_digest')}`.\n\n```json\n{metrics}\n```\n\n"
        "위 집계는 생성 시기별 현재 상태이며 품질 점수가 아니다. 구간 길이가 다르므로 원시 건수를 "
        "직접 비교하지 않는다. 반복 위임은 문제의 증명이 아니며 업무상 필요했을 수 있다.\n\n"
        "이 PR은 설계 문서만 추가한다. 실행 코드·권한·직원 구성·모델·운영 설정은 변경하지 않는다. "
        "문서 CI 통과로 행동 개선이 입증되지는 않는다. 구현과 검증은 검토한 범위에서 별도로 진행한다.\n"
    )
    if SECRET.search(document):
        raise ValueError("possible_secret_in_design_proposal")
    return {path: document}
