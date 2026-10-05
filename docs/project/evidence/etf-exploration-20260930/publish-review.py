"""Publish the frozen owner review once through normal company validation and outbox.

Never grant approval, cancel a program or replay an uncertain Slack write.
"""

import hashlib
import json
import pathlib
import subprocess
import tempfile

P = pathlib.Path
STAGED = P("/tmp/exploration-review-staged-attempt2/candidate-program.json")
spec_bytes = STAGED.read_bytes()
assert (
    hashlib.sha256(spec_bytes).hexdigest()
    == "d8da112ab939dbaca20df26bc19bd5e06b144a27da2d96b2406bfb36245d51fd"
)
image = subprocess.check_output(
    ["docker", "inspect", "quant-company-slack-socket-1", "--format", "{{.Config.Image}}"], text=True
).strip()
assert image == "quant-company:231d6ba0755f658f167636a8ea2c3ef65b6af562-conditional"
worker_image = subprocess.check_output(
    ["docker", "inspect", "quant-company-worker-1", "--format", "{{.Config.Image}}"], text=True
).strip()
assert worker_image == "quant-company-autonomous:231d6ba0755f658f167636a8ea2c3ef65b6af562-conditional"
receiver = json.loads(subprocess.check_output(
    ["docker", "inspect", "quant-company-slack-socket-1"], text=True
))[0]
assert receiver["State"]["Running"] and receiver["Image"] == "sha256:7f305b5f8b235d065aa2b4d45106436f31355ec4b66fd78445453148472767ff"

code = r"""
import json,sys
from quant_company.company import Company,fingerprint
from quant_company.config import Settings
from quant_company.research.program_contracts import ResearchProgram
from quant_company.research.program_controller import program_tool
company=Company(Settings())
assert company.settings.company_max_project_tasks==0, 'project_task_limit_changed'
spec=ResearchProgram.model_validate_json(sys.argv[1])
digest=fingerprint(spec.model_dump(mode='json'))
assert digest=='04cee0f99abab3dfb94b37756a195753960fffd5a3f623ce0dd3563bf776d162'
with company.db.transaction() as conn:
 project=company._project(conn,'9aac0de4-2b97-5195-a720-287d324234f3')
 assert project['revision']==5 and project['status']=='active'
 old=conn.execute("SELECT * FROM research_programs WHERE id='e06537d3-fac3-5c8c-bf25-ddabb3c7e282'").fetchone()
 assert old['manifest_digest']=='53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b' and old['state'] in {'active','cancelled'}
 assert conn.execute("SELECT count(*) AS n FROM research_program_reservations WHERE program_id=%s",(old['id'],)).fetchone()['n']==0
 assert conn.execute("SELECT count(*) AS n FROM research_missions WHERE program_id=%s",(old['id'],)).fetchone()['n']==0
 task=conn.execute("SELECT * FROM tasks WHERE project_id=%s AND revision=5 AND agent='director' ORDER BY created_at DESC,id DESC LIMIT 1",(project['id'],)).fetchone()
 assert task is not None
 result=program_tool(company,conn,project,task,{'action':'program_draft','spec':spec.model_dump(mode='json')})
 assert result['id']=='a9854436-e65c-5b5d-a811-4bbccd74f542' and result['state']=='draft'
 bindings=conn.execute("SELECT b.*,o.status,o.sent_ts FROM research_approval_bindings b JOIN outbox o ON o.id=b.message_id WHERE b.target_kind='program' AND b.target_id=%s AND b.manifest_digest=%s ORDER BY b.id",(result['id'],digest)).fetchall()
 assert len(bindings)==1,'existing_notice_ambiguous_no_replay'
 binding=bindings[0]
 notice=conn.execute('SELECT * FROM outbox WHERE id=%s FOR UPDATE',(binding['message_id'],)).fetchone()
 if notice['status']=='pending':
  # Alter the still-unsent persisted review, never a sending/delivered uncertain effect.
  intro='한계를 명시한 ETF 탐색 연구 승인 요청\n탐색 연구 정책과 서명된 취소 처리가 반영된 현재 운영 서비스를 복구했습니다. 이 새 연구의 실험은 아직 0건입니다.\n'
  if old['state']=='active':
   intro+='먼저 이 스레드에 아래 명령을 정확히 보내 기존 프로그램을 중단해 주세요:\n'
   intro+='연구 프로그램 취소 e06537d3-fac3-5c8c-bf25-ddabb3c7e282 53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b\n'
   intro+='중단 확인 후 이 메시지의 승인 버튼을 눌러 새 전체 명세를 승인해 주세요.\n'
  else:
   intro+='기존 프로그램의 중단은 처리된 상태입니다. 이 메시지의 승인 버튼을 눌러 새 전체 명세를 승인해 주세요.\n'
  intro+='당시 공개 시각과 수정 이력은 미확인입니다. 가설 생성만 허용하며 확증·운영 승격의 근거로 인정하지 않습니다.\n'
  intro+='범위: 기존 10개 ETF, 2023~2025 개발 구간. 2026년 추가 가격행 접근 금지.\n'
  intro+='예산: 프로그램 최대 24회 실험 /36,000초 /6 과제 /동시 1; 개별 과제 최대 4회. 일반 업무 40건 제한은 없습니다.\n'
  intro+='새 프로그램 전체 digest: '+digest+'\n정확한 JSON 명세:\n'+json.dumps(spec.model_dump(mode='json'),ensure_ascii=False,indent=2)
  text='<@'+project['owner_user']+'>\n'+intro
  conn.execute('UPDATE outbox SET text=%s WHERE id=%s',(text,notice['id']))
  conn.execute('UPDATE messages SET text=%s WHERE id=%s',(intro,notice['id']))
print(json.dumps({'program':result,'previous_program_state':old['state'],'binding_id':str(binding['id']),'message_id':str(binding['message_id']),'outbox_status':notice['status'],'sent_ts':notice['sent_ts'],'owner_authority_granted':False,'scientific_trials_added':0},ensure_ascii=False))
"""
# A trusted administrative process gets its own bounded memory instead of competing
# with the Temporal worker. Inherit the receiver's public identity and read-only
# credential references; never expose secret values or supply them to a model.
assert "quant-company_core" in receiver["NetworkSettings"]["Networks"]
assert all(not m["RW"] for m in receiver["Mounts"] if m["Type"] == "bind")
with tempfile.NamedTemporaryFile(mode="w", prefix="owner-review-", dir="/var/lib/quant-company/config") as env:
    env.write("\n".join(receiver["Config"]["Env"]) + "\n")
    env.flush()
    command = [
        "docker", "run", "--rm", "--init", "--name", "quant-company-owner-review-04cee0f9",
        "--network", "quant-company_core", "--env-file", env.name,
        "--read-only", "--memory", "512m", "--cpus", "0.5", "--pids-limit", "128",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "10001:10001", "--tmpfs", "/tmp:size=64m,mode=1777",
    ]
    for mount in receiver["Mounts"]:
        if mount["Type"] == "bind":
            command.extend(["--mount", "type=bind,src=" + mount["Source"] + ",dst=" + mount["Destination"] + ",readonly"])
    command.extend(["--entrypoint", "python", image, "/app/entrypoint.py", "python", "-c", code, spec_bytes.decode()])
    result = subprocess.check_output(command, text=True)
print(result.strip())
