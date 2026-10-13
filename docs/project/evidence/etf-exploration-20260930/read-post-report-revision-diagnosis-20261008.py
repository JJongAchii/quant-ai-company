"""Read immutable review shapes and capability excerpts, not performance numbers."""

import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime

QUERY = """
BEGIN READ ONLY;
SELECT json_build_object(
 'reviews',(SELECT coalesce(json_agg(x),'[]'::json) FROM (
   SELECT s.id,s.stage,s.state,s.attempt,s.error,s.context->'mission'->'stage' AS binding,
     s.result, s.created_at,s.updated_at,
     (SELECT coalesce(json_agg(row_to_json(c)),'[]'::json) FROM (
       SELECT id,proposal_id,payload FROM research_mission_challenges
       WHERE mission_id=s.mission_id
         AND proposal_id::text=s.context->'mission'->'stage'->>'proposal_id'
       ORDER BY created_at,id)c) AS challenges
   FROM research_mission_stages s
   WHERE s.mission_id='2ce40574-6368-5cef-b706-b4e67441b3de'
     AND s.stage='selection' AND s.created_at>='2026-10-07T06:54:42Z'
   ORDER BY s.created_at DESC LIMIT 3)x),
 'failed_attempts',(SELECT coalesce(json_agg(x),'[]'::json) FROM (
   SELECT a.stage_id,a.attempt,a.error,a.response,
     (SELECT coalesce(json_agg(c.id::text),'[]'::json) FROM research_mission_challenges c
       WHERE c.mission_id=s.mission_id
       AND c.proposal_id::text=s.context->'mission'->'stage'->>'proposal_id') AS expected_ids
   FROM research_stage_attempts a JOIN research_mission_stages s ON s.id=a.stage_id
   WHERE s.mission_id='2ce40574-6368-5cef-b706-b4e67441b3de'
     AND s.stage='selection' AND a.error IS NOT NULL
     AND a.created_at>='2026-10-07T06:54:42Z'
   ORDER BY a.created_at DESC LIMIT 4)x));
COMMIT;
"""


def excerpt(value, limit=1500):
    text = value if isinstance(value, str) else ""
    text = re.sub(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", "[number]", text)
    return text[:limit]


def main():
    raw = json.loads(subprocess.check_output([
        "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres",
        "-d", "quant_company", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", QUERY,
    ], text=True))
    reviews = []
    for row in raw["reviews"]:
        result = row.pop("result") or {}
        row["decision"] = result.get("decision")
        rationale = result.get("rationale", "")
        row["rationale_sha256"] = hashlib.sha256(rationale.encode()).hexdigest()
        row["rationale_excerpt_numbers_redacted"] = excerpt(rationale, 2600)
        for challenge in row["challenges"]:
            payload = challenge.pop("payload")
            challenge["concern_excerpt_numbers_redacted"] = excerpt(payload.get("concern"))
            challenge["test_excerpt_numbers_redacted"] = excerpt(payload.get("test"))
        reviews.append(row)
    attempts = []
    for row in raw["failed_attempts"]:
        response = row.pop("response") or {}
        artifacts = response.get("decision", {}).get("artifacts", [])
        contents = [json.loads(a["content"]) for a in artifacts if isinstance(a.get("content"), str)]
        ids = [r.get("challenge_id") for content in contents for r in content.get("responses", [])]
        row["submitted_challenge_ids"] = ids
        row["duplicate_count"] = len(ids) - len(set(ids))
        row["missing_ids"] = sorted(set(row["expected_ids"]) - set(ids))
        row["extra_ids"] = sorted(set(ids) - set(row["expected_ids"]))
        attempts.append(row)
    print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "reviews": reviews,
                      "failed_attempts": attempts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
