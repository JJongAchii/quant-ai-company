import hashlib,json,os,pathlib,subprocess,sys
from datetime import UTC,datetime
P=pathlib.Path
BASE=P('/home/achii/quant-company-qualification/exploration-20261001')
COMMIT='3c848af95dc33b7da444a55018fd41d374c10025'
DIRECTORY=BASE/'release-candidates'/COMMIT
ACTIVE=P('/home/achii/.config/quant-company/research-worker.json')
sys.path.insert(0,str(DIRECTORY/'code/src'))
from quant_company.research.releases import PreparedRelease,activate_release,resolve_release_config
from quant_company.research.worker import WorkerConfig,atomic_json,sha_file
assert sha_file(ACTIVE)=='23920eaaaa415ac1e2c2ddbe918f908955de20a54223b9b04a1a76644da5deef','active_config_changed'
old=WorkerConfig.from_file(ACTIVE)
assert resolve_release_config(old,old.company_commit)==old
receipt=json.loads((DIRECTORY/'release.json').read_text())
prepared=PreparedRelease(COMMIT,DIRECTORY,DIRECTORY/'worker-config.json',receipt['config_sha256'],receipt['code_files'])
assert prepared.config_sha256=='67659f99fa6ce5400410c54c597fd9e2c906d54d7ec222ad064264004a9289c9'
assert subprocess.check_output(['systemctl','--user','show','research-worker.service','-p','KillMode','--value'],text=True).strip()=='process'
rollback=BASE/'rollback';rollback.mkdir()
atomic_json(rollback/'worker-config.json',json.loads(ACTIVE.read_text()))
drop=P('/home/achii/.config/systemd/user/research-worker.service.d/99-active-release.conf')
(rollback/'99-active-release.conf').write_bytes(drop.read_bytes())
record={'phase':'activating','previous_commit':old.company_commit,'company_commit':COMMIT,'prior_config_sha256':sha_file(ACTIVE),'prior_dropin_sha256':sha_file(drop),'started_at':datetime.now(UTC).isoformat()}
atomic_json(BASE/'activation.json',record)
current=activate_release(prepared,active_config=ACTIVE)
drop.write_text('[Service]\nWorkingDirectory='+str(current.company_repo)+'\nEnvironment=PYTHONPATH='+str(current.company_repo/'src')+'\n')
subprocess.run(['systemctl','--user','daemon-reload'],check=True)
subprocess.run(['systemctl','--user','restart','research-worker.service'],check=True)
assert subprocess.check_output(['systemctl','--user','is-active','research-worker.service'],text=True).strip()=='active'
pid=subprocess.check_output(['systemctl','--user','show','research-worker.service','-p','MainPID','--value'],text=True).strip()
assert P('/proc/'+pid+'/cwd').resolve()==current.company_repo
assert resolve_release_config(current,old.company_commit)==old
assert all(getattr(current,n)==getattr(old,n) for n in ['api_url','token_file','state_dir','repo_source','input_source','evidence_repo','research_python','adaptive_profiles'])
record.update(phase='active',config_sha256=sha_file(ACTIVE),dropin_sha256=sha_file(drop),pid=int(pid),cwd=str(current.company_repo),previous_release_resolvable=True,transport_runtime_profiles_preserved=True,scientific_trials_added=0,completed_at=datetime.now(UTC).isoformat())
atomic_json(BASE/'activation.json',record)
print(json.dumps(record))
