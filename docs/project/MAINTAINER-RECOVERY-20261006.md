# Maintainer recovery — 2026-10-06

User approval: “그래 진행해”, following the proposal to connect real technical failures, repair oversized diagnostic input, and qualify diagnosis → repair → validation → PR.

## Diagnosis and change

The production Maintainer heartbeat was healthy, but no diagnostic job had been created since the improvement-channel activation. Thirty earlier triage jobs stopped with `maintenance_proposal_context_too_large`. The improvement collector excluded research waiting events and independent review failures.

The new collector observes repeated response-contract errors on current active research stages, including the actual `program_data` DataAssessment error recorded on October 6. It checks owner, revision, actor, task kind, channel and active program/mission state. Normal auth/quota/data waits remain excluded. Independent `invalid_review` and `uncertain` failures become operational diagnostic observations; the original request, receipts and objective employee grade are preserved.

Oversized diagnosis prompts progressively reduce repository path catalogs and observation details. Exact evidence keys and already inspected source paths remain available. Omission counts and lookup instructions are explicit. Full stored evidence and reserved request identities remain unchanged.

## Qualification

- The real blocked PostgreSQL input (job `45dce3da-b3dc-5196-b756-35025c48c2f1`) reproduces the baseline size failure. Candidate formatting produces 87,770 characters from 195,297 characters and passes the provider request contract. No inference was performed. This measures characters, not billed tokens.
- Full tests: **1,404 passed, 46 skipped**. Final maintenance-focused tests: **80 passed**, including the last active-parent/paused-child guard. Lint and diff checks pass.
- PostgreSQL and Temporal are real local test services. Model, Slack and GitHub interactions in pipeline tests are synthetic; their PR receipt is a fixture.
- Production read-only snapshot records the still-installed September 23 Maintainer and hashes of all ten blocked uncertain review rows.

Evidence: [context reproduction](evidence/maintainer-recovery-20261006/context-reproduction.json), [tests](evidence/maintainer-recovery-20261006/tests.json), [production baseline](evidence/maintainer-recovery-20261006/production-before.json).

## Operational rollout

Only the Maintainer service is eligible for this repair rollout. Its immutable candidate image will use the recorded existing image as its base after byte-identity checks for project metadata, lockfile and entrypoint. Existing company/research/feed services and release configuration are independently pinned.

The Maintainer was replaced at **2026-10-06 15:37 KST** with code commit `fadc0e54be0c470068bb18011cc6a1a9ec8ee971` and image ID `sha256:b13f2bc9ad2258acc53b679258f4a6bb5d0ad81019cd4605bc1aaeed63df868c`. The image smoke check ran with no network or model calls. GitHub CI for PR [#106](https://github.com/JJongAchii/quant-ai-company/pull/106) passed.

The maintenance-only Compose override preserves the existing allowed channels and feed publishing flag, which differed from the newer global environment file. All other container IDs/images, the global release symlink, runtime environment (apart from the code commit), mounts, UID, memory/CPU and security settings stayed unchanged across cutover. The ten uncertain review rows and all 56 existing maintenance call rows retained their hashes. A durable operator journal contains the rollback image and exact stack.

The first readback at 15:39 KST found the new service running without a restart or OOM, with matching installed source hash and fresh reporting heartbeat. The next observation deadline was 15:45:56 KST; the original workflow/timer was retained. Three current company turns had priority over new maintenance inference. The later scheduled observation result will be recorded separately.

Evidence: [qualified image](evidence/maintainer-recovery-20261006/image-qualified.json), [cutover and rollback](evidence/maintainer-recovery-20261006/cutover.json), [first live readback](evidence/maintainer-recovery-20261006/live-before-first-poll.json). The operator programs in that directory are formatted copies of the one-off commands used; they are not scheduled jobs. Their journal guards prevent replaying an unresolved cutover.

The override path is recorded in the running container's Compose labels and survives normal container/host restart. Future operator Compose releases must retain this maintenance override or explicitly qualify its replacement. Bot-generated repair deployment remains bound to the exact existing signed owner approval rules. Company-work priority, uncertain-effect reconciliation and evidence-change guards remain active.
