from datetime import timedelta

import pytest

from quant_company.tech_feed.recovery import candidates, legacy_policy, reconcile

from .test_tech_feed import ingest, outgoing, queue, rss, tech  # noqa: F401


def legacy_rows(tech):  # noqa: F811
    queue(tech)
    channels = tech.company.settings.slack_allowed_channels.copy()
    users = tech.company.settings.slack_allowed_users.copy()
    policy = legacy_policy(tech, channels, users)
    with tech.db.transaction() as conn:
        conn.execute("UPDATE tech_feed_publications SET policy_digest=%s", (policy,))
        conn.execute("UPDATE outbox SET status='stale',error='tech_feed_policy_or_freshness_changed'")
    tech.company.settings.slack_allowed_channels.append("CUNRELATED")
    return [str(outgoing(tech)[0]["id"])], channels, users, policy


def test_plan_is_read_only_and_exact_apply_preserves_cards_and_ids(tech):  # noqa: F811
    args = legacy_rows(tech)
    assert candidates(tech, args[-1]) == args[0]
    before = outgoing(tech)[0]
    plan = reconcile(tech, *args)
    assert plan["state"] == "planned" and plan["database_writes"] == 0
    assert outgoing(tech)[0] == before
    with pytest.raises(ValueError, match="plan_changed"):
        reconcile(tech, *args, apply_digest="wrong")
    assert outgoing(tech)[0] == before
    result = reconcile(tech, *args, apply_digest=plan["plan_digest"])
    assert result["database_writes"] == 2 and result["direct_slack_writes"] == result["model_calls"] == 0
    after = outgoing(tech)[0]
    assert after["status"] == "pending" and after["error"] is None
    for field in ("id", "text", "agent", "attempts", "sent_ts", "started_at", "created_at"):
        assert after[field] == before[field]
    with tech.db.transaction() as conn:
        assert conn.execute("SELECT policy_digest FROM tech_feed_publications").fetchone()["policy_digest"] == tech.policy()
    with pytest.raises(ValueError, match="certainly_unsent"):
        reconcile(tech, *args, apply_digest=plan["plan_digest"])


@pytest.mark.parametrize("mutation", ["uncertain", "delivered", "attempted", "started", "timestamp",
                                       "expired", "card", "source", "owner", "channel"])
def test_recovery_rejects_ambiguous_changed_unauthorized_or_expired_rows(tech, mutation):  # noqa: F811
    args = legacy_rows(tech)
    plan = reconcile(tech, *args)
    with tech.db.transaction() as conn:
        if mutation in {"uncertain", "delivered"}:
            conn.execute("UPDATE outbox SET status=%s", (mutation,))
        elif mutation == "attempted":
            conn.execute("UPDATE outbox SET attempts=1")
        elif mutation == "started":
            conn.execute("UPDATE outbox SET started_at=now()")
        elif mutation == "timestamp":
            conn.execute("UPDATE outbox SET sent_ts='100.1'")
        elif mutation == "card":
            conn.execute("UPDATE outbox SET text='modified'")
        elif mutation == "source":
            conn.execute("UPDATE tech_feed_items SET source_digest='modified'")
    if mutation == "expired":
        tech.clock[0] += timedelta(days=4)
    elif mutation == "owner":
        tech.company.settings.slack_allowed_users.clear()
    elif mutation == "channel":
        tech.company.settings.slack_allowed_channels.clear()
    before = outgoing(tech)[0]
    with pytest.raises(ValueError):
        reconcile(tech, *args, apply_digest=plan["plan_digest"])
    assert outgoing(tech)[0] == before


def test_multirow_recovery_is_atomic_and_never_replays_old_sources(tech):  # noqa: F811
    args = legacy_rows(tech)
    ingest(tech, rss(tech.clock[0], "second"))
    rows = outgoing(tech)
    with tech.db.transaction() as conn:
        conn.execute("UPDATE tech_feed_publications SET policy_digest=%s", (args[-1],))
        conn.execute("UPDATE outbox SET status='stale',error='tech_feed_policy_or_freshness_changed'")
    identities = [str(row["id"]) for row in rows]
    args = (identities, *args[1:])
    plan = reconcile(tech, *args)
    with tech.db.transaction() as conn:
        conn.execute("UPDATE outbox SET attempts=1 WHERE id=%s", (identities[-1],))
    with pytest.raises(ValueError, match="certainly_unsent"):
        reconcile(tech, *args, apply_digest=plan["plan_digest"])
    assert all(row["status"] == "stale" for row in outgoing(tech))
    with pytest.raises(ValueError, match="scope_invalid"):
        reconcile(tech, identities + identities, *args[1:])
