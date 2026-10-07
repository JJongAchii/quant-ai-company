# PR113 merge qualification

User request on 2026-10-07: merge PR113 into main.

The source branch integrates main at `64d449ed29cee9197032b101502bdc8094520c66`
without conflicts, preserving the current Codex installer, model assignments,
prompt budgeting, command help and Maintainer changes.

The previous GitHub run `37480516481` failed its `codex-protocol` job because
the source checkout did not yet contain `deploy/install-codex.cjs`, which the
workflow on main required. The main integration supplies the reviewed installer
and its matching protocol checks.

The service job also failed a preview-validation assertion. Its test now reports
the validator's actual reasons, and passes in the focused regression run.
Additional local tests found supplemental-news fixtures mixing a fixed feed clock
with wall-clock publication and PostgreSQL timestamps. These fixtures now use the
feed clock consistently and still commit through the real news publisher. No
trend-feed or news service policy was changed to accommodate the tests.

Focused validation: `12 passed`; lint: `All checks passed!`. PostgreSQL is real;
model, HTTP and Slack boundaries in these regression tests are simulated.
Full service regression and both GitHub jobs remain required before merge.

This merge qualification does not redeploy the production image or resend Slack
messages. The previously qualified ten-topic daily feed remains active.
