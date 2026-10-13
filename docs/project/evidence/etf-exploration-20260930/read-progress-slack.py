"""Read recent program status deliveries and actual Slack bodies without sending."""

import json
import subprocess
from datetime import UTC, datetime

query = """
SELECT coalesce(json_agg(o),'[]'::json) FROM (
 SELECT id,revision,agent,channel,thread_ts,text,status,sent_ts,attempts,error,created_at
 FROM outbox WHERE project_id='9aac0de4-2b97-5195-a720-287d324234f3'
 AND created_at>='2026-10-06T01:37:00Z' ORDER BY created_at DESC,id LIMIT 6)o
"""
rows = json.loads(subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
    "-qAt", "-v", "ON_ERROR_STOP=1", "-c", query
], text=True))
code = r"""
import json,os,pathlib,sys,httpx
rows=json.loads(sys.argv[1])
credentials=json.loads(pathlib.Path(os.environ['SLACK_CREDENTIALS_FILE']).read_text())
for row in rows:
 if row['status']!='delivered' or not row['sent_ts']: continue
 credential=credentials[row['agent']]
 reply=httpx.get('https://slack.com/api/conversations.replies',
  headers={'Authorization':'Bearer '+credential['bot_token']},
  params={'channel':row['channel'],'ts':row['thread_ts'],'oldest':row['sent_ts'],
          'latest':row['sent_ts'],'inclusive':'true','limit':10},timeout=30).json()
 matches=[m for m in reply.get('messages',[]) if m.get('ts')==row['sent_ts']]
 row['slack_readback']={'ok':reply.get('ok',False),'error':reply.get('error'),'match_count':len(matches)}
 if len(matches)==1:
  message=matches[0]
  row['slack_readback'].update({'text':message.get('text'),
    'thread_matches':message.get('thread_ts')==row['thread_ts'],
    'author_matches':message.get('user')==credential['bot_user_id'],
    'raw_text_matches':message.get('text')==row['text'],
    'rendered_text_matches':message.get('text')==f"[지시 v{row['revision']}] {row['text']}"})
print(json.dumps(rows,ensure_ascii=False))
"""
verified = json.loads(subprocess.check_output([
    "docker", "exec", "quant-company-slack-socket-1", "python", "-c", code, json.dumps(rows)
], text=True))
print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "messages": verified}, ensure_ascii=False, indent=2))
