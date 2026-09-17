"""Read-only, owner-scoped history. Counts are signals for diagnosis, never quality scores."""

import json
from datetime import timedelta

from ..company import as_json
from .evaluation import employee_context
from .policy import SECRET, digest


def safe_rows(rows):
    output = as_json(rows)
    for row in output:
        if SECRET.search(json.dumps(row)):
            key = row["key"]
            row.clear()
            row.update(key=key, omitted="possible_secret; operator review required")
    return output


def review_snapshot(conn, company, owners, at):
    """Caller uses one repeatable-read transaction for observations and their historical context."""
    periods = []
    for start_days, end_days in [(30, 7), (7, 0)]:
        start, end = at - timedelta(days=start_days), at - timedelta(days=end_days)
        turns = conn.execute("""
            SELECT k.agent,t.status,count(*) AS turns,sum(t.attempts) AS attempts,
                   count(*) FILTER (WHERE t.attempts>1) AS retried_turns
            FROM turns t JOIN tasks k ON k.id=t.task_id JOIN projects p ON p.id=k.project_id
            WHERE p.owner_user=ANY(%s) AND (p.channel=ANY(%s) OR p.channel LIKE 'D%%') AND t.created_at>=%s AND t.created_at<%s
            GROUP BY k.agent,t.status ORDER BY k.agent,t.status
            """, (owners, company.settings.slack_allowed_channels, start, end)).fetchall()
        tasks = conn.execute("""
            SELECT k.agent,k.status,count(*) AS tasks,max(k.depth) AS max_depth
            FROM tasks k JOIN projects p ON p.id=k.project_id
            WHERE p.owner_user=ANY(%s) AND (p.channel=ANY(%s) OR p.channel LIKE 'D%%') AND k.created_at>=%s AND k.created_at<%s
            GROUP BY k.agent,k.status ORDER BY k.agent,k.status
            """, (owners, company.settings.slack_allowed_channels, start, end)).fetchall()
        edges = conn.execute("""
            SELECT parent.agent AS sender,k.agent AS recipient,count(*) AS delegations
            FROM tasks k JOIN tasks parent ON parent.id=k.parent_id JOIN projects p ON p.id=k.project_id
            WHERE p.owner_user=ANY(%s) AND (p.channel=ANY(%s) OR p.channel LIKE 'D%%') AND k.created_at>=%s AND k.created_at<%s
            GROUP BY parent.agent,k.agent ORDER BY parent.agent,k.agent
            """, (owners, company.settings.slack_allowed_channels, start, end)).fetchall()
        repeated = conn.execute("""
            SELECT count(*) AS repeated_groups,COALESCE(sum(n-1),0) AS additional_tasks FROM (
                SELECT count(*) AS n FROM tasks k JOIN projects p ON p.id=k.project_id
                WHERE p.owner_user=ANY(%s) AND (p.channel=ANY(%s) OR p.channel LIKE 'D%%') AND k.created_at>=%s AND k.created_at<%s AND k.parent_id IS NOT NULL
                GROUP BY k.project_id,k.revision,k.agent,k.instruction HAVING count(*)>1
            ) groups
            """, (owners, company.settings.slack_allowed_channels, start, end)).fetchone()
        periods.append({"start": start, "end": end, "days": start_days - end_days,
                        "turns": turns, "tasks": tasks, "delegation_edges": edges,
                        "identical_delegation_signal": repeated})
    evidence = conn.execute("""
        SELECT 'message:'||m.id::text AS key,m.project_id,m.task_id,m.author,m.kind,
               left(m.text,1000) AS text,m.created_at FROM messages m JOIN projects p ON p.id=m.project_id
        WHERE p.owner_user=ANY(%s) AND (p.channel=ANY(%s) OR p.channel LIKE 'D%%') AND m.created_at>=%s AND m.created_at<%s
          AND m.author NOT LIKE 'maintenance%%'
          AND m.kind IN ('human','answer','delegation','peer','instruction','tool','status')
        ORDER BY m.created_at DESC,m.id DESC LIMIT 25
        """, (owners, company.settings.slack_allowed_channels, at - timedelta(days=30), at)).fetchall()
    # Only bounded, already-recorded requests can be replayed. Never manufacture project context.
    requests = conn.execute("""
        SELECT 'turn:'||t.id::text AS key,k.project_id,k.agent,t.request,t.created_at
        FROM turns t JOIN tasks k ON k.id=t.task_id JOIN projects p ON p.id=k.project_id
        WHERE p.owner_user=ANY(%s) AND (p.channel=ANY(%s) OR p.channel LIKE 'D%%') AND t.created_at>=%s AND t.created_at<%s
          AND t.status='completed' AND t.request IS NOT NULL AND octet_length(t.request::text)<=25000
        ORDER BY t.created_at DESC,t.id DESC LIMIT 7
        """, (owners, company.settings.slack_allowed_channels, at - timedelta(days=30), at)).fetchall()
    replay_inputs = {}
    replay_summaries = []
    for row in safe_rows(requests[:6]):
        if row.get("omitted"):
            continue
        try:
            runtime, context = employee_context(row["request"])
            task = context["task"]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        replay_inputs[row["key"]] = row
        messages = context.get("messages", [])
        exposure = {
            "request_digest": digest(row["request"]),
            "message_count": len(messages),
            "message_excerpts": [{key: (str(m[key])[:600] if key == "text" else m[key])
                                  for key in ("author", "kind", "text") if key in m} for m in messages[-8:]],
            "approved_sources": context["approved_sources"],
            "verified_memories": context.get("verified_memories", []),
            "available_employee": next((r for r in runtime["employees"] if r["id"] == row["agent"]), None),
            "maintenance_snapshot": (context.get("maintenance") or {}).get("service"),
            "excerpted": len(messages) > 8 or any(len(m.get("text", "")) > 600 for m in messages),
            "interpretation": "Only this saved employee context was delivered then. Observer history/evidence "
                              "and subsequent replies were NOT automatically delivered to the employee.",
        }
        if len(json.dumps(exposure, ensure_ascii=False)) > 3500:
            exposure = {"request_digest": digest(row["request"]), "message_count": len(messages),
                        "source_count": len(context["approved_sources"]),
                        "verified_memory_count": len(context.get("verified_memories", [])),
                        "omitted": "employee_context_exceeds_diagnostic_excerpt_limit",
                        "interpretation": "The saved context exists but is too large for this diagnostic excerpt. "
                                          "Do not claim the employee saw specific observer evidence. "
                                          "A prompt replay cannot add missing tools or context."}
        replay_summaries.append({"key": row["key"], "project_id": row["project_id"], "agent": row["agent"],
                                 "kind": "replay_input", "text": str(task.get("instruction", ""))[:1200],
                                 "requester": task.get("requester"), "created_at": row["created_at"],
                                 "employee_context": exposure})
    roster = [{key: getattr(role, key) for key in
               ("id", "mission", "tools", "can_delegate_to", "active", "version")}
              for role in company.roles.values()]
    review = as_json({
        "as_of": at, "periods": periods, "roster": roster,
        "evidence": safe_rows(evidence[:24]) + replay_summaries,
        "sampling": {"recent_message_limit": 24, "messages_truncated": len(evidence) > 24,
                     "replay_limit": 6, "replays_truncated": len(requests) > 6,
                     "replay_request_max_bytes": 25000},
        "interpretation": "Counts by creation cohort and current status, not success or accuracy scores. "
                          "Periods have different lengths; do not compare raw totals as rates. Repeated identical "
                          "delegation is only a signal and may be legitimate. Samples omit older/large/secret inputs. "
                          "A before/after history trend does not establish causality or financial expertise.",
    })
    if SECRET.search(json.dumps(review)):
        raise ValueError("possible_secret_in_review_context")
    return review, replay_inputs, digest(review)
