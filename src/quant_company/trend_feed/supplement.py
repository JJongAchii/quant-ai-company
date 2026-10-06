"""Read accepted news events without collecting, publishing or changing the news queue."""

from datetime import timedelta

from ..company import as_json, fingerprint, stable
from ..contracts import ProviderResponse
from ..news.contracts import load_sources
from ..news.editor import validate_review
from ..news.feeds import timestamp
from .contracts import MAJOR_ISSUE_LIMIT


def major_issues(conn, settings, cutoff):
    sources = {s.id: s for s in load_sources(settings.news_sources_file)
               if s.enabled and s.use_for_summary and s.kind == "media"}
    rows = conn.execute("""SELECT p.id,p.event_id,p.article_ids,r.id AS review_id,r.response,r.bundle,
        m.created_at FROM news_publications p JOIN news_reviews r ON r.id=p.review_id
        JOIN messages m ON m.id=p.id JOIN projects project ON project.id=m.project_id
        WHERE r.state='completed' AND r.completed_at<%s AND m.created_at>=%s AND m.created_at<%s
        AND project.channel=%s AND project.owner_user=%s
        ORDER BY m.created_at DESC,p.id LIMIT 100""",
        (cutoff, cutoff-timedelta(days=1), cutoff, settings.news_channel_id, settings.news_owner_user)).fetchall()
    reviewed, events, candidates = {}, set(), []
    for row in rows:
        event = str(row["event_id"])
        if event in events:
            continue
        if row["review_id"] not in reviewed:
            try:
                reviewed[row["review_id"]] = validate_review(ProviderResponse.model_validate(row["response"]), row["bundle"])
            except (ValueError, TypeError):
                reviewed[row["review_id"]] = None
        review = reviewed[row["review_id"]]
        if not review:
            continue
        proposal = next((item for i, item in enumerate(review.items)
                         if stable(f"news-message:{row['review_id']}:{i}") == str(row["id"])
                         and item.disposition == "publish" and item.article_ids == row["article_ids"]), None)
        if not proposal:
            continue
        originals = conn.execute("""SELECT a.*,s.config_digest,s.enabled FROM news_articles a
            JOIN news_sources s ON s.id=a.source_id WHERE a.id=ANY(%s)""", (proposal.article_ids,)).fetchall()
        usable = {}
        for original in originals:
            source = sources.get(original["source_id"])
            receipt = original["retrieval"] or {}
            retrieved = timestamp(receipt.get("retrieved_at"))
            published = original["published_at"]
            if (not source or not original["enabled"] or original["source_digest"] != original["config_digest"]
                    or original["source_digest"] != fingerprint(source.model_dump())
                    or not source.allows_article(original["url"]) or not receipt.get("ok")
                    or not retrieved or retrieved >= cutoff or original["collected_at"] >= cutoff
                    or not published or not cutoff-timedelta(days=1) <= published < cutoff
                    or len(original["content"] or "") < 120):
                continue
            usable[original["id"]] = (original, source)
        evidence_ids = list(dict.fromkeys(e.article_id for e in proposal.evidence))
        if not set(evidence_ids) <= usable.keys():
            continue
        articles = []
        for key in evidence_ids[:2]:
            original, source = usable[key]
            quote = next(e.quote for e in proposal.evidence if e.article_id == key)
            position = original["content"].find(quote)
            if position < 0:
                break
            start = max(0, position-200)
            excerpt = original["content"][start:start+1200]
            articles.append({"id": fingerprint([original["url"], excerpt])[:32], "url": original["url"],
                "publisher": source.publisher, "published_at": original["published_at"],
                "retrieved_at": timestamp(original["retrieval"]["retrieved_at"]),
                "content": excerpt, "excerpt_truncated": len(original["content"]) > len(excerpt),
                "sha256": original["retrieval"].get("original_sha256"),
                "extraction": original["retrieval"].get("article_extraction")})
        else:
            events.add(event)
            candidates.append({"id": fingerprint(["major_issue", event])[:24], "kind": "major_issue",
                "title": proposal.headline, "approved_facts": proposal.facts, "articles": articles,
                "news_review_id": row["review_id"], "news_publication_id": str(row["id"]), "event_id": event,
                "priority": proposal.priority, "published_at": max(a["published_at"] for a in articles)})
        if len(candidates) == MAJOR_ISSUE_LIMIT:
            break
    return as_json(candidates)
