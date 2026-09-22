# Quant-feed implementation / qualification

2026-09-22. Implementation in scoter; operational qualification remains in progress.

Implemented: dedicated source registry and collection leases, original HTML/PDF retrieval,
DOI/arXiv version aliases, Korean typed brief, separate AI evidence critique, one bounded rewrite,
per-document holds, atomic Slack outbox, KST window/pacing, correction links/priority, native
search slots, status/probe/explicit live preview, independent third Codex lock and Temporal worker.
No daily publication cap. Defaults are disabled. Separate Quant Scout manifest and matching avatar.

Local full service suite: **1,034 passed, 37 skipped**. Additional deployment suite: **27 passed**.
PostgreSQL and Temporal are real, model and Slack proposals are synthetic. Thirty golden contract
cases test schema/evidence gates, NOT measured AI curation quality. Linux PDF hard-limit test is
intentionally skipped on macOS and must pass in Linux CI/release qualification.

The public-source probe is real and credential-free; it does not prove full-text availability or
publication quality. Source titles/snippets are not accepted as evidence. Updated NBER to the
real asset-pricing RSS, parsed KCMI numeric report links without JS execution, followed the
official Research Affiliates pointer to Syzygy. The complete dated probe is under evidence.

User instruction during implementation: existing company/research worker was intentionally
stopped for another task. **Preserve stopped state. Do not restart it during quant deployment.**
Reporter/news remains separate. The currently deployed base observed during this work was
`4fc8132bb31d56eb1ce3f7a3c57d98afba7a8124`; deployment must check for concurrent changes.

Linux CI passed for the initial implementation (Actions run 35686244281). Integrated the
concurrent research audit-recovery change from PR #59 without activating its stopped worker.
The quant editorial activity now allows the runtime's 900-second model limit to complete.

Slack installation verified via real auth.test: Quant Scout `A0C3HFP831Q`, bot user
`U0C3288AXPH`, bot `B0C3CM109FX`, workspace `T0C1YRDRPNF`, sole scope `chat:write`.
The matching generated icon is persisted on the app. Credential handoff appended only
`quant_scout` to the host's private store; all six existing entries were unchanged. Channel
`#quant-feeds` was resolved in the authenticated workspace as `C0C3K8ZB9PB`.

The explicit `deploy/quant_feed_release.py` operator path builds all five images from the
committed Dockerfile/lock in a 512 MiB, one-CPU build container. It verifies installed source
and the exact qdata tree, snapshots actual running services, and refuses if the stopped research
worker changes state. Cutover and rollback never start/recreate that worker. Activation begins
with publication disabled. A crashed cutover requires manual journal reconciliation; do not
blindly repeat it. The helper is not an autonomous maintenance-policy expansion.

Pending: full image qualification, real subscription original/critic preview, channel invitation,
controlled activation, actual load admission, and elapsed 48-hour observation. No simulated result
is claimed as deployed.

## Deployment coordination gate, 13:30 KST

The staging attempt failed closed **before any build or runtime change** because another task
deployed `08b84d4d14d823d77b3af4ee2fab83a18a72c516` and started the research worker at
13:25:48 KST. This task did not start or stop it. The user had explicitly asked us to preserve
its stopped state, so neither the deployment baseline nor service-state policy was silently
changed. Await clarification of the other task's intended state. News remains running.
Exact public receipts are in `evidence/quant-feed-deployment-conflict-20260922.json`.

The user subsequently confirmed the research worker is intentional and independently owned. Its
state may therefore legitimately change between the long image build and cutover. The revised
release snapshots its exact container ID, running/OOM state and restart count at the cutover
boundary, then refuses any drift during cutover/rollback. It never stops, starts or recreates that
worker during staging, backup, cutover or rollback. The production capacity observation showed 701 MiB host memory available,
108.4 MiB used by the research worker and 35.18 MiB by the 512 MiB Codex runtime, with no OOM or
restart. This admits a bounded preview, not yet three-lane publication.

The first post-confirmation staging call rejected the whole-repository source archive at the
existing 32 MiB expanded-input gate, before creating a builder or changing a service. Release
archives now contain only Git-committed build inputs (`pyproject.toml`, `uv.lock`, `README.md`,
`src/`, and `deploy/`); all resulting service images are still built afresh from the committed
Dockerfile and verified against the committed package inventory. The failed attempt remains in
the release journal and is not represented as a build.

Additional post-merge targeted tests: 152 passed, one macOS PDF skip. Release state-preservation,
rollback and real Temporal/deployment tests pass (33 tests after the running-worker refinement).
The latest full local regression passed 1,071 with 36 skipped and one live test deselected; PR CI
passed. Do not claim the new feed is active until preview and delivery gates complete.

## Preview findings and source fairness

The first real subscription preview rejected or held all twelve initial AQR entries: marketing
landing pages lacked full methods/results, and unsupported exact quotes failed closed. No Slack
message was created. This also exposed source-order bias: a large commercial index could delay
NBER, Korean institutional and academic originals. Candidate fetching and document review now
prefer the source with the fewest prior fetches/reviews, while an already drafted brief's critique
retains absolute priority. PostgreSQL tests prove both rotations. This changes ordering only, not
the quality threshold or publication count policy.

During preview the company, news and quant lanes naturally overlapped without synthetic model
calls. Codex runtime memory peaked at 338,063,360 bytes under its 512 MiB limit, with zero `high`,
`max`, OOM or OOM-kill events. The quant worker used about 73 MiB under its 256 MiB limit. Full
image rebuilds reuse only the prior bounded BuildKit cache after an exact-base check; images are
still rebuilt and their installed source inventory is reverified.
