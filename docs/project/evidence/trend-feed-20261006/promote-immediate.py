"""Apply the user's immediate inspected-delivery gate; do not fabricate three elapsed days."""

import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

ROOT = Path("/opt/quant-company/operator-releases/trend-feed-20261006")
STATE = Path("/var/lib/quant-company")
REVIEW = STATE / "releases/trend-feed-20261006-immediate-review.json"
JOURNAL = STATE / "releases/trend-feed-20261006-immediate-activation.json"
OBSERVATION = STATE / "releases/trend-feed-20261006-observation.json"
APPROVAL = "chat-user-immediate-brief-inspect-activate-20261006"


def main(operator):
    if JOURNAL.exists():
        previous = json.loads(JOURNAL.read_text())
        print(json.dumps({"state": previous["state"], "automatic_retry": False}))
        return
    review = json.loads(REVIEW.read_text())
    cutover = json.loads(operator.JOURNAL.read_text())
    if (review["approval"] != APPROVAL or not review["valid"] or not review["direct_inspection_passed"]
            or not review["channel_ui"]["body_matches"] or not review["channel_ui"]["channel_matches"]
            or cutover["state"] != "preview_active"):
        raise RuntimeError("immediate_acceptance_missing")
    identity = str(UUID(review["id"]))
    receipt = operator.sql("SELECT row_to_json(s) FROM (SELECT o.id,o.status,o.sent_ts,o.channel,o.attempts,o.error,"
                           "md5(m.text) AS body_md5 FROM outbox o JOIN messages m ON m.id=o.id "
                           "WHERE o.id='" + identity + "' AND m.author='trend_scout')s")
    if (not receipt or receipt["status"] != "delivered" or not receipt["sent_ts"]
            or receipt["channel"] != operator.CHANNEL or receipt["error"]
            or receipt["body_md5"] != hashlib.md5(review["text"].encode()).hexdigest()):
        raise RuntimeError("immediate_delivery_or_body_not_verified")
    before = operator.inspect()
    if (operator.lanes_busy(before) or operator.sql("SELECT count(*) FROM outbox WHERE status='sending'")
            or operator.run(["systemctl", "show", "quant-company-release.service", "-p", "ActiveState", "--value"]).strip() != "inactive"):
        print(json.dumps({"state": "waiting_for_quiet_cutover", "publish_enabled": False}))
        return
    if any(before[name]["Id"] != cutover["target_ids"][name] for name in operator.TARGETS):
        raise RuntimeError("target_changed_requires_review")
    candidate = review["renderer_image"]
    if (candidate["changed_files"] != ["trend_feed/editor.py"] or not candidate["base_config_preserved"]
            or any(before[name]["Image"] != candidate["base_image"] for name in operator.TARGETS)):
        raise RuntimeError("renderer_candidate_not_qualified")
    now = datetime.datetime.now(datetime.UTC)
    first_day = (now.astimezone(ZoneInfo("Asia/Seoul")).date() + datetime.timedelta(days=1)).isoformat()
    record = {"state": "promoting", "approval": APPROVAL, "acceptance_mode": "immediate_inspected_real_delivery",
              "started_at": now.isoformat(), "manual_delivery": receipt, "three_day_observation_completed": False,
              "three_day_wait_replaced_by_user": True, "first_scheduled_day": first_day,
              "preview_backfill": False, "automatic_message_replay": False}

    def save():
        operator.atomic(JOURNAL, (json.dumps(record, indent=2) + "\n").encode())

    save()
    oldpause = operator.sql("SELECT row_to_json(s) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)s")
    operator.sql("WITH s AS (UPDATE runtime_control SET paused_until=now()+interval '5 minutes',"
                 "reason='deployment_trend_feed_immediate_publication' WHERE id=1 RETURNING id) SELECT json_agg(id) FROM s")
    try:
        oldenv = operator.ENV.read_bytes()
        operator.atomic(STATE / "releases/trend-feed-immediate-publication.env.before", oldenv)
        lines = oldenv.decode().splitlines()
        if lines.count("TREND_FEED_PUBLISH_ENABLED=false") != 1:
            raise RuntimeError("publication_flag_changed")
        operator.atomic(operator.ENV, ("\n".join("TREND_FEED_PUBLISH_ENABLED=true" if line == "TREND_FEED_PUBLISH_ENABLED=false"
                                                  else line for line in lines) + "\n").encode())
        for name in operator.TARGETS:
            path = STATE / "config" / ("trend-feed-20261006-" + name + ".compose.json")
            content = json.loads(path.read_text())
            operator.atomic(STATE / "releases" / ("trend-feed-immediate-publication-" + name + ".before.json"), path.read_bytes())
            content["services"][name]["environment"]["TREND_FEED_PUBLISH_ENABLED"] = "true"
            content["services"][name]["image"] = candidate["image_tag"]
            operator.atomic(path, json.dumps(content).encode())
            operator.run([*operator.compose(before[name], path), "up", "-d", "--no-deps", "--no-build", "--pull", "never",
                          "--force-recreate", "--wait", "--wait-timeout", "120", name], timeout=180)
            fresh = operator.inspect()[name]
            expected = operator.signature(before[name])
            expected["env"]["TREND_FEED_PUBLISH_ENABLED"] = "true"
            if (fresh["Image"] != candidate["image"] or not fresh["State"]["Running"]
                    or fresh["State"]["OOMKilled"] or operator.signature(fresh) != expected):
                raise RuntimeError("publication_runtime_drift")
        fresh = operator.inspect()
        if any(fresh[n]["Id"] != r["Id"] for n, r in before.items() if n not in operator.TARGETS):
            raise RuntimeError("unrelated_container_replaced")
        record.update(state="publication_enabled", publish_enabled=True,
                      completed_at=datetime.datetime.now(datetime.UTC).isoformat(),
                      target_ids={n: fresh[n]["Id"] for n in operator.TARGETS},
                      target_images={n: fresh[n]["Image"] for n in operator.TARGETS},
                      target_pids={n: fresh[n]["HostConfig"]["PidsLimit"] for n in operator.TARGETS},
                      unrelated_container_ids_preserved=True)
        save()
        operator.atomic(OBSERVATION, (json.dumps({**record, "first_delivery_verified": False,
                                                "days": [], "qualification_days": []}, indent=2) + "\n").encode())
        cutover.update(state="publication_enabled", publish_enabled=True, target_ids=record["target_ids"],
                       publication_gate=str(JOURNAL), acceptance_mode=record["acceptance_mode"],
                       three_day_wait_replaced_by_user=True, immediate_brief_receipt=receipt)
        operator.save(cutover)
        print(json.dumps(record))
    except BaseException as exc:
        record.update(state="reconciliation_required", error=type(exc).__name__ + ":" + str(exc))
        save()
        raise
    finally:
        operator.sql("WITH s AS (UPDATE runtime_control SET paused_until="
                     + ("'" + oldpause["paused_until"] + "'::timestamptz" if oldpause["paused_until"] else "NULL")
                     + ",reason=" + ("'" + oldpause["reason"].replace("'", "''") + "'" if oldpause["reason"] else "NULL")
                     + " WHERE id=1 RETURNING id) SELECT json_agg(id) FROM s")


if __name__ == "__main__":
    os.umask(0o077)
    spec = importlib.util.spec_from_file_location("trend_operator", ROOT / "production-cutover.py")
    operator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operator)
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main(operator)
