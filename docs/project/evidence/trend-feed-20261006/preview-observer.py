"""Host deployment gate: observe real preview days, then apply the authorized flag change."""

import datetime
import fcntl
import importlib.util
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

STATE = Path("/var/lib/quant-company")
ROOT = Path("/opt/quant-company/operator-releases/trend-feed-20261006")
CUTOVER = STATE / "releases/trend-feed-20261006-cutover.json"
JOURNAL = STATE / "releases/trend-feed-20261006-observation.json"
ENV = STATE / "config/runtime.env"


def atomic(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("wb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    temp.chmod(0o600)
    temp.replace(path)


def save(record):
    atomic(JOURNAL, (json.dumps(record, indent=2) + "\n").encode())


def completed_days(days):
    valid = [d for d in days if d.get("valid") and d.get("stable")]
    for index in range(len(valid) - 2):
        group = valid[index:index + 3]
        dates = [datetime.date.fromisoformat(d["day"]) for d in group]
        if dates[1] - dates[0] == dates[2] - dates[1] == datetime.timedelta(days=1):
            return group
    return []


def main():
    cutover = json.loads(CUTOVER.read_text())
    if cutover["state"] not in {"preview_active", "publication_enabled"}:
        print(json.dumps({"state": "not_preview_active", "cutover": cutover["state"]}))
        return
    spec = importlib.util.spec_from_file_location("trend_operator", ROOT / "production-cutover.py")
    operator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operator)
    if cutover["state"] == "publication_enabled":
        record = json.loads(JOURNAL.read_text())
        day = datetime.date.fromisoformat(record["first_scheduled_day"])
        receipt = operator.sql("SELECT row_to_json(s) FROM (SELECT d.day,d.state,o.status,o.sent_ts "
                               "FROM trend_feed_digests d LEFT JOIN outbox o ON o.id=d.id "
                               "WHERE d.channel='C0C6WTA9ECV' AND d.day='" + str(day) + "')s")
        record["first_delivery"] = receipt
        if receipt and receipt["status"] == "delivered" and receipt["sent_ts"]:
            record["first_delivery_verified"] = True
        save(record)
        print(json.dumps({"state": record["state"], "first_delivery": receipt,
                          "first_delivery_verified": record.get("first_delivery_verified", False),
                          "automatic_message_replay": False}))
        return
    now = datetime.datetime.now(datetime.UTC)
    first_day = (datetime.datetime.fromisoformat(cutover["completed_at"]).astimezone(ZoneInfo("Asia/Seoul"))
                 + datetime.timedelta(days=1)).date().isoformat()
    record = json.loads(JOURNAL.read_text()) if JOURNAL.exists() else {
        "state": "observing", "start_day": first_day, "first_observations": {}, "days": []}
    if record["state"] != "observing":
        print(json.dumps({"state": record["state"], "automatic_retry": False}))
        return
    reader = operator.inspect()["news-worker"]
    override = STATE / "config/trend-feed-20261006-news-worker.compose.json"
    # A second Python interpreter must not consume the resident worker's memory limit.
    output = operator.run([*operator.compose(reader, override), "run", "--rm", "--no-deps", "--pull", "never",
                           "-T", "news-worker", "python", "-", first_day],
                          input=(ROOT / "preview-validator.py").read_text())
    observed = json.loads(output)
    if observed["publish_enabled"] or not observed["authorized"]:
        raise RuntimeError("unexpected_preview_policy")
    for day in observed["days"]:
        first = record["first_observations"].get(day["id"])
        if first is None:
            first = {"fingerprint": day["fingerprint"], "at": now.isoformat()}
            record["first_observations"][day["id"]] = first
        day["stable"] = (day["fingerprint"] == first["fingerprint"]
                         and now - datetime.datetime.fromisoformat(first["at"]) >= datetime.timedelta(minutes=9))
        if day["fingerprint"] != first["fingerprint"]:
            day["valid"] = False
            day["reasons"].append("committed_preview_changed_between_observations")
    record.update(days=observed["days"], checked_at=now.isoformat())
    eligible = completed_days(record["days"])
    save(record)
    if not eligible:
        print(json.dumps({"state": "observing", "start_day": first_day, "days": record["days"],
                          "publish_enabled": False}))
        return
    connection = cutover.get("connection_test", {})
    if connection.get("status") != "delivered" or not connection.get("sent_ts") or connection.get("channel") != "C0C6WTA9ECV":
        print(json.dumps({"state": "waiting_for_connection_receipt", "publish_enabled": False}))
        return
    before = operator.inspect()
    if (operator.lanes_busy(before) or operator.sql("SELECT count(*) FROM outbox WHERE status='sending'")
            or operator.run(["systemctl", "show", "quant-company-release.service", "-p", "ActiveState", "--value"]).strip() != "inactive"):
        print(json.dumps({"state": "waiting_for_quiet_cutover", "publish_enabled": False}))
        return
    for name in operator.TARGETS:
        if before[name]["Id"] != cutover["target_ids"][name]:
            raise RuntimeError("preview_cohort_changed_requires_review")
    record.update(state="promoting", qualification_days=[d["day"] for d in eligible],
                  promotion_started_at=now.isoformat())
    save(record)
    oldpause = operator.sql("SELECT row_to_json(s) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)s")
    operator.sql("WITH s AS (UPDATE runtime_control SET paused_until=now()+interval '5 minutes',"
                 "reason='deployment_trend_feed_publication' WHERE id=1 RETURNING id) SELECT json_agg(id) FROM s")
    try:
        oldenv = ENV.read_bytes()
        atomic(STATE / "releases/trend-feed-publication.env.before", oldenv)
        lines = oldenv.decode().splitlines()
        if lines.count("TREND_FEED_PUBLISH_ENABLED=false") != 1:
            raise RuntimeError("publication_flag_changed")
        atomic(ENV, ("\n".join("TREND_FEED_PUBLISH_ENABLED=true" if line == "TREND_FEED_PUBLISH_ENABLED=false" else line
                              for line in lines) + "\n").encode())
        for name in operator.TARGETS:
            path = STATE / "config" / ("trend-feed-20261006-" + name + ".compose.json")
            content = json.loads(path.read_text())
            atomic(STATE / "releases" / ("trend-feed-publication-" + name + ".before.json"), path.read_bytes())
            content["services"][name]["environment"]["TREND_FEED_PUBLISH_ENABLED"] = "true"
            atomic(path, json.dumps(content).encode())
            command = operator.compose(before[name], path)
            operator.run([*command, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate",
                          "--wait", "--wait-timeout", "120", name], timeout=180)
            fresh = operator.inspect()[name]
            expected = operator.signature(before[name])
            expected["env"]["TREND_FEED_PUBLISH_ENABLED"] = "true"
            if (fresh["Image"] != before[name]["Image"] or not fresh["State"]["Running"]
                    or fresh["State"]["OOMKilled"] or operator.signature(fresh) != expected):
                raise RuntimeError("publication_runtime_drift")
        fresh = operator.inspect()
        if any(fresh[n]["Id"] != r["Id"] for n, r in before.items() if n not in operator.TARGETS):
            raise RuntimeError("unrelated_container_replaced")
        record.update(state="publication_enabled", publish_enabled=True, promotion_completed_at=datetime.datetime.now(datetime.UTC).isoformat(),
                      first_scheduled_day=(now.astimezone(ZoneInfo("Asia/Seoul")).date() + datetime.timedelta(days=1)).isoformat(),
                      preview_backfill=False)
        save(record)
        cutover.update(state="publication_enabled", publish_enabled=True, publication_gate=str(JOURNAL),
                       target_ids={n: fresh[n]["Id"] for n in operator.TARGETS})
        operator.save(cutover)
        print(json.dumps(record))
    except BaseException as exc:
        record.update(state="reconciliation_required", error=type(exc).__name__ + ":" + str(exc))
        save(record)
        raise
    finally:
        operator.sql("WITH s AS (UPDATE runtime_control SET paused_until="
                     + ("'" + oldpause["paused_until"] + "'::timestamptz" if oldpause["paused_until"] else "NULL")
                     + ",reason=" + ("'" + oldpause["reason"].replace("'", "''") + "'" if oldpause["reason"] else "NULL")
                     + " WHERE id=1 RETURNING id) SELECT json_agg(id) FROM s")


if __name__ == "__main__":
    os.umask(0o077)
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main()
