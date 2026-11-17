"""Round 2 of the limited trial: the configurations round 1 showed were needed, once each.

Round 1 (trial_claude_editorial.py) found Sonnet 5.5 medium news reviews and a trend set failing their domain
contracts (attributed reporting not allowed, publication without facts or evidence, trend background without
evidence), so a review would block its articles. Round 2 runs, once each and concurrently:

- news reviews 1-2 of the comparison inputs on Opus 5.5 medium with this branch's editor guidance (the comparison's
  Opus medium outputs were valid but published 0/4 and 1/7);
- the two latest completed production screening requests on Sonnet 5.5 low;
- the two latest completed production Quant Scout review requests on Sonnet 5.5 high.

Production requests were read read-only from PostgreSQL into .local (git-ignored). Each output is checked by the
production parse_result and the same domain checks the stores apply; nothing is written to any database.
"""

import argparse
import asyncio
import json

from trial_claude_editorial import (
    DEFAULT_INPUTS,
    NEWS_OLD,
    PRIVATE,
    ROOT,
    assess,
    current_texts,
    replaced,
    run_case,
    write,
)

from quant_company.contracts import ProviderRequest, ProviderResponse
from quant_company.providers.routing import CLAUDE_MODEL, CLAUDE_SONNET

PUBLIC = ROOT / "docs/project/evidence/claude-editorial-20261117/trial-round2.json"


def screening_check(response, bundle):
    """news/screening.NewsScreeningStore.commit's checks, without the database."""
    from quant_company.news.contracts import NewsScreening

    d = response.decision
    if d.status != "complete" or len(d.artifacts) != 1 or d.tools or d.messages or d.delegations or d.memories \
            or d.follow_up or d.artifacts[0].source_ids:
        raise ValueError("news_screen_artifact_only")
    screen = NewsScreening.model_validate_json(d.artifacts[0].content)
    ids = [a["id"] for a in bundle["articles"]]
    covered = [a.article_id for a in screen.items]
    offered = set(ids) | {a["id"] for a in bundle["held"]}
    if (set(covered) != set(ids) or len(covered) != len(ids)
            or any(not set(a.related_ids) <= offered or a.article_id in a.related_ids for a in screen.items)):
        raise ValueError("news_screen_input_coverage")
    return {"domain_valid": True, "articles": len(ids), "kept": sum(i.disposition == "keep" for i in screen.items)}


def quant_check(response, bundle, stage):
    from quant_company.quant_feed.editor import validate

    value = validate(response, bundle, stage, audit=[])
    return {"domain_valid": True, "disposition": getattr(value, "disposition", None)}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude", required=True, help="the Claude CLI to run (production pins 2.1.280)")
    binary = parser.parse_args().claude
    rows = json.loads(DEFAULT_INPUTS.read_text())
    production = json.loads((PRIVATE / "round2-inputs.json").read_text())
    news_new, _ = current_texts()
    cases = {}
    for index in (1, 2):
        row = rows[index - 1]
        value = dict(row["request"], model=CLAUDE_MODEL, reasoning_effort="medium", session=None,
                     prompt=replaced(row["request"]["prompt"], [(NEWS_OLD, news_new)]))
        cases[f"r2-news-{index}"] = ("news", row, ProviderRequest.model_validate(value))
    for index, row in enumerate(production["screens"], 1):
        value = dict(row["request"], model=CLAUDE_SONNET, reasoning_effort="low")
        cases[f"r2-screen-{index}"] = ("screen", row, ProviderRequest.model_validate(value))
    for index, row in enumerate(production["quant"], 1):
        value = dict(row["request"], model=CLAUDE_SONNET, reasoning_effort="high")
        cases[f"r2-quant-{index}"] = ("quant", row, ProviderRequest.model_validate(value))
    results = await asyncio.gather(*(run_case(name, request, binary) for name, (_, _, request) in cases.items()))
    runs = {r["case"]: r for r in results}
    write(PRIVATE / "runs-round2.json", runs)
    summary = {"purpose": "round 2 of the limited trial (see module docstring); not a comparison or production run",
               "cli_version": json.loads((PRIVATE / "cli.json").read_text())["version"], "cases": []}
    for name, (kind, row, request) in cases.items():
        case = {"case": name, "kind": kind, "model": request.model, "effort": request.reasoning_effort,
                "prompt_chars": len(request.prompt), **runs[name]}
        folder = PRIVATE / f"case-{name}"
        if case.get("state") == "completed":
            try:
                response = ProviderResponse.model_validate_json((folder / "response.json").read_text())
                case.update(assess(row, folder) if kind == "news" else screening_check(response, row["bundle"])
                            if kind == "screen" else quant_check(response, row["bundle"], row["stage"]))
            except ValueError as error:
                case.update(domain_valid=False, domain_error=str(error)[:400])
        summary["cases"].append(case)
    write(PUBLIC, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
