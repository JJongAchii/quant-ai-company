# Director queue recovery and real Maintainer diagnosis — 2026-10-06

User approval: “응 지행해줘”, after the proposal to recover the old director queue, observe the real Maintainer cycle and measure actual tokens.

## Root cause and repair

The two original director turns were queued without a request or model attempt. Actual Temporal histories repeatedly failed with `ProviderRequest.prompt` exceeding 90,000 characters. The request builder bounded task JSON at 68,000 characters but also added 23,798–24,213 characters of role instructions and runtime facts. Infrastructure retries could not change this deterministic preparation failure.

The builder now reserves the complete header length before bounding task evidence. Existing source authorization, protected requested/read sources, truncation disclosures and full stored records remain intact. An existing reserved request is reused unchanged. Deterministic policy/validation failures before dispatch now produce an explicit blocked turn instead of cycling through infrastructure retries; validation input is excluded from its error receipt.

Real production configuration and PostgreSQL qualification, with every SQL write rolled back and **zero model calls**:

| Original turn | Baseline characters | Candidate characters | Preparation |
|---|---:|---:|---|
| `ffc34b0d-5504-5463-b6dc-2e954cbb408b` | 91,441 | 79,028 | failure → ready |
| `2e4de4d4-00b8-50d6-8dc4-1765a28e1807` | 90,350 | 77,937 | failure → ready |

Models, max reasoning effort, instructions and original output contracts were retained. These are **character measurements**, not measured token savings.

## Qualification and production rollout

- Focused real-PostgreSQL regression tests: **50 passed**.
- Complete service suite: **1,444 passed, 46 skipped**, two existing deprecation warnings, 385.67 seconds. PostgreSQL and Temporal are real local test services; model/Slack/GitHub pipeline responses are test fixtures.
- Lint and diff checks pass. New cases cover complete prompt budgeting for work/answer turns, preserved stored evidence and request snapshots, redacted terminal validation faults, and irreducible instruction budgets.
- Production worker module hashes matched code base `ec1fbeb29a49420a367f06af596283e6ead4ab72`. Only the company/execution module diff from reviewed patch commit `87e774fbb5caafb6cf56bc14af678699ccacea10` was applied over its existing immutable held-audit image. Its briefing, research/native-output features and dependencies were retained.
- The worker was activated at **17:27:34 KST**. Image ID: `sha256:edb93937acda68bfe7ef17307eef3832b7f775665fddd69e98658048741848fb`. Patch identity is the `quant-company.turn-context-patch` image label; the base release declaration is preserved, not represented as an entire main rebuild.
- All installed environment values, UID, entrypoint/command, mounts, networks, security settings, 384MiB memory and CPU limits were preserved. Other service container IDs/images and the global release symlink were unchanged. All ten blocked uncertain independent-review hashes and all 56 historical maintenance-call hashes remained unchanged.
- A persistent worker-only Compose overlay and durable cutover/rollback receipt are recorded. Future operator releases must preserve this overlay or qualify its replacement. No model/runtime restart, credential transfer, manual model replay or Slack approval fabrication was performed.

## Real execution observed

Both original turns completed with their original IDs and actual Codex usage at 17:29 and 17:30 KST. Their parent tasks and the answer's follow-up turn completed by 17:33 KST. The short `busy` subscription-lane waits preceding completion are historical errors retained on the original rows; completed results were not replaced or replayed.

By the **19:41 KST** readback, the Maintainer had completed **three real diagnostic calls**, requested actual repository inspections, and preserved the current technical-error cases. The explicit review is still in `triage`; its current reason is `company_work_has_priority`, with a research turn running. Its diagnostic revision subsequently changed with current implementation evidence, so earlier inspection rounds are historical rather than treated as valid current findings.

**An automatic repair finding, tested patch and bot-generated PR have not yet completed.** The real diagnosis calls are not claimed as a full automatic repair cycle. Their recorded model is `gpt-5.6-sol`; the recovered director calls used `gpt-6-astra`. Model policy was not changed.

Actual input/cache/output totals are produced from the stored usage by [the aggregation script](../../scripts/summarize_maintenance_cycle_usage.py) in [usage.json](evidence/maintainer-live-cycle-20261006/usage.json). The official [Codex JSON output documentation](https://learn.chatgpt.com/docs/non-interactive-mode) defines the observed usage fields. Counts here cover these calls only; there is no comparable baseline inference for rejected inputs and no subscription-percentage or dollar-cost estimate.

## Evidence

- [Original Temporal failures](evidence/maintainer-live-cycle-20261006/temporal-before.json)
- [Rolled-back production input qualification](evidence/maintainer-live-cycle-20261006/qualification.json)
- [Source/module overlay manifest](evidence/maintainer-live-cycle-20261006/overlay-manifest.json)
- [Worker-only cutover and retained receipts](evidence/maintainer-live-cycle-20261006/cutover.json)
- [Actual recovered turns and Maintainer calls](evidence/maintainer-live-cycle-20261006/live-20261006T1041.json)
- [Repository inspection responses](evidence/maintainer-live-cycle-20261006/maintenance-diagnosis.json)

Next operation: let current authorized company work drain and observe this existing review's actual repair decision and subsequent validation/PR. Preserve signed approval and uncertain-effect reconciliation boundaries. Do not force an unnecessary patch if the evidence supports no finding.
