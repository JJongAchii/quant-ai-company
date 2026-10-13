"""Read revision reasons with all numeric prose redacted; no performance results."""

import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime

query = """
SELECT coalesce(json_agg(r),'[]'::json) FROM (SELECT proposal_id,payload->>'actor' AS actor,
 payload->>'rationale' AS rationale,created_at FROM research_mission_rejections
 WHERE mission_id='2ce40574-6368-5cef-b706-b4e67441b3de' ORDER BY created_at DESC LIMIT 2)r
"""
rows = json.loads(subprocess.check_output([
    'docker', 'exec', 'quant-company-postgres-1', 'psql', '-U', 'postgres', '-d', 'quant_company',
    '-qAt', '-v', 'ON_ERROR_STOP=1', '-c', query,
], text=True))
for row in rows:
    reason = row.pop('rationale')
    row['rationale_sha256'] = hashlib.sha256(reason.encode()).hexdigest()
    row['numeric_prose_redacted'] = re.sub(r'[-+]?\d[\d.,%/:-]*', '[number]', reason)
print(json.dumps({'observed_at': datetime.now(UTC).isoformat(), 'revisions': rows}, ensure_ascii=False, indent=2))
