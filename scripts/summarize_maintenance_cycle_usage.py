"""Summarize observed CLI usage without estimating subscription allowance or billing."""

import argparse
import json
from pathlib import Path


def summarize(rows):
    complete = [row for row in rows if isinstance(row.get("usage"), dict)]
    keys = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
    totals = {key: 0 for key in keys}
    missing = {key: 0 for key in keys}
    for row in complete:
        usage = row["usage"]
        for key in keys:
            if key not in usage:
                missing[key] += 1
                continue
            value = usage[key]
            if type(value) is not int or not 0 <= value <= 2**53:
                raise ValueError("Invalid recorded token count")
            totals[key] += value
        if usage.get("cached_input_tokens", 0) > usage.get("input_tokens", 0):
            raise ValueError("Cached input exceeds recorded input")
    for key, count in missing.items():
        if count:
            totals[key] = None
    if totals["input_tokens"] is not None and totals["cached_input_tokens"] is not None:
        totals["uncached_input_tokens"] = totals["input_tokens"] - totals["cached_input_tokens"]
        totals["cached_input_fraction"] = (totals["cached_input_tokens"] / totals["input_tokens"]
                                            if totals["input_tokens"] else None)
    return {"calls_with_usage": len(complete), "rows_without_usage": len(rows) - len(complete),
            "totals": totals, "missing_fields": missing}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text())
    report = {
        "captured_at": snapshot["captured_at"],
        "scope": "Two original recovered director turns and completed new Maintainer calls in this snapshot only",
        "director": summarize(snapshot["original_turns"]),
        "maintainer": summarize(snapshot["maintenance_calls"]),
        "source": "ProviderResponse.usage from official Codex CLI turn.completed events",
        "reference": "https://learn.chatgpt.com/docs/non-interactive-mode",
        "limits": [
            "This is observed successful-call usage, not all server traffic or unresolved prior charges.",
            "Cached input is part of input; reasoning output is not added again to output.",
            "There are no baseline inference counts for the two originally rejected prompts. Character reduction is not measured token savings.",
            "ChatGPT subscription allowance percentages and dollar costs cannot be derived from these counts alone.",
        ],
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
