# Quant Scout quality follow-up evidence

`INTENT.json` records the task boundary, `RESULT.json` is the secret-free outcome, and
`STATUS.json` records the final production snapshot and open gates. `production-baseline.json`
is bounded, public-source metadata captured before qualification. The original model responses,
frozen excerpts, durable request IDs and release journals remain root-private on the production
host under `/var/lib/quant-company/operations/quant-feed-quality-20260928/` and
`/var/lib/quant-company/releases/`; they are not copied into Git.

`read_state.py`, `final_state.py`, `list_positive_candidates.py`, `check_existing_brief.py`,
`diagnose_preview.py` and `revalidate_preview.py` are diagnostic/read-only checks. Scripts whose
names start with `run_` and `qualify_existing_critic.py` are historical one-off preview drivers:
they can invoke the real ChatGPT-subscription runtime and write private receipt files. Do not
rerun them blindly, especially after an uncertain call; reconcile the durable request receipt
and current service state first. No script here authorizes Slack publication or changes the
separately owned Housing, news or research services.

The positive result is a new independent critique of an existing source-valid preview brief.
Fresh positive brief generation did not pass; the staged release was not cut over. This evidence
must not be interpreted as a completed publication-activation gate.
