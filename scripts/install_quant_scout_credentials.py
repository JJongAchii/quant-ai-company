"""Operator-only Orca-to-host credential handoff; never exposes token payloads.

Run from the authenticated new app's Install App page. No credentials are saved locally.
Only the outbound quant_scout entry may be appended; existing identities are immutable.
"""

import argparse
import json
import subprocess
import sys
import urllib.request

APP = "A0C3HFP831Q"
TEAM = "T0C1YRDRPNF"

REMOTE = r'''
import fcntl,json,os,pathlib,tempfile
p=pathlib.Path('/var/lib/quant-company/secrets/slack-credentials.json')
item=json.load(__import__('sys').stdin)
assert set(item)=={'app_id','bot_user_id','bot_token'}
assert item['app_id']=='A0C3HFP831Q' and item['bot_token'].startswith('xoxb-')
os.umask(0o077)
with pathlib.Path('/var/lib/quant-company/.backup.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    before=json.loads(p.read_text())
    if 'quant_scout' in before:
        assert before['quant_scout']==item, 'existing_quant_credential_conflict'
    else:
        result={**before,'quant_scout':item}
        stat=p.stat()
        fd,name=tempfile.mkstemp(prefix='.quant-credential-',dir=p.parent)
        try:
            with os.fdopen(fd,'w') as out:
                json.dump(result,out)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(name,stat.st_mode & 0o777)
            os.chown(name,stat.st_uid,stat.st_gid)
            os.replace(name,p)
        finally:
            pathlib.Path(name).unlink(missing_ok=True)
    after=json.loads(p.read_text())
    assert all(after[key]==value for key,value in before.items())
    print(json.dumps({'installed':True,'preserved_existing':True,'roles':len(after)}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", required=True)
    parser.add_argument("--ssh-key", required=True)
    parser.add_argument("--known-hosts", required=True)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    expression = (
        "(()=>{if(location.origin!=='https://api.slack.com'||location.pathname!=="
        f"'/apps/{APP}/install-on-team')throw Error('wrong_app_page');"
        "const t=[...document.querySelectorAll('input')].filter(x=>/^xoxb-/.test(x.value));"
        "if(t.length!==1)throw Error('expected_one_bot_token');return {token:t[0].value}})()"
    )
    raw = subprocess.run(["orca", "eval", "--page", args.page, "--expression", expression, "--json"],
                         check=True, capture_output=True, timeout=30).stdout
    token = json.loads(json.loads(raw)["result"]["result"])["token"]
    request = urllib.request.Request("https://slack.com/api/auth.test", data=b"",
                                     headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=20) as response:
        identity = json.load(response)
        scopes = {scope.strip() for scope in response.headers.get("x-oauth-scopes", "").split(",")}
    if not identity.get("ok") or identity.get("team_id") != TEAM or scopes != {"chat:write"}:
        raise ValueError("unexpected_bot_identity_or_scope")
    credential = {"app_id": APP, "bot_user_id": identity["user_id"], "bot_token": token}
    receipt = {"app_id": APP, "team_id": TEAM, "bot_user_id": identity["user_id"],
               "bot_id": identity["bot_id"], "scopes": sorted(scopes), "installed": False}
    if args.install:
        command = ["ssh", "-i", args.ssh_key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                   "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + args.known_hosts,
                   "ubuntu@15.165.137.122", "sudo", "python3", "-c", __import__("shlex").quote(REMOTE)]
        raw = subprocess.run(command, input=json.dumps(credential).encode(), capture_output=True,
                             check=True, timeout=40).stdout
        receipt.update(json.loads(raw))
    print(json.dumps(receipt))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # HTTP/process exception bodies may contain credential-adjacent diagnostics.
        print(json.dumps({"ok": False, "error": type(exc).__name__}), file=sys.stderr)
        sys.exit(1)
