"""Explicit, bounded live sanity check. No database writes and no expertise claim.

Run inside the authorized worker environment through deploy/entrypoint.py. The
two input-derived request IDs recover cached responses on repeated invocation.
"""

import argparse
import asyncio
import json

from quant_company.company import fingerprint
from quant_company.config import Settings
from quant_company.providers.client import RuntimeClient
from quant_company.providers.codex_runner import strict_json
from quant_company.staff.review_contract import REVIEW_INSTRUCTIONS, REVIEW_MODEL, IndependentReview


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Make up to two official subscription requests")
    args = parser.parse_args()
    if not args.apply:
        print(json.dumps({"mode": "preview", "max_requests": 2, "model": REVIEW_MODEL,
                          "scope": "arithmetic sanity only; not evaluator calibration"}))
        return
    from quant_company.contracts import ProviderRequest

    settings = Settings()
    client = RuntimeClient(settings.staff_review_runtime_url, settings.model_runtime_token.get_secret_value(),
                           expected_provider="claude")
    results = []
    for label, answer, expected in (("correct", "2+2=4", "supported"), ("incorrect", "2+2=5", "concern")):
        material = {"case": "Compute 2+2 in ordinary arithmetic. No further claim is requested.", "answer": answer}
        prompt = REVIEW_INSTRUCTIONS + "\nREVIEW EVIDENCE:\n" + json.dumps(material)
        identity = "review-sanity-" + fingerprint([REVIEW_MODEL, prompt])[:32]
        response = await client.run(ProviderRequest(request_id=identity, model=REVIEW_MODEL, prompt=prompt))
        review = IndependentReview.model_validate(strict_json(response.decision.artifacts[0].content))
        accepted = review.reasoning.assessment == expected
        if label == "correct":
            accepted = accepted and all(getattr(review, key).assessment != "concern" for key in
                                        ("grounding", "assumptions", "limitations"))
        results.append({"case": label, "expected_reasoning": expected, "sanity_matched": accepted,
                        "request_id": identity, "usage": response.usage, "review": review.model_dump(mode="json")})
    print(json.dumps({"scope": "two arithmetic sanity cases only", "calibration_status": "not_yet_calibrated",
                      "all_matched": all(row["sanity_matched"] for row in results), "results": results},
                     ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
