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
- Initial complete service suite: **1,444 passed, 46 skipped**, two existing deprecation warnings, 385.67 seconds. PostgreSQL and Temporal are real local test services; model/Slack/GitHub pipeline responses are test fixtures.
- Integration with model-assignment main: **75 focused tests passed, 1 skipped**; full suite **1,490 passed, 48 skipped**, two existing warnings, 376.56 seconds. Later main integration preserves the account help, pinned CLI release and model policy changes; its final result is recorded in the integration evidence.
- Final integration with main `a9ef0b5fec77e9135c82bc75213226183d57b660`: **1,520 passed, 49 skipped**, zero failures/errors, two existing warnings, 372.00 seconds; lint and diff checks passed. [Validation receipt](evidence/maintainer-live-cycle-20261006/integration-validation.json) includes tested source/lock hashes.
- Lint and diff checks pass. New cases cover complete prompt budgeting for work/answer turns, preserved stored evidence and request snapshots, redacted terminal validation faults, and irreducible instruction budgets.
- Production worker module hashes matched code base `ec1fbeb29a49420a367f06af596283e6ead4ab72`. Only the company/execution module diff from reviewed patch commit `87e774fbb5caafb6cf56bc14af678699ccacea10` was applied over its existing immutable held-audit image. Its briefing, research/native-output features and dependencies were retained.
- The worker was activated at **17:27:34 KST**. Image ID: `sha256:edb93937acda68bfe7ef17307eef3832b7f775665fddd69e98658048741848fb`. Patch identity is the `quant-company.turn-context-patch` image label; the base release declaration is preserved, not represented as an entire main rebuild.
- All installed environment values, UID, entrypoint/command, mounts, networks, security settings, 384MiB memory and CPU limits were preserved. Other service container IDs/images and the global release symlink were unchanged. All ten blocked uncertain independent-review hashes and all 56 historical maintenance-call hashes remained unchanged.
- A persistent worker-only Compose overlay and durable cutover/rollback receipt are recorded. Future operator releases must preserve this overlay or qualify its replacement. No model/runtime restart, credential transfer, manual model replay or Slack approval fabrication was performed.

## Real execution observed

Both original turns completed with their original IDs and actual Codex usage at 17:29 and 17:30 KST. Their parent tasks and the answer's follow-up turn completed by 17:33 KST. The short `busy` subscription-lane waits preceding completion are historical errors retained on the original rows; completed results were not replaced or replayed.

By the **2026-10-07 08:04 KST** usage readback, the Maintainer had completed **24 real diagnostic calls** across four cases. It requested actual repository inspections. Its final case states were read at **08:28 KST**:

| Case | Actual calls | Final state | Recorded boundary |
|---|---:|---|---|
| Explicit review `6f3c54a0` | 5 | blocked | `evidence_churn_requires_review` |
| Automatic case `a4aabd4e` | 9 | blocked | `investigation_budget_exhausted` |
| Automatic case `454ef5b7` | 5 | blocked | `evidence_churn_requires_review` |
| Automatic case `19aa1178` | 5 | done, no finding | Original uncertain-review receipts and validation evidence unavailable to the diagnosis; operator reconciliation required |

The first two evidence-change revisions include different Git commits; another archived revision has the same commit and a different scope digest. The scope also includes runtime configuration, role definitions, deployed code and assessments. The existing guard stops a fourth revision; historical inspection evidence is not treated as a current finding. Slack delivered the blocked explicit-review notice to its real case thread. The temporary `subscription_paused` wait observed earlier is historical; the latest states above supersede it.

**An automatic repair finding, tested patch and bot-generated PR have not completed.** The later `done` receipt is a recorded no-finding conclusion with an evidence limit, not independent proof that the current code has no defect. No original uncertain review was replayed or employee grade changed. [PR #110](https://github.com/JJongAchii/quant-ai-company/pull/110) is the operator-authored director recovery and evidence PR; it is not a bot-generated PR.

The recovered director calls used `gpt-6-astra`; the first 19 diagnostic calls used `gpt-5.6-sol`, and five later calls used `gpt-6.1-sol` under the separately deployed owner model-policy update. This repair does not change model policy or overwrite that rollout.

Actual input/cache/output totals are produced from stored usage by [the aggregation script](../../scripts/summarize_maintenance_cycle_usage.py) in [usage-final.json](evidence/maintainer-live-cycle-20261006/usage-final.json):

| Scope | Completed calls | Input tokens | Cached input (included in input) | Output tokens |
|---|---:|---:|---:|---:|
| Recovered director turns | 2 | 80,656 | 5,888 | 3,838 |
| Maintainer diagnoses in the observation window | 24 | 882,903 | 60,800 | 76,847 |

The Maintainer's observed input cache fraction is **6.89%**. Repeated diagnosis revisions remain a measured source of input usage; this patch prevents the director's preparation loop and is not evidence of a completed optimization of diagnosis revisions. The official [Codex JSON output documentation](https://learn.chatgpt.com/docs/non-interactive-mode) defines the observed usage fields. Counts cover these recorded calls only; there is no comparable baseline inference for rejected inputs and no subscription-percentage or dollar-cost estimate.

## Evidence

- [Original Temporal failures](evidence/maintainer-live-cycle-20261006/temporal-before.json)
- [Rolled-back production input qualification](evidence/maintainer-live-cycle-20261006/qualification.json)
- [Source/module overlay manifest](evidence/maintainer-live-cycle-20261006/overlay-manifest.json)
- [Worker-only cutover and retained receipts](evidence/maintainer-live-cycle-20261006/cutover.json)
- [Actual recovered turns and Maintainer calls](evidence/maintainer-live-cycle-20261006/live-20261006T1041.json)
- [Final calls and durable states, 2026-10-07 08:04 KST](evidence/maintainer-live-cycle-20261006/live-20261006T2304.json)
- [Repository inspection responses](evidence/maintainer-live-cycle-20261006/maintenance-diagnosis.json)
- [Terminal diagnoses, preserved revisions and delivered Slack notice](evidence/maintainer-live-cycle-20261006/maintenance-terminal.json)

## Concrete restart boundary

There is no supported command that clears `evidence_churn_requires_review` in place. A new explicit diagnosis is a separate immutable request: `maintenance/requests.py:submit` requires a recorded human inbound task, and `maintenance/identity.py` requires an explicit request before using `maintenance_review`. The Slack ingress accepts it only in the configured workspace/user/channel. This chat approval cannot be manufactured into a signed Slack event.

After repository/deployment evidence is stable, the owner can send **“기존 검토 6f3c54a0가 증거 변경으로 중단됐다. 현재 운영 코드와 원 호출 영수증을 기준으로 새 진단을 요청한다. 불확실한 과거 호출은 재실행하지 말고 대사하라.”** as a new message in the configured improvements channel, or explicitly request a new diagnosis in [the existing case thread](https://app.slack.com/archives/C0C3Q7BFQCE/p1791269678293859). The new request gets a new case; old requests, receipts and blocked states remain preserved. A diagnosis cannot establish what happened to uncertain external calls without their original runtime/delivery/validation receipts. Any resulting exact PR candidate has the existing separate application approval.

Queue recovery and usage measurement are complete. The live automatic repair/test/PR objective remains partial at the recorded review and evidence boundaries. Record this attachment as partial; do not claim that the automatic repair pipeline completed.
