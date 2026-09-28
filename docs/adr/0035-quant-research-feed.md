# ADR 0035 — Quant Scout evidence-first research curation

2026-09-22. User-approved implementation; delivery activation is separately qualified.

## Decision

Add an outbound-only `quant_scout` Slack identity for `#quant-feeds`. Its only Slack scope is
`chat:write`; it has no event subscriptions, tools, delegations or conversational company turns.
The avatar uses the existing ceramic-robot family. Reporter and Tech Scout retain their identities.

The independent `quant-feed-worker` owns collection and editorial Temporal queues. All quant
search, review, revision and critique calls use `quant-feed-*` IDs and `.runtime-quant.lock`.
Ordinary company work and news retain their original locks, request digests and receipts.
One model call per lane, three total. Shared daily model budgets and quota pauses still apply;
no API-key fallback or subscription purchase. No training, backtesting, orders or 5090 fallback.

PostgreSQL owns source leases, candidates, aliases, immutable text versions, frozen requests,
responses, briefs, critiques and publication receipts. Temporal owns retries/timers. A crash retries
the same request ID; ambiguous calls are held, never replaced to bypass a charge receipt.
An ambiguous document does not block other documents. An ambiguous Slack write is not replayed.

## Editorial policy

Korean and US equities are central, with explicitly transferable other-market methods. Cover
factors/alpha, ML/forecasting, portfolio/risk, costs/execution, research validity, replication/data.
Negative results and promising hypotheses can qualify; label maturity and limitations. Include
classics with original dates and a current reason to read. No daily publication target or cap.

Each original passes a type-aware Korean editorial call and a separate AI evidence critique.
One rewrite is allowed. Missing evidence then holds that document. Theory does not need a
backtest; empirical omissions are exposed, not fabricated. Code availability, peer review and
AI critique do not establish local reproduction or tradability. Institutional commercial bias is
disclosed. Local point-in-time data availability remains unverified unless a separate task checks it.

Every published brief includes authors, original date, market/type, why read, mechanism, data,
validation, author-reported results, costs/turnover, limitations, application prerequisites and links.
Exact evidence quotations and page/section references are validated against the frozen version;
quotes remain internal instead of copying paper passages into Slack. The second model must
check semantic support. This is a probabilistic AI check, not a guarantee of factual correctness.

DOI/arXiv/exact title+author aliases link preprints and journal versions. Cosmetic changes are not
republished. Material changes and corrections link the previous delivered Slack post. Unresolved
prior delivery blocks updates to that work. Corrections receive outbox priority, otherwise FIFO by
review completion. Delivery is KST 06:00–24:00 at least 60 seconds apart. Nighttime collection
continues. Preview results are not silently replayed when publication is enabled.

## Sources and resource boundaries

The explicit registry includes arXiv q-fin, NBER asset-pricing RSS, Crossref journal metadata,
AQR, Robeco, Two Sigma, Man, former Research Affiliates/Syzygy, KCMI, BOK, KJFS and official
author/code/data resources. Metadata is discovery, not full-text evidence. Native subscription
search runs at 08:00/20:00 KST with rotating topics; Monday's sweep adds classics, follow-ups,
refutations and corrections. Each search is bounded, not an exhaustive literature census.

Source polling honors configured 1–168 hour intervals and Retry-After/backoff. The 2026-09-22
probe found NBER's RSS usable, KCMI numeric report links behind fixed JS markup, and the former
Research Affiliates domain explicitly routing to Syzygy. HTML zero-candidate results are flagged
as structure/access issues, not proof that nothing was published. Source failures remain isolated.

Only public HTTPS, exact registered article hosts, checked public DNS addresses, pinned socket
addresses, host TLS validation and per-hop redirect checks. No authentication/paywall bypass or
automatic professional eligibility acceptance. No PDF republication or paid OCR. Downloads are
bounded to 20 MiB PDF / 2 MiB HTML and 60 seconds. Linux PDF subprocesses get no credentials,
`-I`, 128 MiB address-space, 50 CPU seconds, 60 wall seconds, 200 pages, bounded text and no core
dumps. The parser does not execute PDF actions/attachments. The worker has 256 MiB / 0.5 CPU.
On non-Linux hosts PDF extraction fails closed; Linux CI/release must qualify the real limits.
Scans, unreadable math/tables and insufficient/truncated context are held or use a public HTML copy.

`pypdf==6.18.0` is promoted to a direct dependency, matching the already pinned qdata integration.
No research repository is modified. Lock generation uses an isolated archive of the approved qdata
commit. Images must be built with the full committed dependency inputs, not assumed compatible
solely because a wheel installs. Host memory and 3-lane/PDF load are activation gates, not inferred
from container hard-limit sums. No automatic paid resize.

## Qualification

Thirty synthetic editorial-contract cases test deterministic gates, not AI selection accuracy.
Real PostgreSQL, Temporal history replay and subprocess lane locks are tested separately from
mocked model/Slack delivery. Public-source probe, real subscription preview, Linux PDF limits,
real Slack identity/delivery and production load/48-hour observation need their own dated evidence.
Do not label unperformed gates as deployed or verified.

Official references: [arXiv feeds](https://info.arxiv.org/help/rss.html),
[Crossref REST practices](https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/),
[PDF extraction limitations](https://pypdf.readthedocs.io/en/6.18.0/user/extract-text.html),
[Codex non-interactive authentication](https://learn.chatgpt.com/docs/non-interactive-mode).
