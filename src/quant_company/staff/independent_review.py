"""Frozen, blinded review inputs; advisory results remain separate from objective grades."""

import asyncio
import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb
from temporalio import activity

from ..company import fingerprint
from ..contracts import ProviderFault, ProviderRequest
from ..providers.client import RuntimeClient
from ..providers.codex_runner import strict_json
from .review_contract import REVIEW_INSTRUCTIONS, REVIEW_MODEL, RUBRIC_VERSION, IndependentReview


def evidence_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from evidence_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from evidence_strings(child)


class IndependentReviewStore:
    def __init__(self, company):
        self.company, self.db = company, company.db

    def prepare(self):
        settings = self.company.settings
        if not settings.company_staff_review_enabled:
            return {"state": "paused"}
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(71350222)")
            busy = conn.execute("""SELECT 1 FROM turns t JOIN tasks k ON k.id=t.task_id
                JOIN projects p ON p.id=k.project_id WHERE t.status IN ('queued','running','waiting')
                AND t.due_at<=now() AND k.revision=p.revision AND p.status='active' LIMIT 1""").fetchone()
            if busy:
                return {"state": "defer", "reason": "owner_work"}
            today = datetime.now(UTC).astimezone(ZoneInfo("Asia/Seoul")).date()
            count = conn.execute("SELECT count(*) AS n FROM staff_independent_reviews WHERE schedule_day=%s",
                                 (today,)).fetchone()["n"]
            row = conn.execute("""SELECT * FROM staff_independent_reviews WHERE state='running'
                AND next_at<=now() ORDER BY created_at,id LIMIT 1 FOR UPDATE""").fetchone()
            if row and row["schedule_day"] != today:
                if count >= settings.staff_review_daily_limit:
                    return {"state": "idle", "reason": "review_daily_limit"}
                # A carried-over review consumes today's allowance before inference as well.
                conn.execute("UPDATE staff_independent_reviews SET schedule_day=%s WHERE id=%s",
                             (today, row["id"]))
            if row is None:
                if count >= settings.staff_review_daily_limit:
                    return {"state": "idle", "reason": "review_daily_limit"}
                run = conn.execute("""SELECT r.* FROM staff_runs r WHERE r.state='completed'
                    AND r.owner_user=ANY(%s) AND r.final_answer IS NOT NULL
                    AND NOT EXISTS(SELECT 1 FROM staff_independent_reviews v WHERE v.run_id=r.id)
                    ORDER BY r.completed_at DESC,r.id LIMIT 1 FOR UPDATE""",
                                   (settings.slack_allowed_users,)).fetchone()
                if run is None:
                    return {"state": "idle"}
                if fingerprint([run["public_case"], run["answer_key"]]) != run["case_digest"]:
                    return {"state": "blocked", "reason": "case_integrity"}
                material = {"case": run["public_case"], "answer": run["final_answer"]}
                request = ProviderRequest(request_id=f"review-{run['id']}", model=REVIEW_MODEL,
                    prompt=REVIEW_INSTRUCTIONS + "\nREVIEW EVIDENCE:\n" + json.dumps(material, ensure_ascii=False,
                                                                                   sort_keys=True))
                binding = fingerprint([run["case_digest"], run["final_answer"]])
                conn.execute("""INSERT INTO staff_independent_reviews(id,run_id,model,rubric_version,
                    source_digest,request,input_digest,schedule_day) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (request.request_id, run["id"], REVIEW_MODEL, RUBRIC_VERSION, binding,
                     Jsonb(request.model_dump(mode="json")), fingerprint(request.model_dump(mode="json")), today))
                row = conn.execute("SELECT * FROM staff_independent_reviews WHERE id=%s",
                                   (request.request_id,)).fetchone()
            run = conn.execute("SELECT * FROM staff_runs WHERE id=%s", (row["run_id"],)).fetchone()
            if (not self._bound(row, run) or run["owner_user"] not in settings.slack_allowed_users):
                conn.execute("UPDATE staff_independent_reviews SET state='blocked',error='source_changed' WHERE id=%s",
                             (row["id"],))
                return {"state": "blocked", "reason": "source_changed"}
            return {"state": "ready", "request": row["request"]}

    @staticmethod
    def _bound(row, run):
        return (run and run["state"] == "completed" and row["model"] == REVIEW_MODEL
                and row["rubric_version"] == RUBRIC_VERSION
                and fingerprint(row["request"]) == row["input_digest"]
                and fingerprint([run["public_case"], run["answer_key"]]) == run["case_digest"]
                and fingerprint([run["case_digest"], run["final_answer"]]) == row["source_digest"])

    def commit(self, response):
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM staff_independent_reviews WHERE id=%s FOR UPDATE",
                               (response.request_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown independent review")
            body = response.model_dump(mode="json")
            if row["response"] is not None:
                if fingerprint(body) != fingerprint(row["response"]):
                    raise ValueError("Review result cannot be replaced")
                return {"state": row["state"], "duplicate": True}
            run = conn.execute("SELECT * FROM staff_runs WHERE id=%s FOR UPDATE", (row["run_id"],)).fetchone()
            if (row["state"] != "running" or not self._bound(row, run)
                    or run["owner_user"] not in self.company.settings.slack_allowed_users):
                conn.execute("UPDATE staff_independent_reviews SET state='blocked',error='source_changed' WHERE id=%s",
                             (row["id"],))
                return {"state": "blocked", "reason": "source_changed"}
            decision = response.decision
            if (response.provider != "claude" or response.usage.get("actual_model") != REVIEW_MODEL
                    or decision.status != "complete" or len(decision.artifacts) != 1 or decision.tools
                    or decision.delegations or decision.messages or decision.memories or decision.follow_up
                    or decision.artifacts[0].source_ids or response.web_searches):
                raise ValueError("Unexpected review provider, model or action")
            result = IndependentReview.model_validate(strict_json(decision.artifacts[0].content))
            material = {"case": run["public_case"], "answer": run["final_answer"]}
            evidence = [json.dumps(material, ensure_ascii=False, sort_keys=True), *evidence_strings(material)]
            for name in ("grounding", "reasoning", "assumptions", "limitations"):
                for quote in getattr(result, name).evidence_quotes:
                    if not 4 <= len(quote) <= 1200 or not any(quote in text for text in evidence):
                        raise ValueError("Review citation is not present in its evidence")
            advisory = {**result.model_dump(mode="json"), "calibration_status": "not_yet_calibrated",
                        "objective_grade_unchanged": True, "rubric_version": RUBRIC_VERSION}
            conn.execute("""UPDATE staff_independent_reviews SET state='completed',result=%s,response=%s,
                completed_at=now(),error=NULL WHERE id=%s""", (Jsonb(advisory), Jsonb(body), row["id"]))
            return {"state": "completed", "review_id": row["id"], "run_id": str(row["run_id"]),
                    "scope": "advisory_independent_explanation_review"}

    def fault(self, identity, code, seconds=0):
        with self.db.transaction() as conn:
            retryable = code in {"quota", "busy", "unavailable", "auth"}
            conn.execute("""UPDATE staff_independent_reviews SET state=%s,error=%s,
                next_at=now()+make_interval(secs=>%s) WHERE id=%s AND state='running'""",
                ("running" if retryable else "blocked", code, max(60, min(seconds or 900, 604800)), identity))


class IndependentReviewRunner:
    def __init__(self, company, provider=None):
        self.store = IndependentReviewStore(company)
        self.provider = provider

    async def tick(self, heartbeat=False):
        ready = await asyncio.to_thread(self.store.prepare)
        if ready["state"] != "ready":
            return ready
        request = ProviderRequest.model_validate(ready["request"])
        settings = self.store.company.settings
        provider = self.provider or RuntimeClient(settings.staff_review_runtime_url,
            settings.model_runtime_token.get_secret_value(), expected_provider="claude")
        task = asyncio.create_task(provider.run(request))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=5)
                if heartbeat:
                    activity.heartbeat({"review_id": request.request_id})
            return await asyncio.to_thread(self.store.commit, await task)
        except ProviderFault as exc:
            await asyncio.to_thread(self.store.fault, request.request_id, exc.code, exc.retry_after_seconds)
            return {"state": "defer" if exc.code in {"quota", "busy", "unavailable", "auth"} else "blocked",
                    "reason": f"claude_{exc.code}"}
        except ValueError:
            await asyncio.to_thread(self.store.fault, request.request_id, "invalid_review")
            return {"state": "blocked", "reason": "invalid_review"}
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
