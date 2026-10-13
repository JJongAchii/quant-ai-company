"""Question-driven evidence selection under a fixed, digested policy. No model calls."""

import json
import math
import re
from collections import Counter

from psycopg.types.json import Jsonb

from ..company import PolicyError, as_json, fingerprint, stable
from .library import program_corpus, project_corpus
from .missions import typed
from .planning_contracts import GapRequest, QuestionSpec
from .planning_store import open_gap
from .program_contracts import ResearchProgram

POLICY = {
    "version": 1,
    "bm25": {"k1": 1.2, "b": 0.75},
    "bonus": {"market": 0.5, "topic": 0.5, "counter_kind": 0.5},
    "max_cards": 12, "card_chars": 3000, "chunk_chars": 12000,
    "full_text": {"literature_max": 5, "chars": 300000},
    "brief_fields": ["title", "idea", "market", "data_period", "validation", "author_results",
                     "costs_turnover", "limitations", "application"],
    "market_terms": {"kr_etf": ["한국", "국내", "krx", "kospi", "korea", "korean", "etf"],
                     "kr_stock": ["한국", "국내", "krx", "kospi", "kosdaq", "korea", "korean", "주식", "stock",
                                  "equit"]},
}
POLICY_DIGEST = fingerprint(POLICY)
STOPWORDS = frozenset("""a an and are as at be by for from has have in into is it its of on or that the their
    this to was were which with we our using use based via than then these those""".split())
WORD = re.compile(r"[a-z0-9]+")
HANGUL = re.compile(r"[가-힣]+")
CARD_FIELDS = ("title", "kind", "maturity", "market", "topic", "vintage", "published_on", "idea", "data_period",
               "validation", "author_results", "costs_turnover", "limitations", "application", "change")
NOTE = "Navigation only. Cite the original file, never this card."


def tokens(text):
    """English words without stopwords; Hangul as character bigrams (not morphological analysis)."""
    text = (text or "").lower()
    result = [word for word in WORD.findall(text) if len(word) > 1 and word not in STOPWORDS]
    for run in HANGUL.findall(text):
        result.extend([run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)])
    return result


def bm25(query, documents):
    k1, b = POLICY["bm25"]["k1"], POLICY["bm25"]["b"]
    if not documents:
        return {}
    average = sum(len(value) for value in documents.values()) / len(documents) or 1
    frequency = Counter(term for value in documents.values() for term in set(value))
    scores = {}
    for key, value in documents.items():
        terms, score = Counter(value), 0.0
        for term in set(query):
            if term in terms:
                idf = math.log(1 + (len(documents) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += idf * terms[term] * (k1 + 1) / (terms[term] + k1 * (1 - b + b * len(value) / average))
        scores[key] = score
    return scores


def _brief(row):
    try:
        content = json.loads(row["content"])
    except ValueError:
        return None
    brief = content.get("brief") if isinstance(content, dict) else None
    return brief if isinstance(brief, dict) else None


def _text(value):
    return "; ".join(map(str, value)) if isinstance(value, list) else str(value or "")


def _index(row, brief):
    if brief:
        return " ".join(_text(brief.get(name)) for name in POLICY["brief_fields"])
    return (row["title"] or "") + " " + row["content"][:4000]


def _fit(value, limit):
    while len(json.dumps(value, ensure_ascii=False, sort_keys=True)) > limit:
        key = max((k for k, v in value.items() if isinstance(v, str) and k not in {"source_id", "role", "use"}),
                  key=lambda k: (len(value[k]), k), default=None)
        if key is None or len(value[key]) <= 1:
            raise PolicyError("Evidence card cannot fit its limit")
        value[key] = value[key][: len(value[key]) * 4 // 5]
    return value


def _question(conn, question_id):
    row = conn.execute("""SELECT q.*,r.payload AS spec FROM research_questions q JOIN research_question_revisions r
        ON r.question_id=q.id AND r.revision=q.current_revision WHERE q.id=%s""", (question_id,)).fetchone()
    if not row:
        raise PolicyError("Unknown research question")
    return row


def _corpus(company, conn, question, spec):
    project = company._project(conn, question["project_id"], lock=False)
    if question["program_id"] is None:
        return None, project_corpus(company, conn, project, spec.source_refs), []
    program = conn.execute("SELECT * FROM research_programs WHERE id=%s", (question["program_id"],)).fetchone()
    program_spec = typed(ResearchProgram, program["spec"])
    if fingerprint(program_spec.model_dump(mode="json")) != program["manifest_digest"]:
        raise PolicyError("Program specification changed")
    rows = program_corpus(company, conn, project, program_spec, program["id"])
    # Question references can only point inside the program authority; outside ones are reported, not admitted.
    known = {row["id"]: row for row in rows}
    for ref in spec.source_refs:
        if ref in known and known[ref]["authority"] == "literature":
            known[ref]["authority"] = "explicit"
    return program, rows, sorted(set(spec.source_refs) - known.keys())


def compute(company, conn, question_id):
    question = _question(conn, question_id)
    spec = QuestionSpec.model_validate(question["spec"])
    program, rows, excluded = _corpus(company, conn, question, spec)
    briefs = {row["id"]: _brief(row) for row in rows}
    documents = {row["id"]: tokens(_index(row, briefs[row["id"]])) for row in rows}
    related = bm25(tokens(" ".join([spec.question, *spec.search_terms_en, *spec.search_terms_ko,
                                    *spec.required_data_fields])), documents)
    counter = bm25(tokens(" ".join(spec.counter_terms)), documents) if spec.counter_terms else {}
    market_terms = POLICY["market_terms"].get(spec.market, [])
    scores = {}
    for row in rows:
        brief = briefs[row["id"]] or {}
        base = round(related.get(row["id"], 0.0), 6)
        market = POLICY["bonus"]["market"] if base and any(t in _text(brief.get("market")).lower()
                                                           for t in market_terms) else 0.0
        topic = POLICY["bonus"]["topic"] if base and brief.get("topic") in spec.topics else 0.0
        against = round(counter.get(row["id"], 0.0), 6)
        if against and (brief.get("kind") == "replication" or brief.get("change") in {"correction", "retraction"}):
            against = round(against + POLICY["bonus"]["counter_kind"], 6)
        scores[row["id"]] = {"bm25": base, "market": market, "topic": topic,
                             "total": round(base + market + topic, 6), "counter": against}

    def order(row):
        stamp = row["literature_created_at"] or row["available_at"]
        return (-scores[row["id"]]["total"], -stamp.timestamp(), row["id"])

    required = sorted((row for row in rows if row["authority"] in {"predecessor", "explicit"}),
                      key=lambda row: (row["authority"] != "predecessor", row["id"]))
    latest = {}
    for row in sorted((r for r in rows if r["authority"] == "literature"),
                      key=lambda r: (r["literature_created_at"], r["id"]), reverse=True):
        latest.setdefault(row["work_id"] or row["id"], row)
    ranked = sorted(latest.values(), key=order)
    slots, roles, chosen = max(0, POLICY["max_cards"] - len(required)), {}, []
    counter_pick = next((row for row in sorted(ranked, key=lambda r: (-scores[r["id"]]["counter"], order(r)))
                         if scores[row["id"]]["counter"] > 0), None)
    foundation_pick = next((row for row in ranked if (briefs[row["id"]] or {}).get("vintage") == "classic"
                            and scores[row["id"]]["total"] > 0), None)
    for row, role in ((counter_pick, "counter"), (foundation_pick, "foundation")):
        if row is not None and row["id"] not in roles and len(chosen) < slots:
            roles[row["id"]] = role
            chosen.append(row)
    for row in ranked:
        if len(chosen) >= slots:
            break
        if row["id"] not in roles and scores[row["id"]]["total"] > 0:
            roles[row["id"]] = "related"
            chosen.append(row)
    chosen.sort(key=order)
    budget, full = POLICY["full_text"]["chars"], {row["id"] for row in required}
    used = sum(len(row["content"]) for row in required)
    taken = 0
    for row in chosen:
        if taken < POLICY["full_text"]["literature_max"] and used + len(row["content"]) <= budget:
            full.add(row["id"])
            used += len(row["content"])
            taken += 1
    authors = {row["id"]: {str(a).strip().lower() for a in (briefs[row["id"]] or {}).get("authors", [])[:20]
                           if str(a).strip()} for row in [*required, *chosen]}
    members, cards = [], []
    for rank, row in enumerate([*required, *chosen], start=1):
        chars = len(row["content"])
        member = {"source_id": row["id"], "work_id": row["work_id"], "content_fingerprint": fingerprint(row["content"]),
                  "role": row["authority"] if row["authority"] != "literature" else roles[row["id"]],
                  "rank": rank, "score": scores[row["id"]], "chars": chars,
                  "chunks": math.ceil(chars / POLICY["chunk_chars"]), "full_text": row["id"] in full,
                  "file": ("planning/sources/" + fingerprint(row["id"])[:24] + ".txt") if row["id"] in full else None}
        members.append(member)
        if len(cards) < POLICY["max_cards"]:
            brief = briefs[row["id"]]
            card = {"source_id": row["id"], "role": member["role"], "rank": rank, "full_text": member["full_text"],
                    "chars": chars, "chunks": member["chunks"], "use": NOTE}
            if brief:
                card |= {name: _text(brief.get(name)) for name in CARD_FIELDS if brief.get(name)}
                card["authors"] = _text((brief.get("authors") or [])[:3])
            else:
                card |= {"title": row["title"] or row["id"], "excerpt": row["content"][:1500]}
            shared = sorted(other for other, names in authors.items()
                            if other != row["id"] and names & authors[row["id"]])
            if shared:
                card["shared_authors_with"] = ", ".join(shared[:5])
            cards.append(_fit(card, POLICY["card_chars"]))
    required_chars = sum(len(row["content"]) for row in required)
    value = {"question_id": str(question_id), "question_revision": question["current_revision"],
             "program_id": str(program["id"]) if program else None,
             "program_manifest_digest": program["manifest_digest"] if program else None,
             "corpus_digest": fingerprint(sorted([row["id"], fingerprint(row["content"]), row["authority"]]
                                                 for row in rows)),
             "corpus_watermark": max((row["available_at"] for row in rows), default=None),
             "policy_digest": POLICY_DIGEST, "members": members, "cards": cards,
             "notes": {"excluded_refs": excluded, "counter_reserved": counter_pick is not None and
                       roles.get(counter_pick["id"]) == "counter",
                       "foundation_reserved": foundation_pick is not None and
                       roles.get(foundation_pick["id"]) == "foundation",
                       "literature_full_text": taken, "required_chars": required_chars},
             "char_total": sum(m["chars"] for m in members if m["full_text"]),
             "state": "oversize" if required_chars > budget else "ready"}
    value = as_json(value)
    value["digest"] = fingerprint(value)
    return value


def freeze(company, conn, question_id):
    question = conn.execute("SELECT * FROM research_questions WHERE id=%s FOR UPDATE", (question_id,)).fetchone()
    if not question or question["state"] not in {"open", "waiting"}:
        raise PolicyError("Evidence sets are frozen only for owner-activated questions")
    value = compute(company, conn, question_id)
    identity = stable(f"evidence-set:{question_id}:{value['question_revision']}:{value['corpus_digest']}:"
                      f"{value['policy_digest']}")
    conn.execute("""INSERT INTO research_evidence_sets(id,question_id,question_revision,program_id,
        program_manifest_digest,corpus_digest,corpus_watermark,policy_digest,members,cards,notes,char_total,digest,state)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        (identity, question_id, value["question_revision"], value["program_id"], value["program_manifest_digest"],
         value["corpus_digest"], value["corpus_watermark"], value["policy_digest"], Jsonb(value["members"]),
         Jsonb(value["cards"]), Jsonb(value["notes"]), value["char_total"], value["digest"], value["state"]))
    row = conn.execute("SELECT digest FROM research_evidence_sets WHERE id=%s", (identity,)).fetchone()
    if row["digest"] != value["digest"]:
        raise PolicyError("Evidence set changed under the same corpus and policy")
    if value["state"] == "oversize":
        open_gap(conn, question["project_id"], GapRequest(kind="oversize",
            subject=f"question:{question_id}/revision:{value['question_revision']}", resolver="owner",
            required_evidence="Narrow the question or split required sources to fit 300,000 characters",
            recheck_on="A new question revision or program source list"),
            dependency_fingerprint=value["corpus_digest"], question_id=question_id, evidence_set_id=identity)
    return value | {"id": identity}
