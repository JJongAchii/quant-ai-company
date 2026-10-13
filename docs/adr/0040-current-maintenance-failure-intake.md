# Current maintenance failures and bounded diagnosis

The maintainer must distinguish an operational response failure from a normal research decision,
data limitation, owner approval wait or subscription backoff. The production maintainer's old
observer excluded all research waiting events. Independent explanation-review failures had no
observer. Its path catalogs could also exhaust the shared provider input budget before inference.

## Decision

Observe repeated response-contract errors only on current tasks belonging to active approved
programs/missions, with the current owner, project revision and allowed channel. Completed,
paused, cancelled and first-attempt stages are excluded. Raw Pydantic input values are not
passed to the maintainer. A stage and attempt has one observation identity.

Observe blocked `uncertain` and `invalid_review` independent reviews for an allowed owner.
These are operational failures; objective staff grades remain intact. An uncertain request is
never replayed by observation. The case states that operator reconciliation is required.
Existing request IDs, responses and receipts are preserved. Subscription/auth backoff is excluded.

The observer uses the existing PostgreSQL records and existing Temporal workflow. It adds no
queue, scheduler, model, credentials, spending tier or automatic application permission.

Keep full diagnostic evidence in PostgreSQL. When source excerpts and system summaries still
leave a prompt oversized, bound repository path catalogs while retaining currently inspected
paths and exact evidence keys. Record omitted counts and permit the existing bounded code
inspection tool to find omitted paths. Bound oversized observation detail explicitly without
dropping observation identities. A saved/reserved provider request is never rewritten.

## Verification

Use real PostgreSQL for ownership, current-state, deduplication and receipt-preservation checks.
Reproduce the actual blocked diagnostic input locally against the old function and candidate,
without inference or external writes. Distinguish synthetic model/GitHub/Slack pipeline checks
from production intake, real subscription calls and actual PR receipts.

The existing exact-candidate approval and uncertain-effect rules remain in force. Observation
cannot approve a research result, expand a research budget, merge a future bot PR or certify
that a protected/uncertain failure was repaired.
