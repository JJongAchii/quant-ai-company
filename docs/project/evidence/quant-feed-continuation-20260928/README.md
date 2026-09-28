# Quant Scout continuation, 28 September

`INTENT.json` defines this continuation. `RESULT.json` records the read-only production
preflight, the failed real-subscription qualification and regression checks; `STATUS.json`
names the open gates. The qualification made no Slack post or service cutover.

`list_full_originals.py`, `list_complete_originals.py`, `inspect_candidate_bundles.py` and
`show_saved_card.py` read metadata, bounded originals or stored preview cards.
`diagnose_saved_positive.py` revalidates one saved response offline; it makes no model call.
`run_combined_positive_preview.py
--check-only` verifies the exact existing app image, source inventory, Compose preview
configuration, publication pause and activity without invoking a model. Its `--live`
mode can invoke the real ChatGPT-subscription runtime and write a root-private receipt;
never run it while a Quant call is active, the shared quota pause persists, or a previous
receipt is uncertain. It deliberately refuses the existing failed receipt instead of replaying it.

The failed real preview used the existing version-8 image. A local version-9 prompt/date
correction followed; it has not been deployed or qualified by this receipt. A different
full-context source and a new durable request series are required for any next trial.

The public repo holds no full model response, credentials or original PDF. The exact
qualifier source was installed at
`/var/lib/quant-company/operations/quant-feed-continuation-20260928/qualify_quant_editorial.py`
with SHA-256 `5e12d4ee0083b8fff24d3e64e612c5d4657e1550917e30c2a90fcc4e47f132ca`.
The private output path is below that operation's `preview/` directory. A later operator
must recheck the production baseline before any live preview or cutover.
