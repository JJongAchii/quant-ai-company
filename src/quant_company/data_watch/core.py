"""Only approved, current, registered ETF inputs can enter the read-only worker queue."""

import secrets
from datetime import timedelta

from psycopg.types.json import Jsonb

from ..company import PolicyError, as_json, fingerprint, stable
from ..research.recipes import load_recipe, recipe_digest
from .contracts import CheckAssignment, CheckReceipt, CoreScope
from .store import LOCK, utcnow


class CoreChecks:
    def __init__(self, store):
        self.store, self.company, self.db = store, store.company, store.db

    def eligible(self, conn, job_id, *, execution=True):
        s = self.company.settings
        if not (self.store.authorized() and s.data_watch_core_enabled and (s.company_research_enabled or not execution)):
            return None
        job = conn.execute("""SELECT j.* FROM research_jobs j JOIN projects p ON p.id=j.project_id
            WHERE j.id=%s AND j.recipe_id='kr-etf-p11-replay-v1' AND j.approved_at IS NOT NULL
            AND j.approval_event_id IS NOT NULL AND j.approved_by=p.owner_user AND j.revision=p.revision
            AND p.owner_user=%s AND p.channel=ANY(%s) AND j.state NOT IN ('pending_approval','cancel_requested','cancelled')""",
            (job_id, s.data_watch_owner_user, s.slack_allowed_channels)).fetchone()
        recipe = load_recipe()
        if (not job or job["manifest_digest"] != recipe_digest(recipe)
                or job["manifest"] != recipe.model_dump(mode="json")):
            return None
        return job

    def plan(self):
        if not self.store.authorized() or not self.company.settings.data_watch_core_enabled:
            return 0
        scope, policy, at = CoreScope.from_recipe(load_recipe()), self.store.policy(), utcnow()
        count = 0
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            jobs = conn.execute("""SELECT id FROM research_jobs WHERE recipe_id='kr-etf-p11-replay-v1'
                AND approved_at IS NOT NULL ORDER BY created_at DESC LIMIT 50""").fetchall()
            for row in jobs:
                job = self.eligible(conn, row["id"])
                if not job:
                    continue
                identity = stable(f"data-watch-core:{job['id']}:{scope.digest()}:{policy}:{at.date()}")
                inserted = conn.execute("""INSERT INTO data_watch_checks
                    (id,research_job_id,revision,scope,scope_digest,policy,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING RETURNING id""", (identity, job["id"], job["revision"],
                    Jsonb(scope.model_dump(mode="json")), scope.digest(), policy, at)).fetchone()
                count += bool(inserted)
        return count

    def poll(self):
        if not self.store.authorized():
            return {"assignment": None}
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            # The next day's check supersedes an unstarted old check, never a running lease.
            conn.execute("""UPDATE data_watch_checks c SET state='stale' WHERE state='queued' AND
                (policy<>%s OR EXISTS(SELECT 1 FROM data_watch_checks newer WHERE newer.research_job_id=c.research_job_id
                 AND newer.created_at>c.created_at AND newer.policy=c.policy))""", (self.store.policy(),))
            rows = conn.execute("""SELECT * FROM data_watch_checks WHERE state IN ('queued','running')
                ORDER BY (state='running') DESC,created_at FOR UPDATE LIMIT 50""").fetchall()
            for row in rows:
                job = self.eligible(conn, row["research_job_id"])
                if (not job or row["policy"] != self.store.policy() or row["revision"] != job["revision"]
                        or row["scope"] != CoreScope.from_recipe(load_recipe()).model_dump(mode="json")):
                    conn.execute("UPDATE data_watch_checks SET state='stale' WHERE id=%s", (row["id"],))
                    continue
                if row["state"] != "running" or row["lease_until"] <= utcnow():
                    row["lease_token"] = secrets.token_hex(32)
                    conn.execute("""UPDATE data_watch_checks SET state='running',lease_token=%s,lease_until=%s
                        WHERE id=%s""", (row["lease_token"], utcnow() + timedelta(minutes=10), row["id"]))
                value = CheckAssignment(id=row["id"], research_job_id=row["research_job_id"], revision=row["revision"],
                                        scope=CoreScope.model_validate(row["scope"]), scope_digest=row["scope_digest"],
                                        lease_token=row["lease_token"])
                return {"assignment": value.model_dump(mode="json")}
        return {"assignment": None}

    def accept(self, check_id, lease, value: CheckReceipt):
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            row = conn.execute("SELECT * FROM data_watch_checks WHERE id=%s FOR UPDATE", (check_id,)).fetchone()
            job = self.eligible(conn, row["research_job_id"]) if row else None
            if (not job or row["policy"] != self.store.policy() or row["revision"] != job["revision"]
                    or row["scope"] != CoreScope.from_recipe(load_recipe()).model_dump(mode="json")
                    or not secrets.compare_digest(row["lease_token"] or "", lease)
                    or row["state"] not in {"running", "complete"}):
                raise PolicyError("Data check authorization or lease changed")
            if (str(value.check_id) != str(row["id"]) or value.scope_digest != row["scope_digest"]
                    or value.checked_at > utcnow() + timedelta(minutes=5)
                    or value.checked_at < row["created_at"] - timedelta(minutes=5)):
                raise PolicyError("Data check receipt scope or time mismatch")
            if value.state == "checked":
                from .checker import errors_for, required_columns

                hashes = {k: v.sha256 for k, v in value.files.items()}
                if hashes != row["scope"]["input_files"]:
                    raise PolicyError("Data check receipt input identity mismatch")
                for name, item in value.files.items():
                    if name.endswith(".parquet") and (item.rows is None or not item.columns):
                        raise PolicyError("Data check parquet coverage missing")
                    if item.errors != errors_for(name, item):
                        raise PolicyError("Data check findings contradict measurements")
                    if name.endswith(".parquet") and (
                        set(item.nulls) != set(required_columns(name)) & set(item.columns)
                        or any(n < 0 or n > item.rows for n in item.nulls.values())
                        or (item.rows > 0 and not item.errors and (item.min_date is None or item.max_date is None))
                    ):
                        raise PolicyError("Data check full-row coverage missing")
                if sum(item.bytes for item in value.files.values()) > 512 * 1024 * 1024:
                    raise PolicyError("Data check read budget exceeded")
            receipt = value.model_dump(mode="json")
            digest = fingerprint(receipt)
            if row["receipt_digest"]:
                if row["receipt_digest"] != digest:
                    raise PolicyError("Data check already has a different committed receipt")
                return {"ok": True, "duplicate": True}
            conn.execute("""UPDATE data_watch_checks SET state='complete',receipt=%s,receipt_digest=%s,
                checked_at=%s WHERE id=%s""", (Jsonb(receipt), digest, value.checked_at, check_id))
            return {"ok": True, "duplicate": False}

    def status(self, conn):
        rows = conn.execute("""SELECT DISTINCT ON (c.research_job_id) c.*,p.title,p.channel,p.thread_ts
            FROM data_watch_checks c JOIN research_jobs j ON j.id=c.research_job_id
            JOIN projects p ON p.id=j.project_id WHERE p.owner_user=%s AND p.channel=ANY(%s) AND c.policy=%s
            ORDER BY c.research_job_id,c.created_at DESC LIMIT 50""", (self.company.settings.data_watch_owner_user,
            self.company.settings.slack_allowed_channels, self.store.policy())).fetchall()
        result = []
        for row in rows:
            if not self.eligible(conn, row["research_job_id"]):
                continue
            receipt = row["receipt"]
            problem = (receipt.get("error") or "input_quality_failed" if receipt and (
                receipt.get("error") or any(item.get("errors") for item in receipt["files"].values())) else None)
            result.append({"id": str(row["id"]), "research_job_id": str(row["research_job_id"]),
                           "title": row["title"], "state": row["state"], "scope_digest": row["scope_digest"],
                           "lake_id": row["scope"]["lake_id"], "checked_at": row["checked_at"],
                           "receipt_digest": row["receipt_digest"], "receipt": receipt, "problem": problem,
                           "url": f"https://app.slack.com/archives/{row['channel']}/p{row['thread_ts'].replace('.', '')}"
                           if row["thread_ts"] else None})
        return as_json(result)
