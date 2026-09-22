# Tech Scout activation

2026-09-22. The production `#tech-feeds` sender is now the dedicated **Tech Scout** Slack app, not Reporter.
Reporter remains the hot-news identity. The feed collector, PostgreSQL state, Temporal workflow, freshness,
deduplication and delivery-window policies are unchanged.

## Identity and profile

- The live app is `Tech Scout` (`A0C3112M7N3`, bot user `U0C3JD8E4LR`) and is a delivery-only identity.
- Slack's authenticated response reports exactly one OAuth scope: `chat:write`. Socket Mode and Event
  Subscriptions are disabled, so this bot has no conversational ingress. The packaged inactive role also has
  no tools or delegation targets, and ingress rejects the identity before task or model work can be created.
- [The profile asset](../../slack-apps/avatars/tech_scout.png) uses the same ivory ceramic robot form,
  centered head-and-shoulders composition and material treatment as the existing employee bots. Navy-teal,
  aqua eyes and the radar-compass emblem distinguish the feed role. The 1254×1254 live asset has SHA-256
  `95416a2f2c8693b1e5a3a4ca01bcbacff7e5d2a89e36cfa310dbfbcc52a665c1`.
- Slack UI readback shows Tech Scout as a member of `#tech-feeds` and shows the matching avatar beside its
  delivered article.

## Release and migration

- [PR #52](https://github.com/JJongAchii/quant-ai-company/pull/52) passed CI and was merged as
  `3cd63607d821ecb6489c09135d16fc5d063f9022`.
- The local integrated suite passed **996 tests** with 36 documented skips, using real local PostgreSQL and
  Temporal; Slack HTTP is simulated there. `ruff` also passed. See the [JUnit receipt](evidence/tech-scout-tests.xml).
- Production was quiesced after existing work drained, backed up without secrets, migrated and restarted.
  Eight services are healthy and PostgreSQL was retained. Installed package bytes match the merged source in
  all five image variants. Existing credentials, research profiles and qdata were preserved.
- The migration changes only pending tech-feed ownership. There were no pending rows at cutover; all 28
  historical Reporter deliveries remained Reporter deliveries, with no replay or relabeling.

## Live acceptance

The first naturally collected article after cutover was delivered once by Tech Scout:

- `GeekNews · 기술 동향·커뮤니티 — Transformer를 시각적으로 이해하기`
- DB/outbox ID `b76ecef6-09fe-5696-9630-25b68a76ea87`
- `author=tech_scout`, `agent=tech_scout`, `status=delivered`, `attempts=1`, `error=null`
- Slack timestamp `1790037677.725119`, visually read back with sender **Tech Scout**, matching title/body and
  the new profile image. [Open the live message](https://achiisquantresearch.slack.com/archives/C0C2KPB76KE/p1790037677725119).

At acceptance, all 12 sources were healthy, publication remained enabled and authorized, and the tech-feed
project still had zero tasks and zero turns. No setup message or fabricated article was used for this proof.
The complete secret-free receipt is [tech-scout-production-active.json](evidence/tech-scout-production-active.json).
