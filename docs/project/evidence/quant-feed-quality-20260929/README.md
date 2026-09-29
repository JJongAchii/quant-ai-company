# Quant Scout source and editorial quality repair — 29 September 2026

Publication remains disabled. This work addresses observed false candidates and a
source-scope error; a 48-hour service observation alone is not editorial evidence.

Read-only production diagnosis found 305 `ready` documents. Of these, 236 came from
the Syzygy/Research Affiliates source. A sampled public article page contained its
title, menus, a long disclaimer and related links, but no substantive article body;
the stored candidate title also included CSS. Two Sigma contributed 50 ready
documents, including careers, interviews and corporate announcements. The current
collector followed all `/articles/` links on its topic page, including links outside
the actual article cards. The latest natural HARN paper was correctly held after
independent critique: one qualified gap said `제공 원문` despite clipped input, and
another gap obscured event-time alignment disclosed in the supplied original.

The scoped repair disables Syzygy until a public original or PDF path is verified,
quarantines its existing ready documents without deleting them, restricts Two Sigma
collection to the official listing cards, and holds unambiguous non-research titles
before a model call. Ambiguous titles still receive full editorial review. For a
clipped original, the service narrows the exact phrase `제공 원문` to `제공 발췌` and
audits that correction; truly unscoped absence claims fail. One additional bounded
editorial revision is permitted, but every revision still needs a fresh independent
critique and the second failed critique remains held. Neither an uncertain model
call nor a held document is replayed in production.

Acceptance remains separate: local and Linux regression checks; a fresh live source
probe; a private real-subscription negative and more than one positive original;
no Slack writes; and a source/quality/operations readback before any publication
approval. The existing 48-hour operational clock will restart if the deployed Quant
code or policy changes. Passing software tests or a single positive preview does
not establish long-run curation precision.
