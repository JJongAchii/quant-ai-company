"""Use the existing program hold hook after three failed data response attempts."""

import json
import subprocess
import tempfile

receiver = json.loads(subprocess.check_output(["docker", "inspect", "quant-company-slack-socket-1"], text=True))[0]
assert receiver["State"]["Running"]
assert receiver["Image"] == "sha256:7f305b5f8b235d065aa2b4d45106436f31355ec4b66fd78445453148472767ff"
assert all(not m["RW"] for m in receiver["Mounts"] if m["Type"] == "bind")
code = r"""
import json
from psycopg.types.json import Jsonb
from quant_company.company import Company,stable
from quant_company.config import Settings
company=Company(Settings())
with company.db.transaction() as conn:
 project=company._project(conn,'9aac0de4-2b97-5195-a720-287d324234f3')
 assert project['revision']==5 and project['status']=='active'
 program=conn.execute("SELECT * FROM research_programs WHERE id='f7deaf96-e677-5afe-93d4-18ac387043bb' FOR UPDATE").fetchone()
 assert program['state']=='active' and program['manifest_digest']=='c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
 stage=conn.execute("SELECT * FROM research_mission_stages WHERE id='ce867fc3-a194-5327-b312-864b93538d34' FOR UPDATE").fetchone()
 existing=stage['context'].get('_program_hold')
 if existing:
  assert existing['reason']=='data_response_contract_requires_remediation'
  result={'already_held':True,'stage_id':str(stage['id']),'hold':existing}
 else:
  assert stage['state'] in {'waiting','running'} and stage['attempt']>=3,'stage_already_has_a_data_result'
  assert stage['stage']=='program_data' and stage['actor']=='data'
  assert conn.execute("SELECT count(*) AS n FROM research_stage_attempts WHERE stage_id=%s AND error='stage_response_rejected'",(stage['id'],)).fetchone()['n']>=3
  assert conn.execute('SELECT count(*) AS n FROM research_program_reservations WHERE program_id=%s',(program['id'],)).fetchone()['n']==0
  failed=conn.execute("SELECT t.id,t.error,t.status FROM turns t JOIN research_stage_attempts a ON a.task_id=t.task_id WHERE a.stage_id=%s AND t.status='blocked' ORDER BY t.created_at DESC LIMIT 1",(stage['id'],)).fetchone()
  assert failed['status']=='blocked' and failed['error']=='stage_response_rejected'
  hold={'reason':'data_response_contract_requires_remediation','observed_attempt':stage['attempt'],'observed_state':stage['state'],'failed_turn_id':str(failed['id']),'operator_authorization':'Owner requested continued normalization and research preparation','scientific_authority_changed':False,'existing_attempt_not_cancelled':True}
  context={**stage['context'],'_program_hold':hold}
  conn.execute('UPDATE research_mission_stages SET context=%s,updated_at=now() WHERE id=%s',(Jsonb(context),stage['id']))
  company._event(conn,'research_data_format_held',{'stage_id':str(stage['id']),**hold},project['id'])
  message_id=stable('data-format-hold:'+str(stage['id']))
  text='독립 데이터 검토의 응답 형식 오류가 세 차례 이어져 이후 재시도를 보류했습니다. 이미 시작한 자료 읽기와 기록은 보존합니다. 수정 코드와 적용 명세를 준비하고 있습니다. 기존 조건부 연구 승인은 유효하며 연구 재승인은 필요 없습니다. 실험·계산시간 예산은 아직 사용하지 않았습니다.'
  company._message(conn,project,stage['task_id'],'director','status',text,message_id=message_id)
  result={'already_held':False,'stage_id':str(stage['id']),'hold':hold,'status_message_id':message_id}
print(json.dumps(result,ensure_ascii=False))
"""
with tempfile.NamedTemporaryFile(mode="w", prefix="data-format-hold-", dir="/var/lib/quant-company/config") as env:
    env.write("\n".join(receiver["Config"]["Env"]) + "\n")
    env.flush()
    command = ["docker", "run", "--rm", "--init", "--name", "quant-company-data-format-hold",
               "--network", "quant-company_core", "--env-file", env.name, "--read-only",
               "--memory", "512m", "--cpus", "0.5", "--pids-limit", "128", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--user", "10001:10001", "--tmpfs", "/tmp:size=64m,mode=1777"]
    for mount in receiver["Mounts"]:
        if mount["Type"] == "bind":
            command += ["--mount", "type=bind,src=" + mount["Source"] + ",dst=" + mount["Destination"] + ",readonly"]
    command += ["--entrypoint", "python", receiver["Config"]["Image"], "/app/entrypoint.py", "python", "-c", code]
    result = subprocess.check_output(command, text=True)
print(result.strip())
