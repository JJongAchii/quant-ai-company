"""Durable owner commands, run on the existing inference-independent Temporal queue."""

import asyncio

from psycopg.types.json import Jsonb

from .contracts import ProviderFault
from .model_policy import HELP_TEXT, authorized, baseline, selection, status, targets


def validate_selection(target, chosen, models):
    found = next((model for model in models if model["model"] == chosen["model"]), None)
    if found is None:
        raise ValueError(f"현재 계정의 모델 목록에 {chosen['model']}이 없습니다.")
    if chosen["reasoning_effort"] not in found["reasoning_efforts"]:
        raise ValueError(f"{chosen['model']}은 추론 강도 {chosen['reasoning_effort']}을 지원하지 않습니다.")
    if target == "director" and chosen["reasoning_effort"] != "max":
        raise ValueError("총괄은 기존 정책에 따라 max를 지정해 주세요.")


class ModelControl:
    def __init__(self, company, client):
        self.company, self.client = company, client

    def pending(self):
        with self.company.db.transaction() as conn:
            command = conn.execute("""SELECT * FROM model_assignment_commands WHERE state='requested'
                ORDER BY sequence LIMIT 1""").fetchone()
            account = conn.execute("SELECT profile,revision FROM model_account_policy WHERE id=1").fetchone()
            return command, account

    def apply(self, command_id, account, models, error=None):
        company = self.company
        with company.db.transaction() as conn:
            # Same order as account routing: an account cannot switch during validation/activation.
            current_account = conn.execute("SELECT profile,revision FROM model_account_policy WHERE id=1 FOR SHARE").fetchone()
            row = conn.execute("""SELECT * FROM model_assignment_commands WHERE state='requested'
                ORDER BY sequence LIMIT 1""").fetchone()
            if not row or row["id"] != command_id:
                return False
            task = conn.execute("SELECT * FROM tasks WHERE id=%s", (command_id,)).fetchone()
            project = company._project(conn, task["project_id"])
            task = conn.execute("SELECT * FROM tasks WHERE id=%s FOR UPDATE", (command_id,)).fetchone()
            current = conn.execute("SELECT * FROM model_assignment_policy WHERE id=1 FOR UPDATE").fetchone()
            row = conn.execute("""SELECT * FROM model_assignment_commands WHERE state='requested'
                ORDER BY sequence LIMIT 1 FOR UPDATE""").fetchone()
            if not row or row["id"] != command_id:
                return False
            command, outcome = row["command"], "rejected"
            action = command["action"]
            receipt = {"action": action, "revision": current["revision"]}
            try:
                if (project["owner_user"] != row["owner_user"] or
                        not authorized(company, row["owner_user"], project["channel"], task["agent"], command)):
                    raise ValueError("모델 배정 제어 권한 또는 설정이 변경되었습니다.")
                if not company.settings.model_assignments_enabled:
                    raise ValueError("모델 배정 기능이 아직 활성화되지 않았습니다.")
                if task["revision"] != project["revision"] or task["status"] != "waiting":
                    raise ValueError("이미 변경되거나 중단된 업무의 모델 요청입니다.")
                if action not in {"status", "history", "help"}:
                    if error or models is None:
                        raise ValueError("현재 계정의 모델 목록을 확인하지 못했습니다. 배정은 변경하지 않았습니다.")
                    if account != current_account:
                        raise ValueError("확인 중 선택 계정이 변경되었습니다. 현재 계정으로 다시 요청해 주세요.")
                    conn.execute("""INSERT INTO model_catalog_checks(id,profile,account_revision,models)
                        VALUES(%s,%s,%s,%s)""", (command_id, account["profile"], account["revision"], Jsonb(models)))
                    receipt.update(profile=account["profile"], account_revision=account["revision"], catalog_id=str(command_id))
                if action == "help":
                    text, outcome = HELP_TEXT, "help"
                elif action == "status":
                    snapshot = status(company, conn)
                    text = f"모델 배정 #{current['revision']}\n" + "\n".join(
                        f"• {key}: {value['model']} · {value['reasoning_effort']} ({value['source']})"
                        for key, value in snapshot["assignments"].items())
                    text += "\n우선순위: 이번 작업 지정 → 직원별 고정 → 기본 배정. 별도 Claude 검토 모델은 제외합니다."
                    receipt["assignments"], outcome = snapshot["assignments"], "status"
                elif action == "history":
                    rows = conn.execute("""SELECT revision,owner_user,created_at,bindings FROM model_assignment_revisions
                        ORDER BY revision DESC LIMIT 10""").fetchall()
                    text = "최근 모델 배정 이력\n" + "\n".join(
                        f"• #{item['revision']} · {item['created_at'].isoformat()} · " +
                        (", ".join(f"{key}={value['model']}/{value['reasoning_effort']}"
                                   for key, value in item["bindings"].items()) or "기본 배정") for item in rows)
                    text += "\n`모델 배정 복원 번호`로 해당 고정 배정을 복원합니다."
                    outcome = "history"
                elif action == "catalog":
                    text = f"{account['profile']} 계정에서 확인한 모델\n" + "\n".join(
                        f"• {item['model']}: {', '.join(item['reasoning_efforts'])}" for item in models[:30])
                    if len(models) > 30:
                        text += f"\n전체 {len(models)}개 중 처음 30개를 표시합니다. 전체 목록은 운영 조회 기록에 보존했습니다."
                    text += "\n목록 확인은 실제 추론 성공이나 잔여 사용량 보장이 아닙니다."
                    outcome = "catalog"
                elif action == "task":
                    chosen = {key: command[key] for key in ("model", "reasoning_effort")}
                    validate_selection(task["agent"], chosen, models)
                    count = conn.execute("""SELECT count(*) AS n FROM tasks WHERE project_id=%s AND turn_count>0
                        AND kind<>'research_stage'""", (project["id"],)).fetchone()["n"]
                    cap = company.settings.company_max_project_tasks
                    if cap and count >= cap:
                        raise ValueError("이 스레드의 업무 한도에 도달했습니다. 새 스레드에서 요청해 주세요.")
                    chosen.update(source="task", revision=current["revision"], command_id=str(command_id))
                    task = conn.execute("""UPDATE tasks SET kind='work',status='queued',instruction=%s,model_selection=%s
                        WHERE id=%s RETURNING *""", (command["instruction"], Jsonb(chosen), command_id)).fetchone()
                    company._new_turn(conn, task)
                    receipt["selection"] = chosen
                    text = (f"이번 업무에 {chosen['model']} · {chosen['reasoning_effort']}을 지정했습니다. "
                            "해당 직원의 후속 응답과 웹검색에 적용합니다. 동료 위임은 각 직원의 배정을 사용합니다.")
                    outcome = "task_queued"
                else:
                    updated = dict(current["bindings"])
                    if action in {"pin", "reset"}:
                        target = command["target"]
                        if target not in targets(company):
                            raise ValueError("등록되지 않았거나 모델을 사용하지 않는 직원입니다.")
                        if action == "pin":
                            chosen = {key: command[key] for key in ("model", "reasoning_effort")}
                            validate_selection(target, chosen, models)
                            updated[target] = {**chosen, "command_id": str(command_id)}
                        else:
                            updated.pop(target, None)
                            validate_selection(target, selection(company, conn, target,
                                current={**current, "bindings": updated}, default=baseline(company, target)), models)
                    elif action == "restore":
                        previous = conn.execute("SELECT bindings FROM model_assignment_revisions WHERE revision=%s",
                                                (command["revision"],)).fetchone()
                        if not previous:
                            raise ValueError("해당 모델 배정 이력이 없습니다.")
                        updated = previous["bindings"]
                        # Validate every changed effective assignment, including removed pins/inherited models.
                        for target in current["bindings"].keys() | updated.keys():
                            if target not in targets(company):
                                raise ValueError("이력의 직원 구성이 현재와 달라 복원할 수 없습니다.")
                            validate_selection(target, selection(company, conn, target,
                                current={**current, "bindings": updated}), models)
                    else:
                        raise ValueError("지원하지 않는 모델 배정 명령입니다.")
                    revision = current["revision"] + 1
                    conn.execute("UPDATE model_assignment_policy SET revision=%s,bindings=%s WHERE id=1",
                                 (revision, Jsonb(updated)))
                    conn.execute("""INSERT INTO model_assignment_revisions(revision,bindings,command_id,owner_user)
                        VALUES(%s,%s,%s,%s)""", (revision, Jsonb(updated), command_id, row["owner_user"]))
                    receipt.update(revision=revision, before=current["bindings"], after=updated)
                    text = f"모델 배정 #{revision}을 저장했습니다. 새로 준비하는 요청부터 적용합니다."
                    if action == "pin":
                        text += f"\n{target}: {command['model']} · {command['reasoning_effort']} (고정)"
                    elif action == "reset":
                        text += f"\n{target}: 고정을 해제하고 기본 배정을 사용합니다."
                    else:
                        text += f"\n#{command['revision']}의 고정 배정을 복원했습니다."
                    outcome = "applied"
            except ValueError as exc:
                text = str(exc)
            receipt["outcome"] = outcome
            conn.execute("""UPDATE model_assignment_commands SET state='completed',receipt=%s,completed_at=now()
                WHERE id=%s""", (Jsonb(receipt), command_id))
            if outcome != "task_queued" and task["status"] == "waiting":
                conn.execute("UPDATE tasks SET status='completed',result=%s WHERE id=%s", (text, command_id))
            company._message(conn, project, command_id, task["agent"], "status", text)
            company._event(conn, "model_assignment_control", {"command_id": str(command_id),
                           "owner": row["owner_user"], **receipt}, project["id"])
            return True

    async def tick(self):
        row, account = await asyncio.to_thread(self.pending)
        if row is None:
            return False
        models, error = None, None
        if self.company.settings.model_assignments_enabled and row["command"]["action"] not in {"status", "history", "help"}:
            try:
                if self.client is None:
                    from .providers.client import RuntimeClient

                    self.client = RuntimeClient(self.company.settings.model_runtime_url,
                                                self.company.settings.model_runtime_token.get_secret_value())
                models = await self.client.models(account["profile"])
            except ProviderFault:
                error = "unavailable"
        await asyncio.to_thread(self.apply, row["id"], account, models, error)
        return True
