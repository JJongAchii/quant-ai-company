# Quant Scout: structural diagnosis and repair

The owner asked whether the agent, article processing, or another component was
responsible for the repeated failures. The evidence points to the processing and
response contracts, with semantic editing still requiring an independent critic.
Neither a larger host nor a different model was selected as the remedy.

## Confirmed findings

1. A general company response used an outer JSON string containing an action
   proposal whose artifact contained another JSON string. Quant needs no company
   actions. The v10 failure occurred at that company decision boundary; its exact
   invalid field cannot be reconstructed because the raw output was not retained.
   Quant now has fixed native schemas; the service constructs its action wrapper.
   Generic company/news requests retain their legacy protocol and request digest.
2. Equal page allowances unnecessarily clipped an uneven original even when the
   complete text fit the 42,000-character budget. Unused allowance is redistributed.
3. A deterministic quote error spent the sole editorial revision, without saving
   the failed draft. One bounded technical repair is now separate from one content
   revision. All failing evidence indices and the failed draft are retained.
4. Real v11 responses exposed PDF end-of-line word hyphens and model-written
   ellipses. Narrow layout normalization fixed the former. In v12, a content rewrite
   reintroduced ellipses in already-corrected quotations. Telling the model to copy
   exactly was not a reliable evidence interface.
5. Policy v13 therefore supplies lossless, bounded source passages with IDs. The
   model writes a claim and selects `span_id`; the service resolves the exact
   quotation and location from the frozen original, persists the ID and audits the
   resolution. Unknown IDs and changed span contents fail closed. An exact source
   passage is **not** proof of the claim: the separate semantic critic remains.
6. The v13 final critic exposed an inconsistent editorial requirement: the writer
   was required to disclose missing costs, while the critic required a positive
   quotation proving their absence. Policy v14 defines and displays the evidence
   boundary (provided extracted text), and separates optional phrasing suggestions
   from material issues. A falsely reported gap, unsupported number, causal/skill
   claim, tradability claim, false date or inadequate source still blocks. Editorial
   relevance and conditional application are assessed as interpretation, not as
   supposed verbatim findings by the authors. All existing pass booleans remain.
7. A fresh v14 limit-order-book draft exposed a representational defect: claims
   requiring adjacent passages could only reference one passage. Its revision
   replaced one necessary passage with another. Policy v15 allows one to four
   passages per claim, resolves and checks each passage, and asks the critic to
   assess their **joint** support. The v15 revision resolved the factual span
   mismatches; no technical repair was needed.
8. The v15 final critic conflated the author's qualitative realism/validation
   assertion with empirical-market validation. Inspection of the frozen PDF p.4
   and p.6 confirmed qualitative resemblance and an author assertion, not a
   disclosed market-data test. The draft already attributed qualitative similarity.
   Policy v16 distinguishes evidence types by actual data and design; a material
   objection to an evidence gap must identify a contradicting test or data. This
   does not waive actual empirical comparisons or allow simulations to masquerade
   as independent validation. The v15 receipt remains held, not retroactively passed.

An agreeing pair of original arXiv citation dates is also checked deterministically.
The critic names affected fields and checks all occurrences of an overstated claim.
The existing concise Korean card format and source/evidence provenance are retained.

## Real subscription trials (private, no Quant publication)

| Policy | Evidence | Outcome |
| --- | --- | --- |
| v11, `f4179a5` | Four completed calls; old generic AI-governance item held/rejected | Positive draft and repair rejected at exact quotation checks; PDF wraps and ellipses diagnosed |
| v12, `3bce377` | Three old responses revalidated without model replay; two new calls | Layout issue resolved; critic requested a content revision; that revision reintroduced ellipses and was held |
| v13, `4bca573` | Six completed, source-valid calls; no technical repair; negative control held/rejected | Final critic requested another change concerning phrasing and evidence gaps; held after one revision |
| v14, `81bc86c`, crypto | Three settled responses revalidated; one new critic; no budget reset | Held for a 25-block claim linked to a span without 25, and an omitted log-QLIKE qualifier on numerical results |
| v14, `81bc86c`, LOB | Six fresh, source-valid calls; negative control held/rejected | Held for incomplete joint evidence and overbroad conditions; motivated multiple span IDs, not weaker source checks |
| v15, `8281c1d`, LOB | Six fresh, source-valid calls; negative control held/rejected; no repair | Revision fixed source-reference issues; final critic still held on validation wording, as discussed above |
| v16, `94b4281`, LOB | Six fresh, source-valid calls; negative control held/rejected; no repair | Held: one requested span substitution lost support for another retained result, and the final critic requested narrower validation-gap wording |

The final v16 outcome is **not passed**. In particular, its first critic recommended
replacing `p4-s3` with `p2-s10` to stay within the four-span limit; the final critic
then required the omitted `p4-s3` for the retained balanced-impact result. That is
concrete evidence of a non-convergent revision path, not a token-quota or parsing
failure. A future processing change should bind each factual sentence to its own
source references and check dependencies when a revision removes a reference,
instead of repeatedly rewriting a separate prose summary and evidence list. More
model retries, a larger server, or accepting the final draft manually would not
demonstrate that this problem was solved. No such override or same-policy retry
was performed. These are development cases used to diagnose defects, **not** a
held-out estimate of editorial accuracy; their failures remain visible.

The v11 trial overlapped an independently owned production cutover: the operation
receipt honestly records `services_preserved=false` and `current_release_preserved=false`.
This preview script never replaces production containers or the current-release link.
The v12, v13, v14 LOB and v15 LOB trials preserved both. The v14 crypto receipt recorded
service drift with the release link preserved; the exact cause is unproven. Quant publication
remained disabled and its publication count was unchanged throughout these trials. Temporary preview containers were removed only
after known completed outcomes; all original receipts remain in place.
The v16 trial also overlapped a change of the production release from `fac61ef`
to `5fcd435`; its service/release-preservation checks correctly report false. The
preview did not perform that cutover. Do not claim end-to-end operational isolation
was established for that trial merely because the Quant publication count stayed fixed.

Private source archives and full receipts live under
`/var/lib/quant-company/operations/quant-feed-structural-20260929/`.
No credential, full original, or full model response is checked into Git.
`preview_overlay.py` mounts committed source read-only over existing images, with a
separate runtime that shares the existing durable Quant lane lock. It does not build
another multi-gigabyte image or give model processes database/Slack credentials.

## Verification and limitations

- Native-output/source-reference/provider/qualification/audit-session tests: 183
  passed, 1 skipped at v16. Proposals and Slack are simulated in these tests.
- Full v15 suite: 1,290 passed, 45 skipped; after merging the newer audit-session
  main, 1,307 passed, 46 skipped. Full v16: 1,308 passed, 46 skipped. Ruff passed.
  A later deployment-only compatibility guard passed its eight focused tests;
  final-code Linux CI `36511874627` passed (1,348 passed, 44 skipped, one live test
  deselected; 7m49s) on `2782abe`. These are software
  tests, not live Slack delivery.
- The previous fixed-date Housing fixture failed in the first combined suite. The
  independently committed production fix was merged, not rewritten here; its test
  and the subsequent full suite passed.
- Original-to-brief quality on a few papers is not an estimate of long-run curation
  accuracy. No live Slack activation or elapsed 48-hour observation is claimed.
- Production is still on its existing Quant worker while the new contract is
  qualified privately. Rollout must coordinate the matching runtime and consumer;
  a new consumer cannot send the new contract to the old runtime. The scoped
  quality-release operator now compares the installed producer and target consumer
  schema hashes before stopping any service or changing release state. An old or
  mismatched runtime prevents cutover; this operator does not replace that runtime.
- At 02:10 UTC, the host had 2.55 GiB free disk and 1.86 GiB available memory;
  the existing Quant worker was on `12398af`, collection was enabled, publication
  was disabled, and the one old post remained. Another natural Quant review
  briefly prevented the v16 preflight; no model call was issued by that refusal.
  After it completed, the private trial was started. No new Docker images were
  built in this structural qualification series. Unrelated service/image cleanup
  is outside this task; this measurement is not a claim that the host has healthy
  long-term storage headroom.
- Post-trial read-only observation at 02:20:42 UTC: 6.15 GiB disk available, 1.96 GiB
  memory available, no running Quant call, no pending Quant publication, no global
  quota pause and no preview containers remaining. Collection was on; publication
  was off; the one original post remained. The installed runtime advertised only
  `agent_decision`, so the new Quant consumer must **not** be deployed alone.
- `RESULT.json` contains the exact source/receipt hashes and the seven final
  outcomes (31 new subscription calls). No fresh positive passed. Production
  deployment and publication resumption therefore remain incomplete.
- Handoff readback at 03:48:57 UTC confirmed the same old Quant worker, legacy-only
  runtime, enabled collection, disabled publication, one old post and no preview
  containers. One natural Quant review was running; no Quant publication was pending.
  The independently advanced current release was `ac44bf5`; available disk was
  6.40 GiB and memory 1.95 GiB. See `handoff-observation.json`.
