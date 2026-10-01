"""Project-scoped immutable originals, independent of Slack feed delivery."""

import json

from psycopg.types.json import Jsonb

from ..company import PolicyError, as_json, fingerprint, stable


def sync_library(company, conn, project):
    settings = company.settings
    # A configured feed owner alone may reuse these originals across their projects.
    if not settings.quant_feed_enabled or settings.quant_feed_owner_user != project["owner_user"]:
        return
    docs = conn.execute("""SELECT * FROM quant_feed_documents WHERE critique->>'disposition'='pass'
        AND brief->>'disposition'='publish' ORDER BY created_at,id""").fetchall()
    for doc in docs:
        source_id = "literature:" + fingerprint([str(project["id"]), doc["id"]])
        content = json.dumps(as_json({"document_id": doc["id"], "metadata": doc["metadata"],
            "receipt": doc["receipt"], "pages": doc["pages"], "brief": doc["brief"],
            "critique": doc["critique"], "content_digest": doc["content_digest"]}), ensure_ascii=False)
        old = conn.execute("SELECT content FROM sources WHERE id=%s", (source_id,)).fetchone()
        if old and old["content"] != content:
            raise PolicyError("Registered literature changed in place")
        retracted = doc["brief"].get("change") == "retraction"
        conn.execute("""INSERT INTO sources(id,title,uri,content,available_at,project_id,approved,synthetic,metadata)
            VALUES(%s,%s,%s,%s,%s,%s,true,%s,%s) ON CONFLICT(id) DO NOTHING""",
            (source_id, doc["brief"]["title"], doc["metadata"]["url"], content, doc["created_at"],
             project["id"], company.settings.fixture_mode,
             Jsonb({"kind": "literature", "document_id": doc["id"], "work_id": doc["work_id"]})))
        conn.execute("""INSERT INTO research_literature(source_id,project_id,document_id,work_id,content_digest,state)
            VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (source_id, project["id"], doc["id"], doc["work_id"], doc["content_digest"],
             "retracted" if retracted else "current"))
        if doc["brief"].get("change") in {"material", "correction", "retraction"}:
            changed = conn.execute("""UPDATE research_literature l SET state=%s
                FROM quant_feed_documents d WHERE l.document_id=d.id AND l.project_id=%s AND l.work_id=%s
                AND d.created_at<%s AND l.state='current' RETURNING l.source_id""",
                ("retracted" if retracted else "superseded", project["id"], doc["work_id"], doc["created_at"])).fetchall()
            if changed:
                company._event(conn, "research_literature_reconsider", {
                    "replacement": source_id, "affected_sources": [row["source_id"] for row in changed]}, project["id"])


def available_sources(company, conn, project, spec, program_id=None):
    company._check_sources(conn, project["id"], spec.source_ids)
    identities = list(spec.source_ids)
    if program_id is not None:
        identities.extend(row["source_id"] for row in conn.execute("""SELECT p.payload->>'source_id' AS source_id
            FROM research_mission_publications p JOIN research_mission_trials t ON t.id=p.trial_id
            JOIN research_missions m ON m.id=t.mission_id WHERE m.program_id=%s ORDER BY p.created_at DESC LIMIT 20""",
            (program_id,)))
        lineage_ids = [envelope.template.scientific_lineage.id for envelope in spec.envelopes
                       if envelope.template.scientific_lineage is not None]
        if lineage_ids:
            identities.extend(row["source_id"] for row in conn.execute("""SELECT p.payload->>'source_id' AS source_id
                FROM research_mission_publications p JOIN research_scientific_lineage_trials l ON l.trial_id=p.trial_id
                JOIN research_scientific_lineages s ON s.id=l.lineage_id
                WHERE s.project_id=%s AND s.id=ANY(%s) ORDER BY p.created_at,p.trial_id""",
                (project["id"], lineage_ids)))
    if spec.include_quant_feed:
        sync_library(company, conn, project)
        identities.extend(row["source_id"] for row in conn.execute("""SELECT source_id FROM research_literature
            WHERE project_id=%s AND state='current' ORDER BY created_at DESC LIMIT 20""", (project["id"],)))
    return conn.execute("""SELECT id,title,uri,content,available_at,synthetic FROM sources
        WHERE id=ANY(%s) ORDER BY id""", (sorted(set(identities)),)).fetchall()


def require_current(conn, source_ids):
    if conn.execute("SELECT 1 FROM research_literature WHERE source_id=ANY(%s) AND state<>'current'",
                    (list(source_ids),)).fetchone():
        raise PolicyError("Literature was corrected or retracted; new task selection requires review")


def verify_citations(conn, proposal):
    if set(proposal.source_ids) != {c.source_id for c in proposal.citations}:
        raise PolicyError("Each task source requires an exact original citation")
    for citation in proposal.citations:
        source = conn.execute("SELECT content FROM sources WHERE id=%s", (citation.source_id,)).fetchone()
        if not source:
            raise PolicyError("Citation source is unavailable")
        try:
            original = json.loads(source["content"])
        except ValueError:
            original = None
        if isinstance(original, dict) and "pages" in original:
            texts = [p["text"] for p in original["pages"] if p["location"] == citation.location]
        else:
            # Plain trusted sources use an explicit character offset; no invented page numbers.
            prefix, _, offset = citation.location.partition(":")
            texts = ([source["content"][int(offset):]]
                     if prefix == "char" and offset.isdecimal() else [])
        if not any(citation.quote in text for text in texts):
            raise PolicyError("Citation does not occur at the specified original location")


def publish_library(company, conn, original_project, source_id, report, conclusion):
    """Called only after checkpoint verification in the publication transaction."""
    channel = company.settings.research_library_channel_id
    if not channel:
        return
    if channel not in company.settings.slack_allowed_channels or not channel.startswith(("C", "G")):
        raise PolicyError("Research library channel is not authorized")
    source = conn.execute("SELECT * FROM sources WHERE id=%s AND approved AND project_id=%s",
                          (source_id, original_project["id"])).fetchone()
    if not source or not conn.execute("SELECT 1 FROM research_mission_publications WHERE payload->>'source_id'=%s",
                                     (source_id,)).fetchone():
        raise PolicyError("Research library requires a verified published checkpoint")
    project_id = stable(f"research-library:{channel}:{original_project['owner_user']}")
    conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel)
        VALUES(%s,'research-library','Verified research findings and reconsideration conditions',%s,%s)
        ON CONFLICT DO NOTHING""", (project_id, original_project["owner_user"], channel))
    project = company._project(conn, project_id)
    library_source = source_id + ":library"
    conn.execute("""INSERT INTO sources(id,title,uri,content,available_at,project_id,approved,synthetic,metadata)
        VALUES(%s,%s,%s,%s,%s,%s,true,%s,%s) ON CONFLICT DO NOTHING""",
        (library_source, source["title"], source["uri"], source["content"], source["available_at"], project_id,
         source["synthetic"], Jsonb({**source["metadata"], "original_source_id": source_id,
                                     "content_digest": fingerprint(source["content"])})))
    identity = stable("research-library-publication:" + source_id)
    if not conn.execute("SELECT 1 FROM messages WHERE id=%s", (identity,)).fetchone():
        label = {"supported": "개발구간 근거 있음", "not_supported": "가설 지지 안 됨", "inconclusive": "결론 보류"}[conclusion]
        if source["metadata"].get("research_scope", {}).get("result_scope") == "conditional_retrospective_development":
            label = "조건부 사후 연구 · " + label
        company._message(conn, project, None, "director", "status",
            f"검증된 연구 기록 · {label}\n{report['view_url']}\n근거: {library_source}\n"
            "원문 주장·실측 결과·미해결 반론·재검토 조건은 보고서에 보존했습니다.", message_id=identity)
