# 0035 — Dedicated Market Briefer

Status: accepted for implementation; production activation pending qualification.

The owner requested a high-priority daily market briefing in `daily_brief`, selected balanced US/Korea
coverage, a 3–5 minute main post with detailed thread, trading-day delivery, and 07:30 KST / KRX close +
50 minutes. The financial strategist remains the strategy-development lead. A separate Market Briefer
owns publication and follow-up questions; only an explicit human request reaches the strategist.

Reuse approved news originals, subscription search, the news model execution lane, PostgreSQL and the
existing Slack outbox. Add typed market observations, calendar events, proposals, reviews and immutable
edition snapshots. A distinct collection queue keeps collection outside the serialized model slot.
New ordinary news model requests defer while a scheduled brief is being prepared; existing calls finish.

An edition uses the actual exchange session and Korean delivery date, a cutoff 20 minutes before delivery,
and stable owner/channel/day/edition identity. Two bounded model calls draft and semantically review the
source bundle. Numeric and citation checks are deterministic. Separate calls of the same model are not a
claim of independent factual certainty. Missing core closes visibly reduce the edition. A deadline notice
commits without publishing unreviewed prose. Late model responses cannot replace an already committed issue.

Frozen model IDs and requests survive retries. The root Slack receipt precedes thread creation; ambiguous
writes are not automatically replayed. Publication gates check live policy, authorization, project revision,
expiry and parent receipt both at claim and immediately before network I/O. No exactly-once delivery claim.

Default collection and publication are off. Source probes, synthetic content with real local PostgreSQL/
Temporal, real Codex validation, real Slack acceptance, deployment, and five-trading-day observation are
distinct evidence categories. The existing research repositories and their contracts remain unchanged.

Operational details and limitations: [briefing guide](../briefing.md).

## Quality revision — 2026-09-22

The owner's subsequent instruction prioritizes understanding the day's market from the main post alone.
Keep the 07:30 KST morning edition. Move the KRX edition to 20:00 KST, never earlier than actual close +
50 minutes: an actual read of the lake found the previous session's clean KRX object uploaded at 19:24 KST.
This single observation informs the schedule, not a guarantee of future arrival. Collection refreshes every
five minutes from 19:20 until the 19:40 cutoff; stale rows never become current-session closes.

Reuse the existing lake-enabled general worker for fixed, small qdata API queries in a credential-limited
child process. No new worker container, paid source, model slot or research-repository change is required.
Compute KRX changes from frozen rows. ETF adjusted-price comparisons and macro observations remain dated
background context, distinct from current market prices. Preserve object identities before and after reads.
Bound bytes, rows, columns, runtime and output; do not scan the large stock-price and investor-flow tables.

The service reconciles duplicate quotes and withholds unresolved conflicting numbers. Writing and review
use original news plus the service-controlled facts. Review explicitly checks numbers, sources, timing,
causality, materiality/readability and counterevidence; an empty review cannot authorize publication.
The main post uses a short conclusion, key numbers, the main drivers and observable next checkpoints.
Five analytical headings are editorial criteria, not a repetitive reader-facing form. Reserve main-message
space for checkpoints. Conclusion-changing contrary evidence belongs in the main text too.

Read-only replay and five-completed-trading-day qualification expose missing coverage, single-source core
quotes, fixture inputs and absent real Codex receipts. They do not activate publication or replace actual
Slack delivery and human content acceptance. See [quality evidence](../project/MARKET-BRIEFER-QUALITY.md).
