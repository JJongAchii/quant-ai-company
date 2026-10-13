"""Read only proposal identity fields and immutable receipt metadata, never metrics."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime

QUERY = """
BEGIN READ ONLY;
SET LOCAL statement_timeout='10s';
SELECT json_build_object(
 'stage',(SELECT json_build_object('id',id,'state',state,'attempt',attempt,'task_id',task_id,
   'error',error,'retry_at',retry_at,'updated_at',updated_at) FROM research_mission_stages
   WHERE id='378b5f19-1a59-5021-85a9-eb8c131d2242'),
 'attempts',(SELECT json_agg(x) FROM (
   SELECT attempt,error,created_at,completed_at,response FROM research_stage_attempts
   WHERE stage_id='378b5f19-1a59-5021-85a9-eb8c131d2242' ORDER BY attempt)x),
 'interpreted_predecessors',(SELECT coalesce(json_agg(x),'[]'::json) FROM (
   SELECT t.id,t.ordinal,t.result_id FROM research_mission_trials t
   JOIN research_mission_interpretations i ON i.trial_id=t.id
   WHERE t.mission_id='2ce40574-6368-5cef-b706-b4e67441b3de' AND t.result_id IS NOT NULL
   ORDER BY t.ordinal DESC)x),
 'eligible_superseded_proposals',(SELECT coalesce(json_agg(p.id),'[]'::json)
   FROM research_mission_proposals p JOIN research_mission_rejections r ON r.proposal_id=p.id
   JOIN research_missions m ON m.id=p.mission_id
   WHERE m.id='2ce40574-6368-5cef-b706-b4e67441b3de' AND p.cycle=m.cycle),
 'technical_closures',(SELECT coalesce(json_agg(x),'[]'::json) FROM (
   SELECT t.id,t.proposal_id,t.result_id,t.technical_closed_at,d.digest AS disposition_digest
   FROM research_mission_trials t JOIN research_technical_dispositions d ON d.trial_id=t.id
   WHERE t.mission_id='2ce40574-6368-5cef-b706-b4e67441b3de')x),
 'latest_request',(SELECT json_build_object('id',id,'status',status,'created_at',created_at,
   'updated_at',updated_at,'request',request) FROM turns WHERE task_id=(
   SELECT task_id FROM research_mission_stages WHERE id='378b5f19-1a59-5021-85a9-eb8c131d2242')
   ORDER BY created_at DESC LIMIT 1));
COMMIT;
"""


def main():
    raw = json.loads(subprocess.check_output([
        "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
        "-qAt", "-v", "ON_ERROR_STOP=1", "-c", QUERY,
    ], text=True, timeout=30))
    for row in raw["attempts"] or []:
        response = row.pop("response") or {}
        row["submitted_artifacts"] = []
        for artifact in response.get("decision", {}).get("artifacts", []):
            content = artifact.get("content")
            if not isinstance(content, str):
                continue
            try:
                proposal = json.loads(content, strict=False)
            except json.JSONDecodeError:
                continue
            if isinstance(proposal, dict):
                row["submitted_artifacts"].append({
                    "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                    **{key: proposal.get(key) for key in (
                        "id", "author", "predecessor_trial_ids", "supersedes_proposal_id", "source_ids")},
                })
    latest = raw["latest_request"]
    if latest:
        request = latest.pop("request") or {}
        prompt = request.get("prompt", "")
        context = json.loads(prompt.split("\nMISSION DATA JSON:\n", 1)[1])
        latest["prompt_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
        latest["last_error"] = context.get("last_error")
        latest["proposal_contract"] = context.get("proposal_contract")
        latest["previous_contract_error"] = context.get("previous_contract_error")
    print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "database_mutated": False, **raw}, indent=2))


if __name__ == "__main__":
    main()
