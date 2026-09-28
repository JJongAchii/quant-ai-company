"""Offline real-output assessment; optionally run ONE frozen request in the existing worker.

No Company instance, database mutation, Slack send, model tool or API-key fallback.
Receipts from --call contain the real provider identity and token usage. A lost
response is recovered using the unchanged request file, never a replacement ID.
"""

import argparse
import asyncio
import hashlib
import json
import re
from html import unescape
from pathlib import Path

from quant_company.briefing.contracts import BriefProposal, BriefReview, ConditionPatch
from quant_company.briefing.editor import (
    FORMAT_VERSION,
    apply_condition_patch,
    artifact,
    prompt,
    prune,
    render,
    revision_bundle,
    validate,
    validate_review,
)
from quant_company.briefing.quality import reconcile
from quant_company.company import fingerprint
from quant_company.config import Settings
from quant_company.contracts import ProviderFault, ProviderRequest, ProviderResponse
from quant_company.providers.client import RuntimeClient


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    data = json.dumps(value, ensure_ascii=False, indent=2)+"\n"
    if path.exists() and path.read_text() != data:
        raise ValueError(f"Refusing to overwrite different evaluation evidence: {path.name}")
    path.write_text(data)


def provider_response(path):
    value = read(path)
    return ProviderResponse.model_validate(value.get("response", value))


def request(bundle, phase, proposal=None):
    text = prompt(bundle, phase, proposal)
    return ProviderRequest(request_id="news-brief-eval-"+fingerprint([FORMAT_VERSION, phase, text])[:32],
                           model="gpt-6-astra", reasoning_effort="high", prompt=text)


def prepare_revision(bundle, written, reviewed):
    if "revision_feedback" in bundle:
        raise ValueError("Only one offline revision is allowed")
    # Match BriefStore.commit: repair the mechanically accepted draft, before
    # semantic pruning, with both mechanical and editorial failures preserved.
    initial = assess(bundle, written)
    critique = artifact(reviewed, BriefReview)
    validate_review(critique, BriefProposal.model_validate(initial["proposal"]), initial["bundle"])
    rejected = {**initial["rejected"], **dict.fromkeys(critique.rejected_ids, "semantic_review")}
    return revision_bundle(initial["bundle"], initial["proposal"], critique, rejected)


def assess(bundle, written, reviewed=None, *, previous=None, correction_review=None):
    correction = None
    if previous or correction_review:
        if not previous or not correction_review:
            raise ValueError("Condition repair requires both the previous writer and correction review")
        initial = assess(bundle, previous)
        prior, critique = BriefProposal.model_validate(initial["proposal"]), artifact(correction_review, BriefReview)
        validate_review(critique, prior, initial["bundle"])
        rejected = {**initial["rejected"], **dict.fromkeys(critique.rejected_ids, "semantic_review")}
        feedback = revision_bundle(initial["bundle"], initial["proposal"], critique, rejected)["revision_feedback"]
        if feedback["repair_mode"] != "conditions_only":
            raise ValueError("Review does not authorize an isolated condition repair")
        proposed = apply_condition_patch(prior, artifact(written, ConditionPatch), feedback["allowed_ids"])
        correction = {"mode": "conditions_only", "previous_request_id": previous.request_id,
                      "patch_request_id": written.request_id, "allowed_ids": feedback["allowed_ids"]}
    else:
        proposed = artifact(written, BriefProposal)
    rejected = validate(proposed, bundle)
    accepted = prune(proposed, rejected)
    accepted, conflicts = reconcile(accepted, bundle)
    bundle = {**{key: value for key, value in bundle.items() if key != "revision_feedback"},
              "quote_conflicts": conflicts}
    review = artifact(reviewed, BriefReview) if reviewed else None
    if review:
        validate_review(review, accepted, bundle)
        rejected.update(dict.fromkeys(review.rejected_ids, "semantic_review"))
        accepted = prune(accepted, rejected)
        validity = {k: v for k, v in review.checks.items() if k not in {"coverage", "depth", "readability", "materiality"}}
        if review.verdict == "withhold" or (not all(validity.values()) and not review.rejected_ids):
            accepted = None
    parts, quality = render(accepted, bundle, rejected=rejected,
        fallback="editorial_review_withheld" if accepted is None else None,
        review_reduced=not review or review.verdict != "publish")
    return {"proposal": accepted.model_dump(mode="json") if accepted else None, "bundle": bundle,
            "raw_proposal": proposed.model_dump(mode="json"), "rejected": rejected,
            "review": review.model_dump(mode="json") if review else None, "parts": parts, "quality": quality,
            "passed": bool(review and review.verdict == "publish" and all(review.checks.values())
                           and not rejected and not quality["reduced"] and not conflicts),
            "correction": correction,
            "scope": "One saved original-source snapshot and actual provider responses; not human expert certification, five-day qualification or Slack delivery."}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle")
    parser.add_argument("--writer")
    parser.add_argument("--reviewer")
    parser.add_argument("--previous-writer", help="Original writer receipt when --writer contains a condition patch")
    parser.add_argument("--correction-review", help="Review permitting only the named condition replacements")
    parser.add_argument("--prepare-revision", action="store_true",
                        help="Save one offline repair request using the service's accepted draft; does not run it or qualify timing")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--call", type=Path, help="Submit this immutable request once using the existing subscription runtime")
    args = parser.parse_args()
    if args.prepare_revision and (not args.writer or not args.reviewer or args.call):
        parser.error("--prepare-revision requires --writer and --reviewer, without --call")
    if args.call:
        if args.output.exists():
            raise ValueError("Existing receipt must be reconciled before any call; use a different output only for explicit same-ID recovery")
        frozen = ProviderRequest.model_validate(read(args.call))
        if frozen.web_search:
            raise ValueError("Offline evaluation cannot enable model tools")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        settings = Settings()
        client = RuntimeClient(settings.model_runtime_url, settings.model_runtime_token.get_secret_value(),
                               timeout_seconds=settings.company_model_timeout_seconds)
        receipt = {"request_id": frozen.request_id, "request_sha256": hashlib.sha256(args.call.read_bytes()).hexdigest(),
                   "slack": "not_called", "database": "not_connected"}
        try:
            response = await client.run(frozen)
        except ProviderFault as fault:
            save(args.output, {**receipt, "fault": {"code": fault.code}, "automatic_retry": False})
            raise SystemExit(1) from None
        save(args.output, {**receipt, "response": response.model_dump(mode="json")})
        return
    args.output.mkdir(parents=True, exist_ok=True)
    bundle = read(args.bundle)
    if not args.writer:
        save(args.output/"write-request.json", request(bundle, "write").model_dump(mode="json"))
        return
    result = assess(bundle, provider_response(args.writer), provider_response(args.reviewer) if args.reviewer else None,
                    previous=provider_response(args.previous_writer) if args.previous_writer else None,
                    correction_review=provider_response(args.correction_review) if args.correction_review else None)
    if not args.reviewer:
        save(args.output/"review-request.json", request(result["bundle"], "review", result["proposal"]).model_dump(mode="json"))
    save(args.output/"assessment.json", result)
    if args.prepare_revision and not result["passed"]:
        revised = prepare_revision(bundle, provider_response(args.writer), provider_response(args.reviewer))
        save(args.output/"revise-bundle.json", revised)
        save(args.output/"revise-request.json", request(revised, "revise").model_dump(mode="json"))
    text = "\n\n---\n\n".join(result["parts"])
    text = re.sub(r"<([^|>]+)\|([^>]+)>", r"[\2](\1)", text)
    text = re.sub(r"(?m)^\*([^*]+)\*", r"**\1**", text)
    text = unescape(text)
    label = "실제 모델 출력 · 평가 기록 · Slack 미발송"
    (args.output/"brief.md").write_text(label+"\n\n"+text+"\n")
    print(json.dumps({"passed": result["passed"], "rejected": result["rejected"],
                      "quality": result["quality"], "review": result["review"],
                      "parts": [len(x) for x in result["parts"]]}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
