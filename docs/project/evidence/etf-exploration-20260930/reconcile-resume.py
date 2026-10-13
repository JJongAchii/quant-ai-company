"""Reconcile the completed start against real transport and Slack receipts.

The first readiness predicate required an INFO log hidden by WARNING logging.
This only updates our restoration receipt; it never restarts or replays a service.
"""

import fcntl
import hashlib
import json
import pathlib
import subprocess
from datetime import UTC, datetime

STATE = pathlib.Path("/var/lib/quant-company")
RECORD = STATE / "releases/owner-research-resume-20261006.json"


def run(args):
    return subprocess.check_output(args, text=True, timeout=120, stderr=subprocess.STDOUT)


probe = r"""
import json,logging,os,pathlib,httpx
from quant_company.company import load_roles
from quant_company.config import Settings
active=[r.id for r in load_roles(Settings()).values() if r.active]
connections=0
for table in ['tcp','tcp6']:
 for line in pathlib.Path('/proc/net/'+table).read_text().splitlines()[1:]:
  fields=line.split()
  if fields[3]=='01' and fields[2].split(':')[-1]=='01BB': connections+=1
credential=json.loads(pathlib.Path(os.environ['SLACK_CREDENTIALS_FILE']).read_text())['director']
reply=httpx.get('https://slack.com/api/conversations.replies',headers={'Authorization':'Bearer '+credential['bot_token']},params={'channel':'C0C2B9EUEGM','ts':'1789633942.673909','oldest':'1791241541.860959','latest':'1791241541.860959','inclusive':'true','limit':10},timeout=30).json()
matches=[m for m in reply.get('messages',[]) if m.get('ts')=='1791241541.860959']
assert reply.get('ok') and len(matches)==1,'exact_slack_read_failed'
message=matches[0]
assert message.get('user')==credential['bot_user_id'] and message.get('app_id')=='A0C1ZF715K5'
assert message.get('thread_ts')=='1789633942.673909'
assert 'c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db' in message.get('text','')
assert '추가 승인은 필요 없습니다.' in message.get('text','')
assert not any(b.get('type')=='actions' for b in message.get('blocks',[]))
print(json.dumps({'active_roles':active,'established_https_connections':connections,'logger_default_level':logging.getLogger('quant_company.socket').getEffectiveLevel(),'actual_own_slack_update_verified':True,'sent_ts':message['ts'],'approval_controls_removed':True}))
"""

with (STATE / ".backup.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    record = json.loads(RECORD.read_text())
    assert record["phase"] in {"resume_incomplete", "current_services_resumed_verified"}
    assert pathlib.Path("/opt/quant-company/current").resolve().name == record["source_commit"]
    before = record["before"]
    rows = json.loads(run(["docker", "inspect", *before]))
    after = {r["Name"].lstrip("/"): {
        "id": r["Id"], "image": r["Config"]["Image"], "image_id": r["Image"],
        "state": r["State"]["Status"], "health": r["State"].get("Health", {}).get("Status"),
        "restarts": r["RestartCount"], "oom": r["State"]["OOMKilled"],
    } for r in rows}
    assert all(after[n]["id"] == r["id"] and after[n]["image_id"] == r["image_id"] for n,r in before.items())
    assert all(after[n]["state"] == "running" and not after[n]["oom"] for n in record["started"])
    assert after["quant-company-api-1"]["health"] == "healthy"
    assert all(hashlib.sha256((STATE / "config" / n).read_bytes()).hexdigest() == h
               for n,h in record["configuration_sha256"].items())
    proof = json.loads(run(["docker", "exec", "quant-company-slack-socket-1", "python", "-c", probe]))
    assert proof["established_https_connections"] >= len(proof["active_roles"]) == 7
    if record["phase"] == "resume_incomplete":
        record["initial_readiness_check"] = {"phase": record["phase"], "error_type": record["error_type"],
            "predicate": "INFO connection message required although effective logger level is WARNING"}
    record.update(phase="current_services_resumed_verified", after=after, verification=proof,
                  completed_at=datetime.now(UTC).isoformat(), reconciliation_only=True)
    RECORD.write_text(json.dumps(record, indent=2) + "\n")
    RECORD.chmod(0o600)
    print(json.dumps(record, ensure_ascii=False, indent=2))
