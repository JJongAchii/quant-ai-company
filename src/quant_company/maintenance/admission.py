"""Share one model slot fairly between owner maintenance and background company work."""


def lock(conn):
    # Both preparation paths hold this only while reserving records, never during inference.
    conn.execute("SELECT pg_advisory_xact_lock(71350224)")


def owner_call_waiting(conn, company):
    if not conn.execute("SELECT to_regclass('maintenance_control') AS name").fetchone()["name"]:
        return False
    row = conn.execute("""
        SELECT p.owner_user FROM maintenance_calls c
        JOIN maintenance_jobs j ON j.id=c.job_id
        JOIN projects p ON p.id::text=j.payload->>'request_project_id'
        CROSS JOIN maintenance_control control
        WHERE c.response IS NULL AND c.due_at<=now()
          AND j.state IN ('triage','patch','design','evaluate')
          AND j.payload->>'request_revision'=p.revision::text
          AND j.payload->'owners' ? p.owner_user
          AND p.owner_user=ANY(%s)
          AND (p.channel=ANY(%s) OR p.channel LIKE 'D%%')
          AND control.id=1 AND control.runtime->>'enabled'='true'
          AND control.runtime->'allowed_owners' ? p.owner_user
        ORDER BY c.created_at,c.id LIMIT 1
        """, (company.settings.slack_allowed_users, company.settings.slack_allowed_channels)).fetchone()
    if not row:
        return False
    from .requests import service

    runtime = service(conn, company, row["owner_user"])
    return runtime["enabled"] and runtime.get("worker_recently_seen", False)

