# First scientific audit reconciliation — 2026-10-07

The actual 3070 scientific evaluation completed on October 6 at19:32KST. Researcher interpretation completed at19:43:38KST. The audit did **not** continue overnight: its first provider response completed at19:48:55KST, then the company rejected it and placed the stage on a hold with no retry timer.

## Cause and preserved evidence

The fixed audit stage `5e5e2013-373c-5868-9fc9-242985e085b3` uses service packet delivery version2. Its inherited `required_lineage_reads` also requires a legacy individual read receipt for `mission/scientific-lineage.json`. That legacy requirement runs before accepting a packet response, while the packet service supplies the immutable audit manifest rather than individual-file tool receipts.

The complete original response remains in the official Codex runtime receipt. It contains the correct first packet digest and bounded notes. Its request ID, input digest and provider thread are independently matched; no new model invocation is required to receive that existing response.

The standalone lineage object is exactly equal to the `scientific_lineage` object inside the frozen `audit/scope/history.json`. That complete history remains a required packet input under its original SHA and character count. The repair retires only the duplicate legacy lineage read route for this fixed stage, preserving all byte delivery, thread binding, final qlab and publication requirements.

## Validation and authority

The repair defaults to a production PostgreSQL transaction probe: it validates and applies the cached response through the existing `commit_stage` service, verifies the next normal turn, and rolls back. The corrected probe passed. An earlier probe failed on an incorrect reservation ordering column before mutation; the transaction rolled back. The reservation check now orders by its real primary key `job_id`.

Root review uses the owner's existing deployment and continuation instructions and PR105. This is a fixed-task metadata reconciliation, with no source rollout, scientific rerun, audit verdict or manual report publication. No new exact-commit human approval is claimed.

Evidence: [blocked stage](evidence/etf-exploration-20260930/first-mission-audit-stall-20261007.json), [rolled-back probe](evidence/etf-exploration-20260930/first-mission-audit-reconciliation-dry-run-20261007.json), [operator](evidence/etf-exploration-20260930/reconcile-first-mission-audit-lineage-20261007.py).

## Current status

The reviewed reconciliation is prepared. Final audit, meaning review and report publication remain pending. No strategy performance or live eligibility is reported here.
