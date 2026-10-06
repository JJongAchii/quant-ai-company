"""Runtime-only compatibility checks; subprocess is real, model RPC is synthetic."""

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from quant_company.providers.codex_runner import CodexRunner, RunnerConfig
from quant_company.providers.codex_runtime import create_app


def test_runtime_catalog_does_not_require_a_company_request_client(tmp_path):
    primary, backup, jobs = (tmp_path / name for name in ("primary", "backup", "jobs"))
    for path in (primary, backup, jobs):
        path.mkdir()
    calls = tmp_path / "calls.jsonl"
    executable = tmp_path / "catalog-cli"
    executable.write_text(f"#!{sys.executable}\n" + f"calls={str(calls)!r}\n" + r'''
import json,os,sys
if '--version' in sys.argv:
    print('codex-cli 0.154.0')
elif 'login' in sys.argv:
    print('Logged in using ChatGPT',file=sys.stderr)
elif 'app-server' in sys.argv:
    for line in sys.stdin:
        message=json.loads(line)
        with open(calls,'a') as output:
            output.write(json.dumps({'method':message['method'],'home':os.environ['CODEX_HOME']})+chr(10))
        if message['method']=='initialized': continue
        if message['method']=='initialize': result={}
        elif message['method']=='model/list':
            result={'data':[{'model':'runtime-candidate','supportedReasoningEfforts':[{'reasoningEffort':'high'}]}],'nextCursor':None}
        else: raise RuntimeError('Inference or unexpected RPC forbidden')
        print(json.dumps({'id':message['id'],'result':result}),flush=True)
else: raise RuntimeError('Unexpected CLI command')
''')
    executable.chmod(0o755)
    runner = CodexRunner(RunnerConfig(codex_home=primary, backup_codex_home=backup, jobs_dir=jobs,
                                     codex_bin=str(executable)))
    with TestClient(create_app(runner=runner, token="runtime-fixture-token")) as client:
        assert client.get("/v1/models/backup").status_code == 401
        response = client.get("/v1/models/backup", headers={"Authorization": "Bearer runtime-fixture-token"})
        assert response.status_code == 200
        assert response.json() == {"profile": "backup", "models": [
            {"model": "runtime-candidate", "reasoning_efforts": ["high"]}]}
    observed = [json.loads(line) for line in calls.read_text().splitlines()]
    assert [r["method"] for r in observed] == ["initialize", "initialized", "model/list"]
    assert all(Path(r["home"]) == backup for r in observed)
    assert not list(jobs.iterdir())
