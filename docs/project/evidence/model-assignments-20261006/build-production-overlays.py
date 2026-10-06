"""Merge only the reviewed feature delta into image-derived company source snapshots."""

import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(".local/model-production-sources")
OUT = pathlib.Path(".local/model-candidates")
BASE = "746ece02bf75b119d6e7fc0e7aa7891212a29ae3"
COMMON = {"config.py", "db.py", "model_policy.py", "model_policy_schema.sql"}
RUNTIME = {"providers/codex_runtime.py", "providers/model_catalog.py"}
MODEL_SERVICES = {"worker", "news-worker", "quant-feed-worker", "maintenance",
                  "api", "slack-socket", "account-gateway"}
baseline = json.loads((ROOT / "baseline.json").read_text())
feature = subprocess.check_output(["git", "diff", "--name-only", BASE, "HEAD", "--", "src/"], text=True).splitlines()


def resolve(relative, block):
    installed, incoming = block.split("=======\n", 1)
    installed = installed.split("\n", 1)[1]
    incoming = incoming.rsplit("\n>>>>>>> ", 1)[0] + "\n"
    if relative == "company.py" and "specialist_pack_version" in installed:
        return installed.replace("for role in self.roles.values()", "for role in roles.values()")
    if relative == "company.py" and "reasoning_effort=role.reasoning_effort, prompt=prompt" in installed:
        return installed + ("                from .model_policy import bind\n\n"
                            "                request = bind(self, conn, request, task[\"agent\"], task=task)\n")
    if relative == "quant_feed/store.py" and "prompt=" in installed:
        return installed + ("            from ..model_policy import bind\n\n"
                            "            request = bind(self.company, conn, request, \"quant_scout\")\n")
    raise RuntimeError("unreviewed_production_merge_conflict:" + relative)


def inventory(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


plans = []
for base in sorted(ROOT.iterdir()):
    if not base.is_dir():
        continue
    services = sorted(k for k, v in baseline["services"].items() if v["image"] == "sha256:" + base.name)
    selected = RUNTIME if all("codex-runtime" in s for s in services) else (
        {str(pathlib.Path(p).relative_to("src/quant_company")) for p in feature}
        if set(services) & MODEL_SERVICES else COMMON)
    destination = OUT / base.name / "quant_company"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(base / "quant_company", destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    reviewed_conflicts = []
    for relative in sorted(selected):
        name = "src/quant_company/" + relative
        after = pathlib.Path(name).read_bytes()
        installed = base / "quant_company" / relative
        old = subprocess.run(["git", "show", BASE + ":" + name], capture_output=True)
        if old.returncode:
            if installed.exists() and installed.read_bytes() != after:
                raise RuntimeError("new_production_module_exists:" + relative)
            content = after
        elif not installed.exists():
            raise RuntimeError("production_module_missing:" + relative)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                parent, proposed = pathlib.Path(tmp) / "base", pathlib.Path(tmp) / "feature"
                parent.write_bytes(old.stdout)
                proposed.write_bytes(after)
                merged = subprocess.run(["git", "merge-file", "-p", str(installed), str(parent), str(proposed)],
                                        capture_output=True)
            content = merged.stdout
            if merged.returncode:
                if not 1 <= merged.returncode <= 127:
                    raise RuntimeError("production_merge_failed:" + relative)
                text = content.decode()
                text, count = re.subn(r"^<<<<<<< .*?^>>>>>>> .*?$",
                                      lambda m, relative=relative: resolve(relative, m.group())[:-1], text, flags=re.M | re.S)
                if not count or "<<<<<<< " in text:
                    raise RuntimeError("production_merge_unresolved:" + relative)
                reviewed_conflicts.append(relative)
                content = text.encode()
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    briefing = destination / "briefing/store.py"
    if briefing.exists() and set(services) & MODEL_SERVICES:
        text = briefing.read_text()
        marker = '                                      prompt=model_prompt, web_search=phase == "search")\n'
        if text.count(marker) != 1:
            raise RuntimeError("unreviewed_briefing_request_producer")
        text = text.replace(marker, marker + '            from ..model_policy import bind\n\n'
                            '            request = bind(self.company, conn, request, BRIEFER)\n')
        text, count = re.subn(r"^([ ]*)role = self.company.role\(BRIEFER\)$",
                              lambda m: m[1] + "from ..model_policy import effective_role\n\n" + m[1]
                              + "role = effective_role(self.company, conn, BRIEFER)", text, flags=re.M)
        if count != 2:
            raise RuntimeError("unreviewed_briefing_role_resolution")
        briefing.write_text(text)
        selected.add("briefing/store.py")
    original, candidate = inventory(base / "quant_company"), inventory(destination)
    changed = [{"file": f, "before": original.get(f), "after": candidate[f]}
               for f in candidate if original.get(f) != candidate[f]]
    if any(x["file"] not in selected for x in changed) or set(original) - set(candidate):
        raise RuntimeError("unscoped_source_delta")
    overlay = OUT / base.name / "overlay" / "quant_company"
    if overlay.parent.exists():
        shutil.rmtree(overlay.parent)
    for row in changed:
        target = overlay / row["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(destination / row["file"], target)
    plans.append({"base_image": "sha256:" + base.name, "services": services, "changed": changed,
                  "reviewed_conflicts": reviewed_conflicts, "before": original, "after": candidate})
OUT.mkdir(exist_ok=True)
(OUT / "plan.json").write_text(json.dumps(plans, indent=2) + "\n")
print(json.dumps([{k: p[k] for k in ("base_image", "services", "reviewed_conflicts")}
                  | {"changed_files": len(p["changed"])} for p in plans], indent=2))
