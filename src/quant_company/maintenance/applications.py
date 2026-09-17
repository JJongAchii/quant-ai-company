"""Authenticated Slack approval -> durable, exact-candidate application. No model calls."""

import re
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

import httpx
from psycopg.types.json import Jsonb

from .github import GitHubError
from .policy import digest


def approval_command(text):
    match = re.fullmatch(
        r"(?:(?:응|네|좋아|검토했어)[, ]+)?(?:(?:PR\s*)?#?(\d+)\s*(?:번)?\s*)?"
        r"(?:반영해|반영해줘|반영해주세요|반영해 주세요|적용해|적용해줘|적용해주세요|"
        r"승인|승인해|승인합니다|머지해|머지해줘|병합해|병합해줘)[.!。]*", text.strip(), re.I)
    return (True, int(match[1]) if match[1] else None) if match else (False, None)


def notify(company, conn, project, identity, text):
    message_id = str(uuid5(NAMESPACE_URL, "maintenance-application:" + identity))
    if not conn.execute("SELECT 1 FROM messages WHERE id=%s", (message_id,)).fetchone():
        company._message(conn, project, None, "director", "maintenance", "[개선 담당] " + text,
                         message_id=message_id)


def accept_approval(company, *, text, owner, channel, thread_ts, event_key, event_ts=None):
    recognized, number = approval_command(text)
    if not recognized:
        return None
    with company.db.transaction() as conn:
        if not conn.execute("SELECT to_regclass('maintenance_applications') AS name").fetchone()["name"]:
            return None
        conn.execute("SELECT pg_advisory_xact_lock(71350223)")
        project = conn.execute("SELECT * FROM projects WHERE channel=%s AND thread_ts=%s AND owner_user=%s",
                               (channel, thread_ts, owner)).fetchone()
        if not project or owner not in company.settings.slack_allowed_users:
            return None
        jobs = conn.execute("""SELECT j.* FROM maintenance_jobs j WHERE j.kind='repair'
            AND j.receipt ? 'pr' AND EXISTS (
              SELECT 1 FROM jsonb_array_elements(j.payload->'observations') x
              WHERE x->>'project_id'=%s) ORDER BY j.created_at DESC""", (str(project["id"]),)).fetchall()
        # Only a PR actually announced in this thread can be approved implicitly.
        announced = []
        for job in jobs:
            notice = conn.execute("SELECT sent_ts FROM outbox WHERE id=%s AND status='delivered'", (
                str(uuid5(NAMESPACE_URL, f"maintenance-pr:{job['id']}:{project['id']}")),)).fetchone()
            if notice and (event_ts is None or (notice['sent_ts'] is not None
                                               and Decimal(notice['sent_ts']) < Decimal(event_ts))):
                announced.append(job)
        jobs = announced
        if number:
            jobs = [j for j in jobs if j["receipt"]["pr"]["number"] == number]
        else:
            pending = [j for j in jobs if j["state"] == "pr_open"]
            jobs = pending or jobs[:1]
        if not jobs:
            return None
        if len(jobs) != 1:
            notify(company, conn, project, event_key, "검토할 PR이 여러 개입니다. ‘PR 번호 반영해’로 대상을 지정해 주세요.")
            return {"maintenance_approval": "ambiguous"}
        job = jobs[0]
        old = conn.execute("SELECT * FROM maintenance_applications WHERE job_id=%s", (job["id"],)).fetchone()
        if old:
            if old["event_key"] != event_key:
                state_text = {"approved": "검사 대기", "merging": "병합 확인 중", "merged": "병합 완료",
                              "deploy_pending": "서버 배포 대기", "complete": "반영 완료", "blocked": "확인 필요",
                              "rolled_back": "이전 서버 버전으로 복구"}.get(old["state"], "처리 중")
                notify(company, conn, project, event_key + ":existing",
                       f"PR #{job['receipt']['pr']['number']}의 승인은 이미 기록되어 있습니다. 현재 상태: {state_text}.")
            return {"maintenance_approval": old["state"], "application_id": str(old["id"]), "duplicate": True}
        if job["state"] != "pr_open":
            return {"maintenance_approval": "not_open"}
        identity = str(uuid5(NAMESPACE_URL, "maintenance-approval:" + event_key))
        conn.execute("""INSERT INTO maintenance_applications
            (id,job_id,project_id,owner_user,event_key,approval_text,head)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                     (identity, job["id"], project["id"], owner, event_key, text, job["receipt"]["head"]))
        company._event(conn, "maintenance_approved", {"application_id": identity, "event_key": event_key,
                        "head": job["receipt"]["head"], "pr": job["receipt"]["pr"]["number"]}, project["id"])
        notify(company, conn, project, identity + ":approved",
               f"PR #{job['receipt']['pr']['number']} ({job['receipt']['head'][:12]}) 반영 승인을 기록했습니다. "
               "검사 후 병합하고, 실행 코드 변경이면 서버에 배포한 뒤 결과를 알려드리겠습니다.")
        return {"maintenance_approval": "approved", "application_id": identity}


class Applications:
    def __init__(self, company, config, github):
        self.company, self.config, self.github = company, config, github

    def save(self, application, state, receipt, error=None):
        with self.company.db.transaction() as conn:
            conn.execute("""UPDATE maintenance_applications SET state=%s,receipt=%s,error=%s,updated_at=now()
                WHERE id=%s""", (state, Jsonb(receipt), error, application["id"]))
            if state in {"complete", "blocked", "rolled_back"}:
                project = self.company._project(conn, application["project_id"])
                pr = receipt.get("pr_number", "")
                if state == "complete":
                    detail = ("서버 배포와 상태 확인을 완료했습니다." if receipt.get("deployment")
                              else "문서 변경을 저장소에 반영했습니다. 서버 재배포는 필요하지 않습니다.")
                    text = f"PR #{pr} 병합 완료. {detail} 커밋: {receipt['merge_commit'][:12]}"
                    conn.execute("UPDATE maintenance_jobs SET state='applied',updated_at=now() WHERE id=%s",
                                 (application["job_id"],))
                else:
                    reasons = {
                        "approved_head_changed": "승인한 커밋이 변경됐습니다",
                        "approved_pr_changed_or_closed": "PR의 내용이나 공개 상태가 승인 당시와 다릅니다",
                        "code_base_moved_retest_required": "기준 코드가 변경되어 새 기준으로 검사가 필요합니다",
                        "merge_not_confirmed_requires_reconciliation": "GitHub의 실제 병합 결과를 확인하지 못했습니다",
                        "release_service_unhealthy": "배포 후 서비스 상태 확인에 실패했습니다",
                        "interrupted_cutover_restored": "배포 도중 실행이 중단되어 복구했습니다",
                    }
                    reason = reasons.get(error, "검사 또는 적용 단계에서 확인이 필요한 문제가 발생했습니다")
                    text = (f"PR #{pr} 반영을 완료하지 못했습니다. {reason}. "
                            + ("이전 서버 버전으로 복구했습니다." if state == "rolled_back"
                               else "승인 기록과 진행 결과를 보존했습니다. 운영자 확인이 필요합니다."))
                notify(self.company, conn, project, str(application["id"]) + ":" + state, text)

    def advance(self):
        with self.company.db.transaction() as conn:
            application = conn.execute("""SELECT * FROM maintenance_applications
                WHERE state IN ('approved','merging','merged') ORDER BY created_at LIMIT 1""").fetchone()
            if not application:
                return None
            job = conn.execute("SELECT * FROM maintenance_jobs WHERE id=%s", (application["job_id"],)).fetchone()
        receipt = application["receipt"]
        try:
            if application["owner_user"] not in set(self.config.allowed_owners) & set(self.company.settings.slack_allowed_users):
                raise ValueError("approval_owner_no_longer_authorized")
            if application["head"] != job["receipt"]["head"]:
                raise ValueError("approved_head_changed")
            if digest(job["payload"]["changes"]) != job["payload"]["patch_digest"]:
                raise ValueError("approved_patch_changed")
            receipt.setdefault("pr_number", job["receipt"]["pr"]["number"])
            if application["state"] == "approved":
                validated = self.github.validate_application(job)
                if validated is None:
                    return {"state": "waiting_for_ci"}
                receipt.update(validated)
                # Intent is durable before the external merge. A crash only reconciles, never re-merges.
                self.save(application, "merging", receipt)
                merged = self.github.merge_application(job, receipt)
                receipt.update(merged)
            elif application["state"] == "merging":
                receipt.update(self.github.reconcile_application(job, receipt))
            if application["state"] != "merged":
                self.save(application, "merged", receipt)
            state = "deploy_pending" if receipt["requires_deployment"] else "complete"
            self.save(application, state, receipt)
            return {"state": state, "application_id": str(application["id"])}
        except (ValueError, GitHubError, httpx.HTTPError) as exc:
            code = str(exc)
            if not re.fullmatch(r"[a-z_0-9]{1,100}", code):
                code = "application_validation_failed"
            self.save(application, "blocked", receipt, code)
            return {"state": "blocked", "reason": code}
