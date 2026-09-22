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

Pending: Linux CI/full image qualification, real subscription original/critic preview, Slack
installation/icon/channel receipt, controlled activation, actual load admission, and elapsed
48-hour observation. No simulated result is claimed as deployed.
