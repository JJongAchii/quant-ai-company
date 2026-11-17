"""Limited real-subscription trial of the 2026-11-17 Claude editorial configuration.

Not a model comparison and not a production run. Five bounded calls on the frozen inputs of the 2026-11-17 editorial
comparison (company-editorial-model-eval): the morning and evening briefing compositions on Opus 5.5 xhigh, two news
reviews and one trend set on Sonnet 5.5 medium. Each frozen prompt receives only this branch's instruction changes,
checked to occur exactly once; each command is built by the production claude_runner.command and its output checked
by the production parse_result and domain validators. The writer limit is the production runway (about 67 minutes in
both frozen editions), not the comparison harness's 960 seconds.

Raw prompts and outputs stay in .local (git-ignored); only counts, timings and validator outcomes are written to
docs/project/evidence/claude-editorial-20261117/trial.json. A case that has started.json is never run again: a lost
or uncertain result is reported, not repeated.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

from quant_company.contracts import ProviderFault, ProviderRequest
from quant_company.providers.claude_runner import (
    FORBIDDEN_AUTH,
    ClaudeConfig,
    command,
    parse_result,
    stdin_payload,
)
from quant_company.providers.codex_runner import ProcessRunner
from quant_company.providers.routing import CLAUDE_MODEL, CLAUDE_SONNET

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / ".local/claude-editorial-trial-20261117"
PUBLIC = ROOT / "docs/project/evidence/claude-editorial-20261117/trial.json"
DEFAULT_INPUTS = Path("/Users/achii/quant-ws/company-workspaces/company-editorial-model-eval/"
                      ".local/editorial-eval-20261117/production-inputs.json")
WRITER_SECONDS = 3600  # below the ~67-72 minute write runway both frozen editions had (phase_deadline)

# The instruction text this branch changed, exactly as the frozen prompts carry it (asserted to occur once).
NEWS_OLD = "Select actual developments, not routine local notices, lifestyle advice, entertainment or a quota of posts.\n"
BRIEF_OLD = [
    ("Use supplied calendar originals or exact source passages. Preserve original timezones; convert only when\n"
     "a confirmed time AND timezone exist. A date-only or weekday report is time_unconfirmed, not an inferred\n"
     "release time. Distinguish cancellations/changes and absent verification from no event.\n"),
    ("Return one fact_placements entry for every supplied fact ID,\n"
     "mapping it to main_item_ids; several related facts may share a coherent paragraph."),
]
# (input index, model, effort) — the comparison's cases 1, 2, 3, 5 and 6.
CASES = {1: (CLAUDE_SONNET, "medium"), 2: (CLAUDE_SONNET, "medium"), 3: (CLAUDE_SONNET, "medium"),
         5: (CLAUDE_MODEL, "xhigh"), 6: (CLAUDE_MODEL, "xhigh")}


def current_texts():
    from quant_company.briefing import editor
    from quant_company.news.editor import EDITOR_INSTRUCTIONS

    news_new = EDITOR_INSTRUCTIONS[EDITOR_INSTRUCTIONS.index(NEWS_OLD):].split("Group reports", 1)[0]
    start = editor.WRITE.index("Use supplied calendar originals")
    calendar_new = editor.WRITE[start:editor.WRITE.index("Assess every morning watchpoint", start)]
    placement_new = ("Return one fact_placements entry for every supplied fact ID\n"
                     "(never an empty list), mapping it to main_item_ids; several related facts may share a "
                     "coherent paragraph.")
    assert placement_new.replace("\n", " ") in " ".join(open(editor.__file__).read().split("\n"))
    return news_new, [calendar_new, placement_new]


def replaced(prompt, pairs):
    for old, new in pairs:
        assert prompt.count(old) == 1, "frozen prompt does not carry the changed instruction exactly once"
        prompt = prompt.replace(old, new)
    return prompt


def requests(rows):
    news_new, brief_new = current_texts()
    built = {}
    for index, (model, effort) in CASES.items():
        row = rows[index - 1]
        frozen = row["request"]["prompt"]
        prompt = (replaced(frozen, [(NEWS_OLD, news_new)]) if row["kind"] == "news" else
                  replaced(frozen, list(zip(BRIEF_OLD, brief_new, strict=True))) if row["kind"] == "brief" else frozen)
        value = dict(row["request"], model=model, reasoning_effort=effort, prompt=prompt, session=None,
                     brief_deadline_unix=None)
        assert not value.get("web_search")
        built[index] = (row, ProviderRequest.model_validate(value),
                        {"frozen_prompt_chars": len(frozen), "trial_prompt_chars": len(prompt),
                         "instruction_changes": prompt != frozen})
    return built


def environment():
    # The local desktop subscription context (as in the comparison); no API key, token or auth variable passes.
    keys = {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "USER", "LOGNAME", "SHELL",
            "__CF_USER_TEXT_ENCODING"}
    keys.update(k for k in os.environ if k.startswith("ORCA_") and "TOKEN" not in k and "HOOK" not in k)
    env = {k: os.environ[k] for k in keys if k in os.environ}
    assert not any(k in env for k in FORBIDDEN_AUTH)
    env.update(DISABLE_AUTOUPDATER="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", NO_COLOR="1")
    return env


def public_error(error):
    """A validator's reason without the model text pydantic quotes in it (outputs stay in .local)."""
    text = re.sub(r"input_value=.*?(?=, input_type=)", "input_value=<omitted>", str(error), flags=re.S)
    return re.sub(r"\s*For further information visit \S+", "", text)[:400]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


async def run_case(index, request, binary):
    folder = PRIVATE / f"case-{index}"
    if (folder / "started.json").exists():
        return {"case": index, "state": "not_repeated", "note": "started earlier; never run twice"}
    write(folder / "started.json", {"at": time.time(), "request_id": request.request_id, "model": request.model,
                                    "effort": request.reasoning_effort})
    config = ClaudeConfig(folder / "unused-auth", folder / "unused-jobs", binary,
                          usage_credits_disabled_confirmed=True)
    args = command(config, request)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="trial-") as cwd:
        try:
            output = await ProcessRunner().run(args, cwd=Path(cwd), env=environment(), stdin=stdin_payload(request),
                                               timeout_seconds=WRITER_SECONDS if request.output_contract.startswith(
                                                   "brief_") else 600, max_stdout_bytes=32 * 1024 * 1024,
                                               max_stderr_bytes=65536)
        except ProviderFault as fault:
            return {"case": index, "state": "failed", "fault": fault.code, "seconds": round(time.monotonic()-started)}
    seconds = round(time.monotonic() - started, 1)
    (folder / "stdout.jsonl").write_bytes(output.stdout)
    report = {"case": index, "seconds": seconds, "returncode": output.returncode,
              "stdout_sha256": hashlib.sha256(output.stdout).hexdigest()}
    try:
        response = parse_result(request, output)
    except ProviderFault as fault:
        return {**report, "state": "failed", "fault": fault.code, "detail": str(fault)[:300]}
    write(folder / "response.json", response.model_dump(mode="json"))
    usage = response.usage
    return {**report, "state": "completed", "provider": response.provider, "actual_model": usage["actual_model"],
            "billing_mode": usage["billing_mode"], "effort": usage["effort"],
            "tokens": {k: usage[k] for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                             "cache_creation_input_tokens")}}


def assess(row, folder):
    from quant_company.briefing.contracts import BriefComposition, CalendarEvent
    from quant_company.briefing.editor import artifact, item_map, render, validate, validate_source_notes
    from quant_company.briefing.inventory import compose
    from quant_company.contracts import ProviderResponse
    from quant_company.news.editor import validate_review
    from quant_company.trend_feed.editor import publication_items, validate_draft

    response = ProviderResponse.model_validate_json((folder / "response.json").read_text())
    bundle = row["bundle"]
    try:
        if row["kind"] == "news":
            output = validate_review(response, bundle)
            return {"domain_valid": True, "primary_count": len(bundle["primary_ids"]),
                    "selection_counts": {kind: sum(i.disposition == kind for i in output.items)
                                         for kind in ("publish", "hold", "ignore")}}
        if row["kind"] == "trend":
            output = validate_draft(response, bundle)
            return {"domain_valid": True, "candidate_count": len(bundle["candidates"]),
                    "visible_items": len(publication_items(output.model_dump(mode="json"), bundle))}
        composition = artifact(response, BriefComposition, bundle)
        placements = len(composition.fact_placements)
        output = compose(composition, bundle)
        rejected = validate(output, bundle)
        try:
            validate_source_notes(output, bundle)
            notes = []
        except ValueError as error:
            notes = getattr(error, "violations", [str(error)])
        parts, quality = render(output, bundle)
        (folder / "rendered.txt").write_text("\n\n".join(parts))
        events = [item for item in item_map(output).values() if isinstance(item, CalendarEvent)]
        timed = [e for e in events if e.at]
        stated = [e for e in timed if any(re.search(r"\d{1,2}:\d{2}|\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?)|\b(?:ET|EST|EDT|"
                                                    r"KST|GMT|UTC)\b|오전|오후|\d{1,2}시", ev.quote, re.I)
                                          for ev in e.evidence)]
        return {"domain_valid": not rejected and not notes,
                "rejected": {reason: len(ids) if isinstance(ids, list) else 1 for reason, ids in (
                    rejected.items() if isinstance(rejected, dict) else [])},
                "rejected_items": len(rejected), "source_notes_violations": len(notes),
                "fact_placements": placements, "calendar_events": len(events),
                "timed_events": len(timed), "timed_events_with_time_in_quote": len(stated),
                "rendered_chars": sum(len(p) for p in parts),
                "render_quality": {k: quality[k] for k in ("fallback", "rejected") if k in quality}}
    except ValueError as error:
        return {"domain_valid": False, "domain_error": public_error(error)}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--claude", required=True, help="the Claude CLI to run (production pins 2.1.280)")
    parser.add_argument("--assess-only", action="store_true")
    args = parser.parse_args()
    rows = json.loads(args.inputs.read_text())
    assert [r["kind"] for r in rows] == ["news", "news", "trend", "trend", "brief", "brief"]
    built = requests(rows)
    runs = {}
    if not args.assess_only:
        version = (await ProcessRunner().run([args.claude, "--version"], cwd=ROOT, env=environment(), stdin=b"",
                   timeout_seconds=20, max_stdout_bytes=4096, max_stderr_bytes=4096)).stdout.decode().strip()
        write(PRIVATE / "cli.json", {"version": version})
        # All five run at once, as the four Claude slots allow in production.
        results = await asyncio.gather(*(run_case(i, request, args.claude) for i, (_, request, _) in built.items()))
        runs = {r["case"]: r for r in results}
        write(PRIVATE / "runs.json", runs)
    else:
        runs = {int(k): v for k, v in json.loads((PRIVATE / "runs.json").read_text()).items()}
    summary = {"purpose": "limited trial of the changed Claude configuration; not a comparison or production run",
               "cli_version": json.loads((PRIVATE / "cli.json").read_text())["version"],
               "inputs": "frozen 2026-11-17 comparison inputs (company-editorial-model-eval/.local)",
               "concurrent_calls": len(runs), "cases": []}
    for index, (row, request, prompt_info) in built.items():
        case = {"case": index, "kind": row["kind"], "model": request.model, "effort": request.reasoning_effort,
                **prompt_info, **runs.get(index, {"state": "not_run"})}
        folder = PRIVATE / f"case-{index}"
        if case.get("state") == "completed":
            case.update(assess(row, folder))
        summary["cases"].append(case)
    write(PUBLIC, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
