"""Withdraw our duplicate notice through the existing durable Slack update outbox.

The actual signed, active program is preserved. No program state or approval is changed.
"""

import json
import subprocess
import tempfile

IMAGE = "quant-company:231d6ba0755f658f167636a8ea2c3ef65b6af562-conditional"
receiver = json.loads(subprocess.check_output(
    ["docker", "inspect", "quant-company-slack-socket-1"], text=True
))[0]
assert receiver["State"]["Running"]
assert receiver["Config"]["Image"] == IMAGE
assert receiver["Image"] == "sha256:7f305b5f8b235d065aa2b4d45106436f31355ec4b66fd78445453148472767ff"
assert "quant-company_core" in receiver["NetworkSettings"]["Networks"]
assert all(not m["RW"] for m in receiver["Mounts"] if m["Type"] == "bind")

read_code = r"""
import json,os,pathlib,httpx
credential=json.loads(pathlib.Path(os.environ['SLACK_CREDENTIALS_FILE']).read_text())['director']
reply=httpx.get('https://slack.com/api/conversations.replies',headers={'Authorization':'Bearer '+credential['bot_token']},params={'channel':'C0C2B9EUEGM','ts':'1789633942.673909','oldest':'1791241541.860959','latest':'1791241541.860959','inclusive':'true','limit':10},timeout=30).json()
matches=[m for m in reply.get('messages',[]) if m.get('ts')=='1791241541.860959']
assert reply.get('ok') and len(matches)==1,'exact_review_read_failed'
message=matches[0]
assert message.get('thread_ts')=='1789633942.673909'
assert message.get('user')==credential['bot_user_id'] and message.get('app_id')=='A0C1ZF715K5'
assert '04cee0f99abab3dfb94b37756a195753960fffd5a3f623ce0dd3563bf776d162' in message.get('text','')
print(json.dumps({'own_message_verified':True}))
"""
# Use the receiver's already-configured Slack egress for the read. The bounded
# administrative container remains on the internal database network only.
readback = json.loads(subprocess.check_output(
    ["docker", "exec", "quant-company-slack-socket-1", "python", "-c", read_code], text=True
))
assert readback == {"own_message_verified": True}

code = r"""
import json
from uuid import NAMESPACE_URL,uuid5
from quant_company.company import Company
from quant_company.config import Settings
company=Company(Settings())
old_id='a9854436-e65c-5b5d-a811-4bbccd74f542'
active_id='f7deaf96-e677-5afe-93d4-18ac387043bb'
old_message='d6e1a9c8-16ee-4a2c-924b-fd0880cc9360'
active_digest='c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
update_id=str(uuid5(NAMESPACE_URL,'owner-review-correction:'+old_message+':'+active_digest))
with company.db.transaction() as conn:
 project=company._project(conn,'9aac0de4-2b97-5195-a720-287d324234f3')
 assert project['revision']==5 and project['status']=='active'
 old=conn.execute('SELECT * FROM research_programs WHERE id=%s FOR UPDATE',(old_id,)).fetchone()
 active=conn.execute('SELECT * FROM research_programs WHERE id=%s FOR UPDATE',(active_id,)).fetchone()
 assert old['state']=='draft' and old['approval_event_id'] is None,'duplicate_has_owner_authority'
 assert old['manifest_digest']=='04cee0f99abab3dfb94b37756a195753960fffd5a3f623ce0dd3563bf776d162'
 assert active['state']=='active' and active['manifest_digest']==active_digest
 assert active['approval_event_id']=='slack:T0C1YRDRPNF:C0C2B9EUEGM:1791242030.830452:director:action:dd69d4b8-78c0-4c78-b7ee-41c7cf7d5972:approve'
 binding=conn.execute('SELECT b.*,o.status,o.sent_ts FROM research_approval_bindings b JOIN outbox o ON o.id=b.message_id WHERE b.message_id=%s',(old_message,)).fetchone()
 assert str(binding['target_id'])==old_id and binding['app_id']=='A0C1ZF715K5'
 assert binding['status']=='delivered' and binding['sent_ts']=='1791241541.860959'
 existing=conn.execute('SELECT id,status,update_ts,sent_ts,error FROM outbox WHERE id=%s',(update_id,)).fetchone()
 if existing:
  result={'created':False,**{k:str(v) if v is not None else None for k,v in existing.items()}}
 else:
  text='추가 승인 요청을 철회합니다.\n'
  text+='2026-10-06 08:13 KST에 이미 승인된 「고정 빈티지 ETF 조건부 개발 연구 — 기존 12개 과제 이력 포함」으로 진행합니다. 추가 승인은 필요 없습니다.\n'
  text+='승인된 명세: https://achiisquantresearch.slack.com/archives/C0C2B9EUEGM/p1791241501260409?thread_ts=1789633942.673909&cid=C0C2B9EUEGM\n'
  text+='승인 예산: 최대 4회 실험 /7,200초 /1개 과제 /동시 1. 자료 검토와 후보 선정 후 이 예산 안에서 실행합니다.\n'
  text+='당시 공개·수정 시점은 미확인인 조건부 개발 연구이며, 확증·실제 체결·운영 승격의 근거로 인정하지 않습니다.\n'
  text+='이 메시지에 있던 24회 초안은 미승인 기록으로만 보관합니다. 승인된 전체 digest: '+active_digest
  company._message(conn,project,None,'director','status',text,message_id=update_id)
  conn.execute('UPDATE outbox SET update_ts=%s WHERE id=%s',(binding['sent_ts'],update_id))
  company._event(conn,'owner_review_notice_corrected',{'old_message_id':old_message,'update_message_id':update_id,'duplicate_program_id':old_id,'signed_active_program_id':active_id,'manifest_digest':active_digest,'program_authority_changed':False},project['id'])
  result={'created':True,'id':update_id,'status':'pending','update_ts':binding['sent_ts']}
print(json.dumps({'effect':result,'active_program_id':active_id,'program_authority_changed':False,'scientific_trials_added':0},ensure_ascii=False))
"""

with tempfile.NamedTemporaryFile(mode="w", prefix="owner-review-correction-", dir="/var/lib/quant-company/config") as env:
    env.write("\n".join(receiver["Config"]["Env"]) + "\n")
    env.flush()
    command = [
        "docker", "run", "--rm", "--init", "--name", "quant-company-owner-review-correction",
        "--network", "quant-company_core", "--env-file", env.name,
        "--read-only", "--memory", "512m", "--cpus", "0.5", "--pids-limit", "128",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "10001:10001", "--tmpfs", "/tmp:size=64m,mode=1777",
    ]
    for mount in receiver["Mounts"]:
        if mount["Type"] == "bind":
            command.extend(["--mount", "type=bind,src=" + mount["Source"] + ",dst=" + mount["Destination"] + ",readonly"])
    command.extend(["--entrypoint", "python", IMAGE, "/app/entrypoint.py", "python", "-c", code])
    result = subprocess.check_output(command, text=True)
print(result.strip())
