"""Narrow host-worker protocol; never exposed as an LLM tool or HTTP endpoint."""

import json
import re
import sys
from uuid import UUID

from .applications import Applications
from .github import GitHub
from .policy import MaintenanceConfig


def command(company, action, identity, config_path):
    config = MaintenanceConfig.model_validate_json(config_path.read_text())
    if not config.enabled:
        raise ValueError("maintenance_disabled")
    github = GitHub(config)
    if identity:
        identity = str(UUID(identity))
    with company.db.transaction() as conn:
        if action == "next":
            row = conn.execute("""SELECT id,receipt FROM maintenance_applications
                WHERE state='deploy_pending' ORDER BY created_at LIMIT 1""").fetchone()
            print(json.dumps(None if not row else {"id": str(row["id"]),
                                                  "commit": row["receipt"]["merge_commit"]}))
            return
        application = conn.execute("SELECT * FROM maintenance_applications WHERE id=%s", (identity,)).fetchone()
        if not application or application["state"] not in {"deploy_pending", "complete", "rolled_back", "blocked"}:
            raise ValueError("application_not_ready_for_release")
        if application["owner_user"] not in set(config.allowed_owners) & set(company.settings.slack_allowed_users):
            raise ValueError("release_owner_no_longer_authorized")
        job = conn.execute("SELECT * FROM maintenance_jobs WHERE id=%s", (application["job_id"],)).fetchone()
        if application["head"] != job["receipt"]["head"]:
            raise ValueError("release_approved_head_changed")
    receipt = application["receipt"]
    if action == "archive":
        if application["state"] != "deploy_pending":
            raise ValueError("release_not_pending")
        verified = github.reconcile_application(job, receipt)
        if verified["merge_commit"] != receipt["merge_commit"]:
            raise ValueError("release_merge_changed")
        sys.stdout.buffer.write(github.archive(receipt["merge_commit"]))
    elif action == "activity":
        with company.db.transaction() as conn:
            # Ingress is paused by the host while active turns/outbox drain.
            rows = conn.execute("""SELECT count(*) AS n FROM turns t JOIN tasks k ON k.id=t.task_id
                JOIN projects p ON p.id=k.project_id
                WHERE k.revision=p.revision AND t.status IN ('queued','running','waiting')
                AND t.due_at<=now()""").fetchone()["n"]
            outbox = conn.execute("SELECT count(*) AS n FROM outbox WHERE status IN ('pending','sending')").fetchone()["n"]
        print(json.dumps({"active": rows, "outbox": outbox}))
    elif action == "finish":
        report = json.loads(sys.stdin.read(16384))
        if report.get("commit") != receipt["merge_commit"] or report.get("state") not in {"complete", "rolled_back", "blocked"}:
            raise ValueError("invalid_release_report")
        if not re.fullmatch(r"[a-z_0-9]{1,100}", report.get("error", "none")):
            raise ValueError("invalid_release_error")
        if application["state"] != "deploy_pending":
            if application["state"] != report["state"]:
                raise ValueError("release_result_conflict")
            return
        receipt["deployment"] = report
        Applications(company, config, github).save(application, report["state"], receipt, report.get("error"))
        from ..system_state import record_verification

        with company.db.transaction() as conn:
            record_verification(conn, company, identity="host-release:" + identity, feature="host_release",
                                scope="Host container health after application; not a functional or rollback test.",
                                evidence={key: report[key] for key in
                                          ("commit", "state", "healthy_services", "postgres_recreated", "error") if key in report})
        print(json.dumps({"state": report["state"], "application_id": identity}))
