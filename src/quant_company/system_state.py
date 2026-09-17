"""Versioned, read-only implementation evidence shared by director and maintainer."""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

from psycopg.types.json import Jsonb

from .company import PolicyError, as_json
from .maintenance.policy import SECRET, digest


def readable(path):
    parts = PurePosixPath(path).parts
    return (bool(parts) and str(PurePosixPath(path)) == path and not path.startswith("/")
            and not any(p.startswith(".") or p in {"..", "secrets", "credentials", "__pycache__"} for p in parts)
            and "\\" not in path and (
                path in {"README.md", "AGENTS.md", "pyproject.toml"}
                or path.startswith(("src/", "tests/", "docs/", "deploy/"))
                and path.endswith((".py", ".sql", ".md", ".yaml", ".toml", "roles.json"))))


def record_repository(conn, snapshot, files, metadata):
    conn.execute("""INSERT INTO repository_evidence(commit,snapshot,files,metadata) VALUES (%s,%s,%s,%s)
        ON CONFLICT(commit) DO UPDATE SET metadata=excluded.metadata,checked_at=now()""",
                 (snapshot["commit"], Jsonb(snapshot), Jsonb(files), Jsonb(metadata)))


def assessment(conn, case_id):
    row = conn.execute("""SELECT id,disposition,reason,evidence,created_at FROM finding_assessments
        WHERE case_id=%s ORDER BY created_at DESC,id DESC LIMIT 1""", (case_id,)).fetchone()
    return as_json(row) if row else {"disposition": "unverified", "reason": "Historical finding is a hypothesis, not a current defect."}


def assess(conn, *, case_id, disposition, reason, evidence):
    if not evidence or not reason or SECRET.search(json.dumps([reason, evidence], ensure_ascii=False)):
        raise ValueError("assessment_requires_safe_evidence")
    identity = digest([str(case_id), disposition, reason, evidence])
    conn.execute("""INSERT INTO finding_assessments(id,case_id,disposition,reason,evidence)
        VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (identity, case_id, disposition, reason, Jsonb(evidence)))
    return identity


def runtime_facts(conn, company):
    from .owner_controls import effective_limits

    context = company.runtime_context(conn)
    context.pop("snapshot_at")
    if conn.execute("SELECT to_regclass('maintenance_control') AS name").fetchone()["name"]:
        row = conn.execute("SELECT runtime FROM maintenance_control WHERE id=1").fetchone()
        config = (row or {}).get("runtime") or {}
        context["maintenance"] = {"enabled": bool(config.get("enabled")),
                                  "daily_model_call_cap": effective_limits(conn, company, config.get("max_daily_calls"))["maintenance"]}
    return {"code_commit": company.settings.company_code_commit,
            "roles_digest": digest([r.model_dump(mode="json") for r in company.roles.values()]),
            "config_digest": digest(context), "configuration": context}


def record_verification(conn, company, *, identity, feature, scope, evidence):
    """Operator/host entry only; no model tool may certify its own effects."""
    if SECRET.search(json.dumps(evidence, ensure_ascii=False)):
        raise ValueError("verification_contains_possible_secret")
    runtime = runtime_facts(conn, company)
    conn.execute("""INSERT INTO system_verifications(id,feature,code_commit,config_digest,scope,evidence)
        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                 (identity, feature, runtime["code_commit"], runtime["config_digest"], scope, Jsonb(evidence)))


def current_system(conn, company, owners):
    row = conn.execute("SELECT commit,metadata,checked_at FROM repository_evidence ORDER BY checked_at DESC LIMIT 1").fetchone()
    runtime = runtime_facts(conn, company)
    repo = {"state": "unknown", "reason": "repository_not_observed"}
    if row:
        fresh = datetime.now(UTC) - row["checked_at"] < timedelta(minutes=15)
        repo = {"state": "unknown" if row["metadata"].get("refresh_error") else "observed" if fresh else "stale", "commit": row["commit"],
                "checked_at": str(row["checked_at"]), **row["metadata"]}
    applications, corrections, jobs = [], [], []
    if conn.execute("SELECT to_regclass('maintenance_jobs') AS name").fetchone()["name"]:
        applications = conn.execute("""SELECT a.id,a.job_id,a.state,a.head,a.receipt,a.error,a.updated_at,
            j.receipt->'pr' AS pr FROM maintenance_applications a JOIN maintenance_jobs j ON j.id=a.job_id
            JOIN projects p ON p.id=a.project_id WHERE a.owner_user=ANY(%s)
            AND (p.channel=ANY(%s) OR p.channel LIKE 'D%%') ORDER BY a.updated_at DESC LIMIT 8""",
                                    (owners, company.settings.slack_allowed_channels)).fetchall()
        corrections = conn.execute("""SELECT DISTINCT ON (a.case_id) a.* FROM finding_assessments a
            JOIN maintenance_jobs j ON j.id=a.case_id WHERE j.payload->'owners' <@ %s::jsonb
            ORDER BY a.case_id,a.created_at DESC LIMIT 12""", (Jsonb(owners),)).fetchall()
        jobs = conn.execute("""SELECT id,kind,state,error,updated_at,
            payload->'snapshot'->>'commit' AS diagnostic_commit,receipt->>'case_id' AS case_id
            FROM maintenance_jobs WHERE payload->'owners' <@ %s::jsonb
            ORDER BY updated_at DESC LIMIT 10""", (Jsonb(owners),)).fetchall()
    checks = conn.execute("""SELECT * FROM system_verifications ORDER BY created_at DESC LIMIT 12""").fetchall()
    result = as_json({"repository": repo, "runtime": runtime, "applications": applications,
                      "assessments": corrections, "verifications": checks,
                      "maintenance_jobs": {"observed_at": datetime.now(UTC).isoformat(), "records": jobs,
                                           "scope": "Latest 10 live DB states for these owners. Historical tool messages are not current state. "
                                                    "Triage before a scheduled tick is not proof that automatic resumption is missing."}})
    for check in result["verifications"]:
        check["matches_running_version"] = (check["code_commit"] == runtime["code_commit"]
                                            and check["config_digest"] == runtime["config_digest"])
    result["interpretation"] = (
        "Repository code, merged PR, running process, enabled feature and scoped verification are distinct. "
        "CI is not live proof. Old failure messages are historical; assessments correct them without deletion. "
        "Unknown/stale/bounded evidence is not proof of missing functionality. Applications cover only their stated scope. "
        "A verification on an earlier version is historical, not automatically valid on the current version."
    )
    if SECRET.search(json.dumps(result, ensure_ascii=False)):
        raise ValueError("system_evidence_contains_possible_secret")
    return result


def repository_read(conn, arguments):
    if set(arguments) - {"path", "query", "commit", "start_line", "line_count"}:
        raise PolicyError("repository_read accepts path/query, commit and bounded line range")
    path, query, commit = arguments.get("path"), arguments.get("query", ""), arguments.get("commit")
    start, count = arguments.get("start_line", 1), arguments.get("line_count", 100)
    if (path is not None and (not isinstance(path, str) or not readable(path))
            or not isinstance(query, str) or len(query) > 200 or commit is not None
            and (not isinstance(commit, str) or not re.fullmatch("[a-f0-9]{40}", commit))
            or type(start) is not int or start < 1 or type(count) is not int or not 1 <= count <= 200):
        raise PolicyError("Invalid repository read scope")
    row = conn.execute("""SELECT * FROM repository_evidence WHERE (%s::text IS NULL OR commit=%s)
        ORDER BY checked_at DESC LIMIT 1""", (commit, commit)).fetchone()
    if not row:
        return {"state": "unknown", "reason": "requested_repository_snapshot_unavailable"}
    files, entries = row["files"], row["snapshot"]["entries"]
    output = {"commit": row["commit"], "checked_at": str(row["checked_at"]), "records": [],
              "coverage": row["metadata"].get("coverage"), "truncated": False}
    if path is not None:
        if path not in files:
            return {**output, "state": "unknown", "reason": "path_not_in_readable_snapshot"}
        lines = files[path].splitlines()
        excerpt = "\n".join(lines[start - 1:start - 1 + count])
        output["records"] = [{"path": path, "blob": entries[path]["sha"], "start_line": start,
                              "end_line": min(len(lines), start - 1 + count), "content": excerpt[:16000]}]
        output["truncated"] = start > 1 or len(lines) > start - 1 + count or len(excerpt) > 16000
    else:
        terms = [s.lower() for s in query.split()[:8]]
        for name, content in sorted(files.items()):
            for number, line in enumerate(content.splitlines(), 1):
                if (not terms and number == 1) or terms and any(t in (name + " " + line).lower() for t in terms):
                    output["records"].append({"path": name, "blob": entries[name]["sha"],
                                              "line": number, "content": line[:500]})
                    if len(output["records"]) == 25:
                        output["truncated"] = True
                        return output
    return output


def diagnosis_context(conn, company, owners, snapshot, instruction):
    """Bounded source excerpts before diagnosis, from the exact GitHub commit (never local HEAD)."""
    system = current_system(conn, company, owners)
    row = conn.execute("SELECT files FROM repository_evidence WHERE commit=%s", (snapshot["commit"],)).fetchone()
    if not row:
        raise ValueError("current_repository_evidence_required")
    files = row["files"]
    priority = ["src/quant_company/maintenance/store.py", "src/quant_company/company.py", "src/quant_company/maintenance/requests.py",
                "src/quant_company/maintenance/runner.py", "src/quant_company/owner_controls.py",
                "src/quant_company/system_state.py", "tests/test_system_state.py",
                "docs/adr/0018-current-system-evidence-and-owner-controls.md", "docs/project/NEXT-STEPS.md"]
    terms = re.findall(r"[A-Za-z_]{4,}", instruction.lower())[:12]
    ranked = sorted(files, key=lambda p: (-sum(t in (p + files[p]).lower() for t in terms), p))
    records, total = [], 0
    for path in dict.fromkeys(priority + ranked):
        if path not in files or total >= 54000:
            continue
        start = max(0, files[path].find("    def bind_diagnosis(")) if path.endswith("maintenance/store.py") else 0
        content = files[path][start:start + min(6000, 54000-total)]
        records.append({"key": f"code:{snapshot['commit']}:{path}", "path": path,
                        "blob": snapshot["entries"][path]["sha"], "content": content,
                        "start_line": files[path].count("\n", 0, start) + 1,
                        "excerpted": len(content) != len(files[path])})
        total += len(content)
    scope = digest([snapshot["commit"], system["runtime"]["config_digest"], system["runtime"]["roles_digest"],
                    system["runtime"]["code_commit"], system["assessments"]])
    return {"scope_digest": scope, "system": system, "source_files": records,
            "key": "system:" + scope, "coverage": "Bounded excerpts; omitted code is unknown, not absent."}
