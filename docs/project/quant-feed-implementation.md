# Quant-feed implementation / qualification

2026-09-23. Implemented and activated in scoter, then reactivated after an independently owned
combined-channel release. The first natural Slack delivery exposed an editorial false positive;
Quant Scout publication is now paused while collection continues. The elapsed 48-hour observation
and a quality-approved resumption remain pending.

Implemented: dedicated source registry and collection leases, original HTML/PDF retrieval,
DOI/arXiv version aliases, Korean typed brief, separate AI evidence critique, one bounded rewrite,
per-document holds, atomic Slack outbox, KST window/pacing, correction links/priority, native
search slots, status/probe/explicit live preview, independent third Codex lock and Temporal worker.
No daily publication cap. Defaults are disabled. Separate Quant Scout manifest and matching avatar.

The deployed code passed **1,057 local tests with 37 explicit skips** and Ruff passed. After merging
the latest main branch, the combined suite passed **1,137 with 37 skips and one opt-in live test
excluded**; Ruff passed again. GitHub Actions runs 35800220559 (deployed code) and 35809276862
(merged branch) passed. Unit and integration fixtures
distinguish simulated proposals from the production qualification below; passing contract cases
does not by itself measure long-run curation quality.

The public-source probe is real and credential-free; it does not prove full-text availability or
publication quality. Source titles/snippets are not accepted as evidence. Updated NBER to the
real asset-pricing RSS, parsed KCMI numeric report links without JS execution, followed the
official Research Affiliates pointer to Syzygy. The complete dated probe is under evidence.

User clarification during implementation: the company/research worker belongs to a separate task.
Quant deployment must observe and preserve its exact container ID, running/OOM state and restart
count, never start, stop or recreate it. Reporter/news also remains separate.

Linux CI passed for the initial implementation and every final policy change. The quant editorial
activity allows the runtime's 900-second model limit to complete while its own sustainable cadence
keeps one active Quant request at a time.

Slack installation verified via real auth.test: Quant Scout `A0C3HFP831Q`, bot user
`U0C3288AXPH`, bot `B0C3CM109FX`, workspace `T0C1YRDRPNF`, sole scope `chat:write`.
The matching generated icon is persisted on the app. Credential handoff appended only
`quant_scout` to the host's private store; all six existing entries were unchanged. Channel
`#quant-feeds` was resolved in the authenticated workspace as `C0C3K8ZB9PB`.

The explicit `deploy/quant_feed_release.py` operator path builds all five images from the
committed Dockerfile/lock in a 512 MiB, one-CPU build container. It verifies installed source
and the exact qdata tree, snapshots actual running services, and refuses if the independently owned
research worker changes state during the cutover boundary. Cutover and rollback never start,
stop or recreate that worker. Activation begins
with publication disabled. A crashed cutover requires manual journal reconciliation; do not
blindly repeat it. The helper is not an autonomous maintenance-policy expansion.

All five images were rebuilt and source-verified at
`377335dcab983007a998a2166807d7332548e024`. A real subscription preview completed
review → critique/revise → one revision → critique/pass with zero Slack writes, and the explicit
activation gate then enabled publication. A later combined release temporarily restored the
publication gate to disabled; the scoped reactivation below enabled it again. The first natural
Slack delivery and elapsed 48-hour observation remain pending.

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

Release state-preservation, rollback and real Temporal/deployment tests pass. The combined main
branch regression passed 1,137 with 37 explicit skips, one opt-in live exclusion and Ruff passed.

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
still rebuilt and their installed source inventory is reverified. A cold build requires 640 MiB
host availability; a same-base cache rebuild requires 512 MiB, matching the builder's hard cap.

Complete originals are preferred within the source-fairness tier. Long originals are sampled
across head, middle and tail rather than truncated only at the beginning, and predictable public
NBER paper URLs can resolve to their PDF originals. Exact-quote comparison applies Unicode NFKC
and ignores layout whitespace only; changed words still fail. Raw JSON control characters are
normalized only after a structurally valid parse. A quote/link contract failure may request one
deterministic revision, while a second failure is held. Production exercised both the one-repair
path and the second-failure stop.

## Production activation, 09:28 KST

The operator rebuilt five images from the committed package and exact qdata tree, took a
secret-free backup, preserved PostgreSQL, and moved from `preview_active` to `live_active` only
after one complete real-subscription preview, zero publications, zero running Quant calls and zero
sending outbox rows. Slack `auth.test` matched Quant Scout app `A0C3HFP831Q`, bot user
`U0C3288AXPH`, workspace `T0C1YRDRPNF` and `#quant-feeds` channel `C0C3K8ZB9PB`.

With research, news and Quant workers running together, the observed memory snapshot was 97.0,
89.73 and 88.57 MiB respectively; the shared Codex runtime was 194.1 MiB of its 512 MiB limit and
the host retained 593 MiB available. All four containers had zero OOM kills and zero restarts. The
research container ID was unchanged across cutover and activation. This is an activation/load
snapshot, not yet an elapsed 48-hour stability result. The exact receipt is
`evidence/quant-feed-live-activation-20260923.json`.

At 00:30 UTC, the Quant worker exited with code 143 (SIGTERM), zero OOM kills and zero restarts.
The shutdown initiator has not been proven. An independently owned combined release later restored
the worker on `f40fa10755004db724d6c5c74ebadc3eab4ebec2`, but left
`QUANT_FEED_PUBLISH_ENABLED=false` in its candidate environment.

## Scoped reactivation, 10:58 KST

The `reactivate` operator action requires the earlier Quant `live_active` receipt and the exact
combined-release handoff receipt; it obtains the shared deployment lock, verifies the real preview,
zero running Quant calls and zero sending outbox rows, rechecks the Quant Scout Slack identity,
and preserves the data-watch Compose overlay. It recreates only API, dispatch and Quant worker,
then verifies the company/research worker and the independent news, data-watch, Slack-socket,
Codex and Claude containers were preserved. The shared Codex and Claude images legitimately
remain at the previous verified revision; the health gate records that explicit mixed-image
baseline instead of requiring them to be rebuilt.

The first reactivation attempt failed closed at that mixed-image gate. Its rollback restored the
publication environment to `false`; the next attempt verified both dispatch and Quant worker
also saw `false` before retrying. The corrected action reached `live_active` at
2026-09-23 01:58:41 UTC (10:58:41 KST). Activation recorded one complete preview,
zero publications, zero running Quant calls, zero sending outbox rows, and Slack `auth.test`
matching app `A0C3HFP831Q`, bot user `U0C3288AXPH`, workspace `T0C1YRDRPNF`.
Dispatch reported `publish_enabled=true` and `authorized=true`; the research worker ID and
restart count were unchanged. Host available memory was 414 MiB at this gate, without an OOM.

At 02:00 UTC the new lane was active with 120 source documents ready for editorial review,
but no delivered Slack message yet. The company-wide runtime control had a `quota` pause until
02:12 UTC and extended it to 02:27 UTC at the next observation. Both Temporal collection and
editorial workflows remained running, and the source backlog advanced to 122 ready documents.
We did not bypass that limit or publish a hand-picked item. The actual natural
publication and elapsed stability observation are separate gates; current evidence is in
`evidence/quant-feed-post-combined-recovery-20260923.json`.

## Account-gateway handoff and resumed editorial work, 15:05 KST

A separately owned model-account release moved production to
`deeb846d7942243165a3de6fd9ca7e35b8f49d89`. Quant publication remained enabled, its
worker and the new account gateway were both running, and the company-wide quota pause had
cleared. The server Codex CLI reported ChatGPT authentication, and several real company-lane
model calls completed after 05:46 UTC. Both Quant review and revision runtime jobs completed
with `account.profile=backup`, revision 1. This confirms the selected subscription profile
served Quant calls, though this task did not inspect the underlying account identity. No API-key
fallback was made by this task.

Quant collection continued and the editorial workflow made its first new calls at 05:50 UTC.
One reviewed candidate then failed the one permitted evidence/contract repair and was held at
05:53 UTC without any Slack write. This is the intended fail-closed quality gate. The first
natural delivered post and elapsed 48-hour stability check remain pending. The exact follow-up
receipt is `evidence/quant-feed-after-account-gateway-20260923.json`. The research worker was
recreated by the separate account release before this observation; no change to it was made by
the Quant task.

## Latest main compatibility, 15:16 KST

The account-gateway release and its dedicated-channel follow-up on main were merged into the
Quant branch without changing the separately owned production services. Dispatch now starts
Quant collection/editorial and account control together, while deployment service inventories
include both `quant-feed-worker` and `account-gateway`. A new regression checks both workflow
families start once on their separate queues. The latest CI-equivalent local suite passed 1,186
tests (37 skips, one opt-in live test deselected), and Ruff passed. This validates repository
integration; it does not replace the still-pending natural Slack delivery observation.

## Source-quality follow-up, 15:28 KST

The next natural review ran at 06:23:58–06:25:40 UTC on the selected `backup` subscription
profile. It reviewed a generic cloud-security paper from Two Sigma's broad insights index and
held it, with no Slack write. The recorded hold reason was an unverifiable original publication
date, **not** an explicit topical rejection; this revealed that source relevance should be
tighter before model use.

A credential-free probe of Two Sigma's official `Markets & Economy` index returned 10 article
links through the existing collector; its two generic archive links were excluded. The source
registry now targets that index, and the editor
instructions explicitly exclude unrelated infrastructure, cloud security and career/interview
content. Separately, a failing regression reproduced hidden CSS text leaking into institutional
link titles; one parser condition now ignores that hidden text. Quant tests passed 59 with one
platform skip, and Ruff passed. These source and parser changes were merged in PR #60, but not yet in the
separately owned production release; existing database candidates were not rewritten. The exact
observation and remaining activation gates are in
`evidence/quant-feed-source-quality-20260923.json`.

## First delivery and publication pause, 16:44 KST

At 06:58:54 UTC Quant Scout delivered its first natural post to `#quant-feeds`, a Robeco article
about general agentic-AI governance. It was accurately labeled as lacking a market sample,
quantitative validation and backtest results, yet the critic marked it publishable. This is an
editorial scope/depth false positive, not evidence that the material meets the requested quant
research standard. The Slack card also repeated links, exposed an internal topic enum and packed
too many fields without visual grouping. The user elected to keep that existing post and to pause
only future Quant Scout publication until a stricter gate is deployed. We did not edit or delete it.

The pause operator checked zero running Quant model calls and zero pending/sending Quant outbox
items, then changed `QUANT_FEED_PUBLISH_ENABLED` to `false` and recreated only API, dispatch and
Quant worker to propagate the setting. The Quant worker remained running, so collection and
preview review continue. Research, news, data-watch, account gateway, Slack socket and both model
runtimes retained their exact container IDs and restart counts; PostgreSQL was not recreated.
An independent status readback reported `enabled=true`, `publish_enabled=false`, and the original
one delivered outbox entry unchanged. The private original environment snapshot and pause journal
remain on the host; a secret-free receipt is in `evidence/quant-feed-publication-pause-20260923.json`.
At 08:04 UTC, a second readback still showed only that delivered entry while ready documents
rose from 175 to 177 and held documents from 52 to 53, confirming collection and review activity
continued after the pause.

The follow-up code requires the independent critic to affirm both direct quantitative-finance
scope and substantive research before `pass`; instructions explicitly reject generic AI
governance or organizational commentary even from an asset manager. Slack rendering is grouped
into a short research card. A separate deterministic contract rejects an institutional brief
whose data/sample and validation/method fields both declare no research basis; formal theory and
methodology work use their own categories and are not blocked by that rule. The card suppresses
the raw topic enum and duplicate original link, and no longer labels every related URL as
code/data. These changes are **not deployed** by the pause.
The release operator was also extended to preserve the research worker unchanged whether the
separate task has it running or intentionally stopped. The initial quality patch passed 1,192
local regression tests with 37 skips and one opt-in live test excluded; Ruff passed. The later
stopped-worker operator refinement passed its four focused tests and awaits CI with the branch.
Publication must remain disabled until the change is merged, a scoped production release and real
subscription previews demonstrate the quality bar, and a fresh explicit activation is reviewed.
The 48-hour stability observation has not elapsed.

## Quality-gate follow-up after the 4 GiB migration, 28 September

PR #72 (shorter grouped Slack card, direct quant scope and substantive-research critique gates,
deterministic institutional-research check) and PR #76 (unique-location correction for an exact
quote in the supplied original excerpts) are merged into `main`. The card is capped at 2,400
characters. The citation gate never substitutes or invents quotation text: absent or ambiguous
quotes and unregistered links still fail. Local regression and Ruff, followed by Linux CI, passed
for both changes.

Production publication remained disabled throughout. The quality release was built and source
verified as `0c6dcba5ce747f242c2aa919ab70e71078df9e1e`, then tested in an isolated one-off
container without a service cutover or Slack write. A fresh real-subscription negative preview
held/rejected the previously mispublished generic Robeco AI-governance article. A stored Two Sigma
regime-modeling brief passed frozen-original citation validation and a new independent
real-subscription critique (direct quant scope, substantive research and all evidence checks);
its grouped card was 1,653 characters. This is **not** a fresh end-to-end positive: new AQR and
Two Sigma drafts failed closed because model-generated exact quotes or links were unsupported by
the supplied originals. The acceptance gate for fresh positive generation therefore remains open.

At the final read-only check (04:56:49 UTC), Quant collection was enabled, publication disabled,
one earlier Robeco post remained delivered, and no Quant model call was running. Housing's
independently operated dispatch and worker were on `ccdcc43c2a0985dcc035262f33d19e0ae979f0a1`,
while the API and Quant worker were on the earlier quality image `12398af25dc111fd93d99fa3408fbc66cb699121`.
The current release symlink also pointed to the Housing revision. Cutting over the staged
Quant-only image would replace that dispatch with an image lacking Housing code, so the cutover
was intentionally withheld. No Housing, research or news service was changed by this follow-up.

The remaining sequence is a coordinated combined release after the Housing task owns its merge,
fresh positive end-to-end source-grounded qualification, a 48-hour operational observation with
publication still off, and explicit authorization to resume posting. The compact receipt and
diagnostic scripts are under `evidence/quant-feed-quality-20260928/`; private full
subscription and release journals remain on the server.

## Narrow citation correction and independent critique, later 28 September

PR #79 merged an auditable allowance for a single ASCII first-letter capitalization difference
in an otherwise exact source quotation. Changed words, numbers and ambiguous source locations
still fail; the Quant policy fingerprint advanced to version 8. The local suite passed 1,177
tests with 38 skips, Ruff passed, and Linux CI passed. In a network-disabled replay of a saved
real Two Sigma response, the original draft still failed exact-source validation, while its
one permitted contract revision passed with one recorded initial-case correction at PDF p.2.
The rendered card was 1,511 characters.

A new independent real-subscription critic then marked that revision `revise`, **not `pass`**.
It affirmed direct quantitative-finance scope and substantive research, but requested an exact
PDF p.3 quote for the AIC/multivariate-fit claim and clearer disclosure that split methodology
and time-series leakage controls were unreported. The single allowed revision had already been
used, so the candidate was held with no Slack or document write. The one-off container preserved
all running company-service identities at its boundary. The source-grounded positive
end-to-end activation gate remains unmet.

The five-image production preparation was stopped at the disk-safety threshold after four
images, including the app image, were source-verified. The Claude image was not built; the
release journal remains `staging`, **not `staged`**, and no service cutover occurred. Concurrent
Housing work changed the production release to `c54667e2827506d50b4cbfaa63d924f2b69f21ab`;
the Quant one-off runner detected that change before its first call and stopped. A later exact-base
runner performed only the independent critique. At 06:12 UTC, collection was enabled,
publication disabled, the earlier one post remained delivered, and no Quant call was running.
At approximately 06:15 UTC, disk availability had fallen to 3.6 GiB (96% used) while a separate
image build was running. No image or cache was pruned by this task. Further Quant builds and
activation await safe storage headroom, a coordinated Housing-compatible release, a successful
fresh positive qualification, 48-hour observation and explicit owner approval. Compact evidence
is in `evidence/quant-feed-initial-case-20260928/`.

## Continuation after disk cleanup, later 28 September

Lightsail disk headroom recovered to about 20 GB, so storage no longer blocks the next
qualification. The existing combined `0e09b85` app image was independently rechecked against
its installed package source and qdata revision; it contains the newer Quant quality policy and
the Housing/data-watch integration. A new arXiv portfolio-cost paper with a full PDF, verified
authors and source metadata was selected for a fresh positive preview. The no-call preview
preflight passed, and 121 focused tests (one skip) plus Ruff passed. Two previously saved Quant
briefs still render source-valid cards of 1,653 and 1,855 characters. The original Robeco post
remains the sole delivered Quant post, and publication stays disabled.

An existing Quant review initially remained `running` with a `quota` error, while the shared
runtime quota pause kept renewing. After both cleared, one real subscription preview ran with
publication disabled. The old generic Robeco item was held by the independent critic and
rejected on fresh review. The new portfolio-cost paper's first draft failed the source
contract; its one allowed revision passed exact-source validation, but the independent critic
requested date and limitation corrections, so the fresh positive did **not** pass. No new Slack
post or release cutover occurred. The unchanged-service check also failed because the separately
operated maintenance container OOM-restarted at its 128 MiB limit during the preview; causation
by this preview is unproven. The API had independently changed to `d571734` before this call;
the Quant worker and Housing worker were not changed by this task. The exact private receipt,
check-only runner, candidate metadata and open gates are under
`evidence/quant-feed-continuation-20260928/`.

An offline replay identified the initial draft failure as an exact quotation absent from the
supplied excerpt. A full-context PDF shortlist was then assembled without another model call.
The next local patch makes arXiv's original citation date explicit in the prompt and forbids
claiming a full-paper omission from clipped-excerpt silence; the Quant policy fingerprint is
version 9. This change passed focused regressions and Ruff but remains **undeployed and without
a fresh positive qualification**. The failed version-8 private receipt must not be retried.
