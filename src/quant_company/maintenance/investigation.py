"""Additional exact-commit evidence for maintenance, without executing candidate code."""

import asyncio
import json
import re

from psycopg.types.json import Jsonb

from .policy import SECRET, CodeQuery, digest


def inspect_code(conn, snapshot, queries, *, previous=()):
    # Only complete positive query receipts from this exact commit can bypass a scan.
    # Missing/protected/no-match results are retried when coverage becomes available.
    reusable = {}
    for receipt in previous:
        grouped = {}
        for row in receipt.get("evidence", []):
            signature = row.get("inspection_query")
            if (signature and row.get("content") and row.get("key", "").startswith(f"code:{snapshot['commit']}:")
                    and row.get("inspection_complete")):
                grouped.setdefault(signature, []).append(row)
        reusable.update(grouped)
    files, evidence = None, []
    for value in queries:
        query = CodeQuery.model_validate(value)
        signature = digest([snapshot["commit"], query.model_dump()])
        if signature in reusable:
            evidence.extend({**row, "cache_hit": True} for row in reusable[signature])
            continue
        if files is None:
            cached = conn.execute("SELECT files FROM repository_evidence WHERE commit=%s", (snapshot["commit"],)).fetchone()
            if not cached:
                raise ValueError("exact_repository_evidence_unavailable")
            files = cached["files"]
        result = []
        if query.path:
            paths = [query.path]
            if query.path not in files:
                evidence.append({"key": "inspection:" + signature[:24],
                                 "path": query.path, "status": "not_in_readable_snapshot",
                                 "exists_in_tree": query.path in snapshot["entries"],
                                 "instruction": "Omitted or protected content is unknown, not proof of absence."})
                continue
        else:
            paths = sorted(files)
        matches = 0
        for path in paths:
            lines = files[path].splitlines()
            hits = [i for i, line in enumerate(lines) if query.query and query.query.casefold() in line.casefold()]
            if query.query and not hits:
                continue
            start = max(0, hits[0] - 8) if query.query else query.start_line - 1
            content = "\n".join(f"{i + 1}: {line}" for i, line in
                                enumerate(lines[start:start + query.line_count], start))
            if SECRET.search(content):
                continue
            result.append({"key": f"code:{snapshot['commit']}:{path}", "path": path,
                             "start_line": start + 1, "total_lines": len(lines), "content": content[:14000],
                             "excerpted": start > 0 or start + query.line_count < len(lines) or len(content) > 14000,
                             "inspection_query": signature, "inspection_complete": True})
            matches += 1
            if matches == 4:
                break
        if result:
            reusable[signature] = result
        evidence.extend(result)
    # Keep recent exact evidence in the next prompt; full requests and results remain in the job payload.
    return evidence


def excerpt_identity(row):
    """A citation key identifies a file; different ranges of that file remain distinct."""
    return digest({key: row.get(key) for key in
                   ("key", "path", "start_line", "total_lines", "content", "status", "exists_in_tree")})


def append_inspections(payload, evidence):
    """Preserve existing evidence and original query receipts; append only new exact excerpts."""
    saved = payload.setdefault("investigation_evidence", [])
    known = {excerpt_identity(row) for row in saved}
    for row in evidence:
        identity = excerpt_identity(row)
        if identity not in known:
            saved.append(row)
            known.add(identity)


def inspection_context(payload):
    """Show every saved range in an index; prioritize explicit requests and new ranges in the body."""
    unique = {excerpt_identity(row): row for row in payload.get("investigation_evidence", [])}
    rounds = payload.get("investigation_requests", [])
    recent = rounds[-1] if rounds else {}
    older = {excerpt_identity(row) for round_ in rounds[:-1] for row in round_.get("evidence", [])}
    explicit = {row.get("path") for row in recent.get("requests", []) if row.get("path")}
    candidates = {excerpt_identity(row): row for row in recent.get("evidence", [])}
    if not candidates:
        candidates = unique
    ordered = sorted(candidates.items(), key=lambda pair: (pair[1].get("path") not in explicit,
                     pair[0] in older, pair[1].get("path", ""), pair[1].get("start_line", 0)))
    shown = [row for _, row in ordered[:4]]
    selected = {excerpt_identity(row) for row in shown}
    catalog = [{**{key: row[key] for key in ("key", "path", "start_line", "total_lines", "status") if key in row},
                "line_count": len(row.get("content", "").splitlines()),
                "content_digest": digest(row.get("content", "")), "shown": identity in selected}
               for identity, row in unique.items()]
    return {"investigated_code": shown, "inspection_catalog": catalog,
            "inspection_lookup": "This is an index of stored exact-commit ranges, not model memory. "
                                 "Only shown excerpts include code in this request. Request an indexed path/range "
                                 "to read omitted content; repeated exact queries reuse saved positive receipts.",
            "recent_inspection": {"requests": recent.get("requests", []),
                                  "returned_excerpts": len(recent.get("evidence", [])),
                                  "reused_excerpts": sum(bool(row.get("cache_hit")) for row in recent.get("evidence", []))}}


def feedback_text(text):
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return SECRET.sub("[redacted]", text)[-18000:]


async def external_research(maintainer, job, query, urls, round_number):
    from ..contracts import ToolRequest
    from ..web_fetch import fetch, public_url
    from ..web_tools import arguments_for, search_prompt, search_result

    if not query and not urls:
        return []
    if not maintainer.company.settings.company_web_enabled:
        raise ValueError("maintenance_web_research_disabled")
    results = []
    if query:
        args = arguments_for(ToolRequest(name="web_search", arguments={"query": query, "limit": 5}))
        response = await maintainer.response(job, f"research-i{round_number}", search_prompt(args), web_search=True)
        receipt = search_result(response, args)
        results.append({"key": "external:" + response.request_id, "kind": "search_candidates", "verified": False,
                        "content": json.dumps(receipt, ensure_ascii=False)})
    for supplied in urls:
        url = public_url(supplied)
        if SECRET.search(url):
            raise ValueError("secret_in_research_url")
        identity = "maintweb-" + digest([str(job["id"]), job["payload"].get("diagnostic_revision", 0), url])[:32]
        with maintainer.company.db.transaction() as conn:
            conn.execute("""INSERT INTO web_requests(id,maintenance_job_id,operation,arguments)
                VALUES (%s,%s,'web_read',%s) ON CONFLICT DO NOTHING""", (identity, job["id"], Jsonb({"url": url})))
            saved = conn.execute("SELECT receipt FROM web_requests WHERE id=%s", (identity,)).fetchone()["receipt"]
        if saved is None:
            receipt, raw = await asyncio.to_thread(fetch, url)
            with maintainer.company.db.transaction() as conn:
                conn.execute("""UPDATE web_requests SET receipt=%s,original=%s,completed_at=now()
                    WHERE id=%s AND receipt IS NULL""", (Jsonb(receipt), raw, identity))
        else:
            receipt = saved
        if SECRET.search(json.dumps(receipt, ensure_ascii=False)):
            results.append({"key": "external:" + identity, "omitted": "possible_secret_in_original"})
            continue
        results.append({"key": "external:" + identity, "kind": "fetched_original",
                        **{k: v for k, v in receipt.items() if k not in {"content", "links"}},
                        "content": receipt.get("content", "")[:12000],
                        "excerpted": len(receipt.get("content", "")) > 12000})
    return results
