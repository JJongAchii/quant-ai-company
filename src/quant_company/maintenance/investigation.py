"""Additional exact-commit evidence for maintenance, without executing candidate code."""

import asyncio
import json
import re

from psycopg.types.json import Jsonb

from .policy import SECRET, CodeQuery, digest


def inspect_code(conn, snapshot, queries):
    cached = conn.execute("SELECT files FROM repository_evidence WHERE commit=%s", (snapshot["commit"],)).fetchone()
    if not cached:
        raise ValueError("exact_repository_evidence_unavailable")
    files, evidence = cached["files"], []
    for value in queries:
        query = CodeQuery.model_validate(value)
        if query.path:
            paths = [query.path]
            if query.path not in files:
                evidence.append({"key": "inspection:" + digest([snapshot["commit"], query.model_dump()])[:24],
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
            evidence.append({"key": f"code:{snapshot['commit']}:{path}", "path": path,
                             "start_line": start + 1, "total_lines": len(lines), "content": content[:14000],
                             "excerpted": start > 0 or start + query.line_count < len(lines) or len(content) > 14000})
            matches += 1
            if matches == 4:
                break
    # Keep recent exact evidence in the next prompt; full requests and results remain in the job payload.
    return evidence


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
