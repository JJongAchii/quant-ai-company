"""Build/compare the one-file renderer repair against the actual consumer image."""

import datetime
import fcntl
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path("/opt/quant-company/operator-releases/trend-feed-20261006")
PACKAGE = "/opt/company/src/quant_company"


if __name__ == "__main__":
    spec = importlib.util.spec_from_file_location("trend_operator", ROOT / "production-cutover.py")
    operator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operator)
    with (operator.STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = operator.inspect()
        bases = {before[name]["Image"] for name in operator.TARGETS}
        if len(bases) != 1:
            raise RuntimeError("consumer_images_differ")
        base = bases.pop()
        base_tag = "quant-company-trend-base:" + base.split(":", 1)[1][:16]
        operator.run(["docker", "tag", base, base_tag])
        commit = sys.argv[1]
        if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
            raise RuntimeError("commit_not_frozen")
        context = ROOT / "immediate-image"
        context.mkdir(exist_ok=True)
        source = (ROOT / "immediate-editor.py").read_bytes()
        operator.atomic(context / "editor.py", source, mode=0o644)
        dockerfile = (f"FROM {base_tag}\nUSER root\nCOPY editor.py {PACKAGE}/trend_feed/editor.py\n"
                      f"RUN chmod 644 {PACKAGE}/trend_feed/editor.py && "
                      f"python -c 'from pathlib import Path; [p.unlink() for p in Path(\"{PACKAGE}/trend_feed/__pycache__\").glob(\"editor.*.pyc\")]'\n"
                      "USER 10001:10001\n"
                      f"LABEL io.quant-company.trend-feed={commit}\n"
                      f"LABEL io.quant-company.trend-feed.base-id={base}\n")
        operator.atomic(context / "Dockerfile", dockerfile.encode(), mode=0o644)
        tag = "quant-company-trend:" + commit + "-immediate"
        operator.run(["env", "DOCKER_BUILDKIT=0", "docker", "build", "--network", "none", "--pull=false",
                      "--tag", tag, str(context)], timeout=180)
        image = json.loads(operator.run(["docker", "image", "inspect", tag]))[0]
        parent = json.loads(operator.run(["docker", "image", "inspect", base]))[0]
        config_preserved = all(image["Config"].get(k) == parent["Config"].get(k)
                               for k in ("Env", "Cmd", "Entrypoint", "User", "WorkingDir", "Healthcheck"))
        inventory_code = ("import hashlib,json; from pathlib import Path; "
                          f"root=Path('{PACKAGE}'); print(json.dumps({{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() "
                          "for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}))")

        def inventory(identifier):
            return json.loads(operator.run(["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                                            "--security-opt", "no-new-privileges:true", "--entrypoint", "python",
                                            identifier, "-c", inventory_code]))

        old, new = inventory(base), inventory(image["Id"])
        changed = sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
        if not config_preserved or changed != ["trend_feed/editor.py"] or new["trend_feed/editor.py"] != hashlib.sha256(source).hexdigest():
            raise RuntimeError("unqualified_renderer_delta")
        result = {"checked_at": datetime.datetime.now(datetime.UTC).isoformat(), "commit": commit,
                  "base_image": base, "image": image["Id"], "image_tag": tag, "base_config_preserved": config_preserved,
                  "changed_files": changed, "source_file_count": len(new), "renderer_sha256": new["trend_feed/editor.py"],
                  "before_renderer_sha256": old["trend_feed/editor.py"], "running_services_changed": False}
        operator.atomic(operator.STATE / "releases/trend-feed-20261006-immediate-image.json", (json.dumps(result, indent=2) + "\n").encode())
        print(json.dumps(result))
