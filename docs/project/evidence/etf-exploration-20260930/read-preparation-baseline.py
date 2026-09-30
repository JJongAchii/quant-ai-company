"""Read deployment/program/evidence identities only; never read prices or mutate production."""

import argparse
import hashlib
import json
import shlex
import subprocess
from pathlib import Path

REMOTE = r'''
import hashlib,json,pathlib,subprocess
from datetime import datetime,timezone
def run(args):
    return subprocess.check_output(args,text=True).strip()
sql = """SELECT json_build_object(
 'program',(SELECT row_to_json(p) FROM (SELECT id,project_id,revision,state,spec,manifest_digest,
 approval_event_id,approved_at FROM research_programs WHERE id='e06537d3-fac3-5c8c-bf25-ddabb3c7e282')p),
 'mission_count',(SELECT count(*) FROM research_missions WHERE program_id='e06537d3-fac3-5c8c-bf25-ddabb3c7e282'),
 'usage',(SELECT json_build_object('reservations',count(*),'scientific_trials',count(*) FILTER(WHERE scientific_trial),
 'outstanding',count(*) FILTER(WHERE settled_at IS NULL),'compute_seconds',coalesce(sum(CASE WHEN settled_at IS NULL
 THEN reserved_seconds ELSE actual_seconds END),0)) FROM research_program_reservations
 WHERE program_id='e06537d3-fac3-5c8c-bf25-ddabb3c7e282'),
 'latest_task',(SELECT row_to_json(t) FROM (SELECT id,state,proposal->>'title' AS title,data_assessment,decision,mission_id
 FROM research_program_tasks WHERE program_id='e06537d3-fac3-5c8c-bf25-ddabb3c7e282' ORDER BY created_at DESC LIMIT 1)t));"""
state=json.loads(run(['sudo','docker','exec','quant-company-postgres-1','psql','-U','postgres','-d','quant_company',
                     '-qAt','-v','ON_ERROR_STOP=1','-c',sql]))
if state['program']['manifest_digest'] != '53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b':
    raise ValueError('approved_program_drift')
root=pathlib.Path('/var/lib/quant-company/research/provisioned/data-evidence')
path=root/'registry.json'
before=subprocess.check_output(['sudo','cat',str(path)])
registry=json.loads(before)
packet=next(p for p in registry['packets'] if p['program_digest']==state['program']['manifest_digest'] and p['envelope']=='etf_strategy')
reports={}
for name,item in packet['reports'].items():
    file=pathlib.Path(item['path'])
    if file.parent==pathlib.Path('/state/research/provisioned/data-evidence'):
        file=root/file.name
    if file.parent!=root or '/' in name or file.name=='registry.json':
        raise ValueError('report_path_outside_managed_store')
    raw=subprocess.check_output(['sudo','cat',str(file)])
    if len(raw)>1000000 or hashlib.sha256(raw).hexdigest()!=item['sha256']:
        raise ValueError('report_identity_changed')
    reports[name]={'sha256':item['sha256'],'content':raw.decode()}
after=subprocess.check_output(['sudo','cat',str(path)])
if before!=after:
    raise ValueError('registry_changed_during_read')
state.update(observed_at=datetime.now(timezone.utc).isoformat(),
 app_release=run(['readlink','-f','/opt/quant-company/current']),
 worker_image=run(['sudo','docker','inspect','quant-company-worker-1','--format','{{.Config.Image}}']),
 registry_sha256=hashlib.sha256(before).hexdigest(),packet=packet,reports=reports,
 production_mutated=False,sealed_price_rows_read=False,strategy_performance_computed=False)
print(json.dumps(state,ensure_ascii=False))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output receipt already exists")
    command = ["ssh", "-i", "/Users/achii/.ssh/quant-company-lightsail-rsa", "-o",
               "UserKnownHostsFile=.local/account-deploy/known_hosts", "-o", "StrictHostKeyChecking=yes",
               "-o", "ConnectTimeout=15", "ubuntu@15.165.137.122", "python3 -c " + shlex.quote(REMOTE)]
    state = json.loads(subprocess.check_output(command, text=True))
    reports = state.pop("reports")
    root = args.output.parent / "source-reports"
    root.mkdir(parents=True, exist_ok=True)
    for name, entry in reports.items():
        data = entry["content"].encode()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("Downloaded report identity changed")
        path = root / name
        if path.exists() and path.read_bytes() != data:
            raise ValueError("Local report identity changed")
        path.write_bytes(data)
    state["report_readback"] = {name: {"sha256": item["sha256"], "bytes": len(item["content"].encode())}
                                for name, item in reports.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"receipt": str(args.output), "observed_at": state["observed_at"],
                      "mission_count": state["mission_count"], "usage": state["usage"],
                      "latest_task": state["latest_task"]["state"], "reports": len(reports)}))


if __name__ == "__main__":
    main()
