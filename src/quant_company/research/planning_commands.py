"""Read-only planning commands. Never calls models; database work is rolled back."""

import json
from pathlib import Path

from ..company import Company, as_json
from .planning_retrieval import compute
from .planning_store import QuestionStore
from .planning_triage import replay


def command(settings, action, *, question=None, bundle=None, company=None):
    if action == "replay-triage":
        return replay(json.loads(Path(bundle).read_text()))
    company = company or Company(settings)
    with company.db.transaction() as conn:
        try:
            if action == "status":
                projects = conn.execute("SELECT DISTINCT project_id FROM research_questions ORDER BY project_id").fetchall()
                return as_json({"projects": [QuestionStore(company).status(conn, company._project(
                    conn, row["project_id"], lock=False)) for row in projects]})
            if action == "preview":
                return compute(company, conn, question)
            raise ValueError("Unknown research planning action")
        finally:
            conn.rollback()
