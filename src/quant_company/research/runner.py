"""Short, repeatable server reconciliation; never executes a backtest or an LLM here."""

import asyncio
import hashlib
import json
from datetime import timedelta
from pathlib import Path

from psycopg.types.json import Jsonb
from temporalio import activity

from ..company import as_json, fingerprint, now, stable
from ..task_control import waiting_router
from .contracts import Assignment, Recipe
from .store import STATE_TEXT, ResearchStore


class ReportPublisher:
    def __init__(self, company):
        self.company = company

    def publish(self, job_id, html, archive):
        settings = self.company.settings
        body = html.encode()
        digest = hashlib.sha256(body).hexdigest()
        root = settings.research_artifact_dir / job_id
        root.mkdir(parents=True, exist_ok=True)
        report = root / (digest + ".html")
        report.write_bytes(body)
        if settings.fixture_mode and not settings.research_report_bucket:
            return {"uri": report.as_uri(), "view_url": report.as_uri(), "html_sha256": digest,
                    "publication": "local_fixture", "expires_at": None}
        if not settings.research_report_bucket:
            raise ValueError("research_report_storage_not_configured")
        from botocore.session import get_session

        credentials = {}
        if settings.research_s3_credentials_file:
            value = json.loads(settings.research_s3_credentials_file.read_text())
            credentials = {"aws_access_key_id": value["AccessKeyId"],
                           "aws_secret_access_key": value["SecretAccessKey"]}
        # The production image's locked lake extra includes botocore (not boto3).
        s3 = get_session().create_client("s3", region_name="ap-northeast-2", **credentials)
        prefix = f"{settings.research_report_prefix}/{job_id}"
        key = f"{prefix}/{digest}.html"
        with archive.open("rb") as stream:
            archive_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        with archive.open("rb") as stream:
            s3.put_object(Bucket=settings.research_report_bucket, Key=f"{prefix}/{archive_digest}.zip",
                          Body=stream, ContentType="application/zip", ServerSideEncryption="AES256",
                          Metadata={"sha256": archive_digest})
        s3.put_object(Bucket=settings.research_report_bucket, Key=key, Body=body,
                      ContentType="text/html; charset=utf-8", ServerSideEncryption="AES256",
                      Metadata={"sha256": digest})
        url = s3.generate_presigned_url("get_object", Params={"Bucket": settings.research_report_bucket, "Key": key},
                                        ExpiresIn=604800)
        return {"uri": f"s3://{settings.research_report_bucket}/{key}", "view_url": url,
                "archive_uri": f"s3://{settings.research_report_bucket}/{prefix}/{archive_digest}.zip",
                "html_sha256": digest, "publication": "s3",
                "expires_at": (now() + timedelta(days=7)).isoformat()}


class ResearchRunner:
    def __init__(self, company, publisher=None):
        self.company = company
        self.store = ResearchStore(company)
        self.publisher = publisher or ReportPublisher(company)

    def _notify(self):
        with self.company.db.transaction() as conn:
            rows = conn.execute("""SELECT id FROM research_jobs WHERE
                state IN ('failed','cancelled','awaiting_audit','uncertain')
                AND notified_state IS DISTINCT FROM state ORDER BY created_at LIMIT 20""").fetchall()
        for item in rows:
            with self.company.db.transaction() as conn:
                project, row = self.store._locked(conn, item["id"])
                if (row["notified_state"] == row["state"] or
                        row["state"] not in {"failed", "cancelled", "awaiting_audit", "uncertain"}):
                    continue
                if row["revision"] == project["revision"]:
                    text = f"연구 {row['id']}: {STATE_TEXT[row['state']]}."
                    if row["state"] in {"awaiting_audit", "uncertain", "failed"}:
                        text += " 현재 확인되지 않은 성과 수치는 공개하지 않았습니다. 실행·수신 기록은 보존했습니다."
                    self.company._message(conn, project, row["task_id"], "director", "status", text, notify_owner=True)
                conn.execute("UPDATE research_jobs SET notified_state=state WHERE id=%s", (row["id"],))

    def tick(self):
        self._notify()
        if not self.company.settings.company_research_enabled:
            return {"state": "disabled"}
        with self.company.db.transaction() as conn:
            row = conn.execute("SELECT id FROM research_jobs WHERE state='received' ORDER BY updated_at,id LIMIT 1").fetchone()
            if not row:
                return {"state": "idle"}
            project, row = self.store._locked(conn, row["id"])
            if row["state"] != "received" or project["status"] != "active" or waiting_router(conn, project["id"]):
                return {"state": "deferred"}
            # Snapshot only; expensive hashing, rendering and S3 writes happen outside the transaction.
            row = as_json(row)
        from .report import build_report, validate_bundle

        assignment = Assignment(job_id=row["id"], project_id=row["project_id"], revision=row["revision"],
                                recipe_id=row["recipe_id"], manifest_digest=row["manifest_digest"],
                                approval_event_id=row["approval_event_id"], lease_token=row["lease_token"], action="reconcile")
        archive = Path(row["artifact_path"])
        try:
            with archive.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != row["artifact_sha256"]:
                    raise ValueError("stored_archive_digest_changed")
            validated = validate_bundle(archive, Recipe.model_validate(row["manifest"]), assignment, row["company_commit"])
            report = build_report(validated)
        except (ValueError, OSError):
            with self.company.db.transaction() as conn:
                _, current = self.store._locked(conn, row["id"])
                if current["state"] == "received":
                    conn.execute("""UPDATE research_jobs SET state='awaiting_audit',error='result_evidence_unverified',
                        updated_at=now() WHERE id=%s""", (row["id"],))
                    self.company._event(conn, "research_evidence_withheld", {"job_id": row["id"],
                                        "artifact_sha256": row["artifact_sha256"]}, row["project_id"])
            self._notify()
            return {"state": "awaiting_audit"}
        # Same object keys and content on retry; a write followed by a lost response is reconciled
        # by publishing the same digest, never by launching another experiment.
        try:
            published = self.publisher.publish(row["id"], report["html"], archive)
            published["renderer_company_commit"] = self.company.settings.company_code_commit
        except Exception:
            with self.company.db.transaction() as conn:
                project, current = self.store._locked(conn, row["id"])
                if current["state"] == "received":
                    conn.execute("UPDATE research_jobs SET error='publication_unavailable' WHERE id=%s", (row["id"],))
                    if current["notified_state"] != "publication_unavailable":
                        self.company._message(conn, project, current["task_id"], "director", "status",
                            "연구 결과를 수신했지만 보고서 저장소 연결을 확인 중입니다. 수신 자료를 보존하고 "
                            "저장·게시를 다시 확인합니다. 연구를 재실행하지 않습니다.", notify_owner=True)
                        conn.execute("UPDATE research_jobs SET notified_state='publication_unavailable' WHERE id=%s",
                                     (row["id"],))
            raise RuntimeError("research_publication_unavailable") from None
        with self.company.db.transaction() as conn:
            project, current = self.store._locked(conn, row["id"])
            if current["state"] != "received":
                return {"state": "superseded"}
            if project["status"] != "active" or waiting_router(conn, project["id"]):
                return {"state": "deferred"}
            source_id = "research:" + row["id"]
            source = {"kind": "equivalent_replay", "job_id": row["id"], "revision": row["revision"],
                      "manifest_digest": row["manifest_digest"], "scientific_trials_added": 0,
                      "summary": report["summary"], "report": published,
                      "interpretation": "고정 P11 결과와 일치하는 개발구간 재현. 원래 감사의 현재 파일 일치를 확인했으며 "
                                        "새 연구·확증 판정이 아니다. 기준선·초과성과는 미측정이다."}
            content = json.dumps(source, ensure_ascii=False, allow_nan=False)
            if len(content) > 100000:
                raise ValueError("research_source_too_large")
            conn.execute("""INSERT INTO sources(id,title,uri,content,available_at,project_id,approved,synthetic,metadata)
                VALUES (%s,%s,%s,%s,now(),%s,true,%s,%s)""", (source_id, "고정 국내 ETF 연구 재현 보고서",
                             published["uri"], content, project["id"], self.company.settings.fixture_mode,
                             Jsonb({"job_id": row["id"], "manifest_digest": row["manifest_digest"],
                                    "audit_scope_digest": row["manifest"]["scope_digest"], "kind": "equivalent_replay"})))
            task = self.company._new_task(conn, project, "director",
                f"서버가 연구 {row['id']}의 실제 결과 파일과 기존 독립 감사 범위 일치를 확인했습니다. "
                f"출처 {source_id}를 read_source로 읽고, 고정 결과 재현 완료·핵심 결과·한계와 보고서 링크를 "
                "소유자에게 짧게 최종 보고하세요. 새 실험을 수행하거나 추가 위임하지 마세요. "
                "공학적 재현이 새 전략 탐색·확증·투자 승인을 의미하지 않는다고 명시하세요.",
                task_id=stable("research-report:" + row["id"]), status_only=True, kind="answer")
            self.company._new_turn(conn, task)
            conn.execute("""INSERT INTO artifacts(id,project_id,task_id,revision,title,content,source_ids,digest)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                         (stable("research-artifact:" + row["id"]), project["id"], task["id"], project["revision"],
                          "고정 연구 재현 HTML", content, Jsonb([source_id]), fingerprint(source)))
            conn.execute("""UPDATE research_jobs SET state='completed',report=%s,notified_state='completed',
                error=NULL,updated_at=now() WHERE id=%s""",
                         (Jsonb({**published, "source_id": source_id, "director_task_id": str(task["id"])}), row["id"]))
            self.company._event(conn, "research_report_ready", {"job_id": row["id"], "source_id": source_id,
                                "artifact_sha256": row["artifact_sha256"], "html_sha256": published["html_sha256"],
                                "director_task_id": str(task["id"]), "scientific_trials_added": 0}, project["id"])
        return {"state": "completed", "job_id": row["id"]}

    @activity.defn(name="company_research_tick")
    async def activity_tick(self):
        task = asyncio.create_task(asyncio.to_thread(self.tick))
        while not task.done():
            activity.heartbeat("research_reconciliation")
            await asyncio.wait({task}, timeout=5)
        return task.result()
