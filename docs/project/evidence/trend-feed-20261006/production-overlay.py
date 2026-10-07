"""Prepare the trend delta against the two consumers' actual image-derived source.

This only writes local candidate files. It does not install credentials or cut over services.
Reviewed conflicts retain the already deployed market briefing integration.
"""

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

BASE = "457a9c20f4e1b95a407dca3f1c0b20070142c0ae"
ROOT = Path(".local/trend-production")


def resolve(name, block):
    installed, incoming = block.split("=======\n", 1)
    installed = installed.split("\n", 1)[1]
    incoming = incoming.rsplit("\n>>>>>>> ", 1)[0] + "\n"
    if name == "cli.py":
        if installed.strip() == "include_market_brief=False):":
            return "              include_market_brief=False, include_trend_scout=False):\n"
        if installed.strip().startswith("or (include_market_brief"):
            return installed.replace("))\n", ")\n") + incoming
        if 'if role.id == "market_brief":' in installed:
            return installed.replace("if role.id == TECH_FEED_AGENT:",
                                     'if role.id in {TECH_FEED_AGENT, "trend_scout"}:')
        if '--include-market-brief' in installed:
            return installed + incoming
        if "args.include_market_brief)" in installed:
            return installed.replace("args.include_market_brief)",
                                     "args.include_market_brief, args.include_trend_scout)").replace(
                '"housing-feed"}', '"housing-feed", "trend-feed"}')
    if name == "company.py" and "specialist_pack_version" in installed:
        return installed.replace('{TECH_FEED_AGENT, "quant_scout"}',
                                 '{TECH_FEED_AGENT, "quant_scout", "trend_scout"}')
    if name == "runtime.py":
        if "brief = BriefEditor" in installed:
            return installed.replace("    return Worker", "    trends = TrendFeedEditor(company, editor.provider)\n    return Worker").replace(
                "BriefEditorialWorkflow]", "BriefEditorialWorkflow, TrendFeedEditorialWorkflow]").replace(
                "brief.activity_tick]", "brief.activity_tick, trends.activity_tick]")
        if "def make_brief_collector" in installed:
            return installed + "                  graceful_shutdown_timeout=timedelta(seconds=10))\n\n\n" + incoming
        if 'getattr(company.settings, "briefing_enabled"' in installed:
            return installed + incoming
        if installed.strip() == "make_brief_collector(client, company)):":
            return installed.replace(")):", "),") + incoming
    if name == "slack.py":
        if 'row["message_kind"] == "briefing"' in installed:
            return installed + incoming
        if "requires_receipt =" in installed:
            return installed.replace('"briefing", "housing_feed"}', '"briefing", "housing_feed", "trend_feed"}')
    raise RuntimeError("unreviewed_overlay_conflict:" + name)


def inventory(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


exports = json.loads((ROOT / "source-exports.json").read_text())
feature = subprocess.check_output(["git", "diff", "--name-only", BASE, "HEAD", "--", "src/quant_company"],
                                  text=True).splitlines()
selected = {str(Path(p).relative_to("src/quant_company")) for p in feature}
plans = []
for image, exported in exports.items():
    source = ROOT / image.removeprefix("sha256:") / "quant_company"
    target = ROOT / "candidates" / image.removeprefix("sha256:") / "quant_company"
    if target.parent.exists():
        shutil.rmtree(target.parent)
    shutil.copytree(source, target)
    reviewed = []
    for name in feature:
        relative = str(Path(name).relative_to("src/quant_company"))
        installed = source / relative
        incoming = Path(name).read_bytes()
        old = subprocess.run(["git", "show", BASE + ":" + name], capture_output=True)
        if relative == "roles.json":
            roles = json.loads(installed.read_text())
            trend = next(r for r in json.loads(incoming) if r["id"] == "trend_scout")
            existing = [r for r in roles if r["id"] == "trend_scout"]
            if existing and existing != [trend]:
                raise RuntimeError("conflicting_existing_trend_role")
            content = (json.dumps(roles if existing else [*roles, trend], ensure_ascii=False, indent=2) + "\n").encode()
            reviewed.append(relative)
        elif old.returncode:
            if installed.exists() and installed.read_bytes() != incoming:
                raise RuntimeError("new_module_already_differs:" + relative)
            content = incoming
        else:
            if not installed.exists():
                raise RuntimeError("missing_base_module:" + relative)
            with tempfile.TemporaryDirectory() as tmp:
                parent, proposed = Path(tmp) / "base", Path(tmp) / "proposed"
                parent.write_bytes(old.stdout)
                proposed.write_bytes(incoming)
                merged = subprocess.run(["git", "merge-file", "-p", str(installed), str(parent), str(proposed)],
                                        capture_output=True)
            content = merged.stdout
            if merged.returncode:
                if not 1 <= merged.returncode <= 127:
                    raise RuntimeError("source_merge_failed:" + relative)
                content = re.sub(r"^<<<<<<< .*?^>>>>>>> .*?$", lambda m, name=relative: resolve(name, m.group())[:-1],
                                 content.decode(), flags=re.M | re.S).encode()
                if b"<<<<<<< " in content:
                    raise RuntimeError("unresolved_source_merge:" + relative)
                reviewed.append(relative)
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
    before, after = inventory(source), inventory(target)
    changed = [{"file": name, "before": before.get(name), "after": after[name]}
               for name in after if before.get(name) != after[name]]
    if any(r["file"] not in selected for r in changed) or set(before) - set(after):
        raise RuntimeError("unexpected_source_delta")
    overlay = target.parent / "overlay/quant_company"
    for row in changed:
        output = overlay / row["file"]
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target / row["file"], output)
    plans.append({"base_image": image, "package_root": exported["package_root"], "services": exported["services"],
                  "before": before, "after": after, "changed": changed, "reviewed_conflicts": reviewed})
(ROOT / "candidates/plan.json").write_text(json.dumps(plans, indent=2) + "\n")
print(json.dumps({"production_modified": False, "changed_files": [r["file"] for p in plans for r in p["changed"]],
                  "reviewed_conflicts": [r for p in plans for r in p["reviewed_conflicts"]]}))
