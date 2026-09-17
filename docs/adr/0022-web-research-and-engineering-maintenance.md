# Web research and engineering maintenance

Status: implemented and locally validated; production rollout pending. User request: PR #16 must actually enable external research, and the maintenance BOT must investigate, implement and verify improvements rather than stop at a design proposal.

## Decision

Employees receive `web_search` and `web_read` service tools. Search uses the existing ChatGPT-authenticated Codex runtime in an explicitly scoped native-search request; no paid API fallback is introduced. Native search execution must be observed in the runtime event stream. Search candidates are unverified and cannot be cited as registered sources. `web_read` retrieves a public original, preserves bytes, content, URL, available publication metadata and retrieval time, and registers a project-scoped source. Network work happens outside database transactions. Saved requests, the company model budget and revision checks also apply to search.

The maintenance service distinguishes explicit feature requests from diagnosis and normal research. It may request additional exact-commit code evidence, add service modules with a behavioral regression, and revise a failed candidate from actual CI diagnostics within a finite attempt budget. The original outcome and evaluation criteria remain fixed across repairs. Candidate attempts retain separate immutable Git branches and receipts. A validated draft PR remains the boundary before owner-approved merge and deployment.

## Acceptance

- A request to analyze current Federal Reserve policy can search actual external information, read originals, and return a Korean answer with original links, observation dates, and facts distinguished from interpretation.
- No-search, inaccessible original, unknown publication date and conflicting evidence are represented honestly. Merely listing a URL does not make it a verified source.
- A maintenance feature request can inspect missing implementation context, add a module and meaningful regression, consume failed-CI diagnostics, and produce a passing candidate without replacing the goal with a design document.
- Native shell/MCP/app execution, credential access, deployment-policy edits, self-approval and automatic paid fallback remain outside the maintenance candidate executor.

The [acceptance record](../project/WEB-ENGINEERING-VALIDATION.md) distinguishes deterministic tests, simulated model/GitHub transport and actual subscription/network execution. Slack dispatch and production rollout are not included in this acceptance. Documentation CI alone is not completion.
