# Shorts as the Episode's Summary — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each Shorts carries every briefing issue in order, answers its own hook, and is written, reviewed and inspected like the episode (xhigh write, max review ×2, max visual inspection ×3), without ever being skipped on its checks.

**Architecture:** A new `video/lineup.py` derives the issue lineup (briefing numbered issues → covering episode scenes → core/brief tier). `video/shorts.py` gets a v2 `ShortsPlan` bound to that lineup, new fixed lines, checks and a writer prompt; `video/shorts_review.py` holds the review, visual and screen-fix prompts. `VideoRunner.shorts_step` gains `reviewing` and `inspecting` states. Reviews reuse the existing `video_review_v1` / `video_visual_review_v1` contracts; `claude_runner` routes Shorts requests to the API-first lane by request ID.

**Tech Stack:** Python 3.12, Pydantic v2, PostgreSQL (psycopg), Playwright Chromium + FFmpeg (template render), pytest.

**Spec:** `docs/superpowers/specs/2026-10-27-shorts-episode-summary-design.md` (Korean). Read it first; it is the authority.

## Global Constraints

- **Never edit render-image inputs**: `src/quant_company/contracts.py`, `video/contracts.py`, `video/episode.py`, `video/body.py`, `video/motion.py`, `video/render.py`, `video/upload.py`, `video/photos.py`, `video/illustrations.py`, `video/remote.py`, `video/design/`, `pyproject.toml`, `uv.lock`. Importing from them is fine. Check: `git diff --name-only origin/main | grep -E 'contracts.py|episode.py|body.py|motion.py|render.py|upload.py|photos.py|illustrations.py|remote.py|video/design/|pyproject.toml|uv.lock'` prints nothing (except `video/shorts*`/`shorts_design`, which are not inputs).
- No module that `video/remote.py` imports may import the new modules (`video/lineup.py`, `video/shorts_review.py`).
- No new dependencies.
- Effort is never hard-coded for Shorts: write and fix use `policy['effort']`, review and visual use `policy.get('review_effort', policy.get('effort', 'high'))`.
- A Shorts is never skipped on its checks or reviews: after the last attempt or round, the version with the fewest findings goes on, and the remaining findings go into the Slack delivery text. Existing skip reasons stay (superseded, credit reserve, runway balance, uncertain paid effect, no numbered issues).
- Findings and prompts are English (existing style); everything on screen or spoken is Korean.
- Fixed Korean copy, verbatim:
  - agenda line: `오늘 이슈는 {KO_COUNT[n]} 가지입니다.` with `KO_COUNT = {1: '한', 2: '두', 3: '세', 4: '네', 5: '다섯', 6: '여섯', 7: '일곱'}`
  - closing tail: `자세한 분석은 본편에서, 매일 리포트 메일은 프로필 링크에서 신청하세요.`
  - end-screen button: `본편 보기`; mail row: `구독자 전용 · 매일 증시 리포트 메일 신청은 채널 프로필 링크에서` (unchanged)
  - date line and hook-first opener: unchanged (`opener_line`, `date_line`).
  - The teaser `끝에는 앞으로 볼 신호도 짚어요.` is removed.
- Length limits: hook narration ≤ 32 characters (unchanged). Core narration 60–85 characters, brief narration 25–40, conclusion + signals narration ≤ 70 together, at most 3 core issues. Aim 60–75 s, cap 90 s (unchanged).
- Scene types stay `opener`, `agenda`, `issue`, `outro` (analytics compares scene drops by type). The plan keeps `hook.headline`, `hook.question`, `hook.narration` and `issues[].label` (read by `analytics.shorts_summary`).
- Tests: `uv sync --extra video` once, then `TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55472/company_test uv run --with imageio-ffmpeg pytest <paths> -q`; lint `uv run ruff check .`. Real PostgreSQL, real Chromium/FFmpeg; model, speech, alignment and Slack stay simulated.

## Review Focus

1. A briefing body whose issue section continues into a thread part (the `상세 계속` parts) or has lines before the first numbered heading: the lineup must keep each heading's own lines and ignore everything else. Task 1 adds `test_lines_outside_numbered_issues_belong_to_no_issue`.
2. An issue the episode never covered (no issue-chapter scene cites its lines): the Shorts still carries it, its numbers need only the briefing lines, and its picture falls back to the chapter or cover rule. Task 4 adds `test_an_issue_the_episode_left_out_needs_only_its_briefing_lines`, Task 6 adds `test_an_issue_without_scenes_takes_the_cover`.
3. A cut that was being written, voiced or rendered under the old plan shape when the release swaps the worker: it must not crash a tick; it is written again under v2. Task 7 adds `test_a_plan_of_the_old_shape_is_written_again`.
4. A screen fix that touches spoken text or breaks a check: spoken fields are restored and a fix that adds findings is reverted, so no new speech is paid. Task 8 adds `test_a_screen_fix_never_changes_what_is_said` and `test_a_fix_that_adds_findings_is_reverted`.
5. Six issues with three core ones at their longest: the estimate must stay ≤ 90 s or produce a finding before any speech is paid, and the rendered list and cards must stay inside the safe area. Task 3 adds `test_six_issues_at_their_longest_are_found_before_speech`, Task 6 adds `test_six_issues_render_inside_the_safe_area`.

---

### Task 1: Issue lineup from the briefing and the episode

**Files:**
- Create: `src/quant_company/video/lineup.py`
- Test: `tests/test_video_lineup.py` (it also holds the multi-issue fixtures later tasks import)

**Interfaces:**
- Produces:
  - `class LineupIssue(NamedTuple)`: `number: int`, `title: str`, `claim_ids: tuple[str, ...]`, `scene_ids: tuple[str, ...]`, `tier: str` (`'core'` | `'brief'`)
  - `CORE_MAX = 3`, `ISSUE_CHAPTERS = ('핵심 이슈', '함께 볼 이슈')`
  - `briefing_issues(source: dict) -> list[tuple[int, str, tuple[str, ...]]]`: (number, title without the ` · horizon` tail, claim IDs of the heading line and every line under it up to the next heading or the end of the `주요 이슈` section), in body order.
  - `lineup(source: dict, episode: dict) -> list[LineupIssue]`; `episode` is the EpisodePlan as JSON.
  - `uncovered(issues: list[LineupIssue]) -> list[LineupIssue]`: those with no `scene_ids`.
  - Test fixtures in `tests/test_video_lineup.py`: `issue_body(titles: list[str], lines_per_issue: int = 4) -> dict` (a source dict like `tests.test_video.body_source()`, whose `body` is rendered-style Slack text with `*주요 이슈*` and `*N. title* · 수일~수주` headings; each issue's lines carry numbers such as `코스피 1.46% 하락` so number checks have something to bind), `issue_episode(source: dict, chapters: dict[int, str], skip: tuple[int, ...] = ()) -> dict` (an EpisodePlan JSON with a cold open, a summary, one or two issue scenes per issue in the given chapter citing that issue's lines, a calendar and a signals scene; `skip` leaves those issues out).

Rules the tests pin:
- A heading is a raw body line matching `^\*(\d+)\. ([^*]+)\*(?: · (.*))?$` inside the `주요 이슈` section. Map it to the parsed claim whose text equals what `body.parse` produces for it (`plain(title + ' · ' + horizon)`). Do not change `body.py`.
- A scene covers issue k when its chapter is in `ISSUE_CHAPTERS` and it cites any of issue k's claim IDs (the heading line included). A scene citing two issues covers both.
- Tier: walking the issues in order, an issue is `core` when its first covering scene is in `핵심 이슈` and fewer than `CORE_MAX` issues are core so far; otherwise `brief`. An uncovered issue is `brief`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_lineup_is_the_briefings_numbered_issues_in_order():
    source = issue_body(['장 초반 반등 실패, 코스닥 낙폭 확대', '유가 조정에도 공급 위험', 'AI 이익과 투자재원',
                         '중소기업 수출 최대', 'LIG 급락·루닛 상한가', '청년 채용 부진'])
    issues = briefing_issues(source)
    assert [n for n, _, _ in issues] == [1, 2, 3, 4, 5, 6]
    assert issues[0][1] == '장 초반 반등 실패, 코스닥 낙폭 확대'
    assert all(len(ids) == 5 for _, _, ids in issues)  # the heading line and its four lines

def test_scenes_and_tiers_follow_the_episode_chapters():
    source = issue_body(['a 이슈', 'b 이슈', 'c 이슈', 'd 이슈'])
    episode = issue_episode(source, {1: '핵심 이슈', 2: '핵심 이슈', 3: '함께 볼 이슈', 4: '함께 볼 이슈'})
    found = lineup(source, episode)
    assert [i.tier for i in found] == ['core', 'core', 'brief', 'brief']
    assert all(i.scene_ids for i in found)

def test_no_more_than_three_core_issues():
    source = issue_body([f'{c} 이슈' for c in 'abcde'])
    episode = issue_episode(source, {n: '핵심 이슈' for n in range(1, 6)})
    assert [i.tier for i in lineup(source, episode)] == ['core', 'core', 'core', 'brief', 'brief']

def test_an_uncovered_issue_is_reported_and_brief():
    source = issue_body(['a 이슈', 'b 이슈', 'c 이슈'])
    episode = issue_episode(source, {1: '핵심 이슈', 2: '함께 볼 이슈', 3: '함께 볼 이슈'}, skip=(2,))
    found = lineup(source, episode)
    assert [i.number for i in uncovered(found)] == [2] and found[1].tier == 'brief' and found[1].scene_ids == ()

def test_only_issue_chapter_scenes_cover_an_issue():
    # The fixture's cold open and closing cite issue 1 lines; only its issue scenes count.
    ...

def test_lines_outside_numbered_issues_belong_to_no_issue():
    # Lines in 오늘의 핵심 and 다음 확인할 것, and a 상세 계속 part after the issues, are in no issue's claim_ids.
    ...

def test_the_fixture_brief_has_one_issue():
    from .test_video import body_source
    from .test_video_motion import episode
    found = lineup(body_source(), episode().model_dump(mode='json'))
    assert [(i.number, i.tier) for i in found] == [(1, 'core')] and found[0].scene_ids == ('head', 'flow', 'photo', 'counter')
```

Fill in the two `...` tests with assertions that match their comments.

- [ ] **Step 2: Run the tests, confirm they fail** (`ModuleNotFoundError: quant_company.video.lineup`)

Run: `TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55472/company_test uv run pytest tests/test_video_lineup.py -q`

- [ ] **Step 3: Implement `video/lineup.py`** with the interfaces above. Module docstring: what the lineup is and the owner's rule (2026-10-26: "본편에서의 이슈와 개수가 … 같아야지").

- [ ] **Step 4: Run the tests, confirm they pass**

- [ ] **Step 5: Commit** — `feat(video): derive the Shorts issue lineup from the briefing and the episode`

---

### Task 2: The episode script covers every briefing issue

**Files:**
- Modify: `src/quant_company/video/runner.py` (`write_plan`, after `plan_findings(...)`)
- Test: `tests/test_video_lineup.py`

**Interfaces:**
- Consumes: `lineup`, `uncovered` (Task 1).
- Produces: `coverage_findings(source: dict, plan_json: dict) -> list[str]` in `video/lineup.py`, one finding per uncovered issue: `f"Briefing issue {n} ({title}) has no scene in 핵심 이슈 or 함께 볼 이슈: give it at least one issue scene"`. Empty for an edition with `source.get('format') == 'facts'` or with no numbered issues.

`write_plan` adds these findings only for the `motion-v2` template, so an uncovered issue makes the attempt `check_failed` and the next attempt is asked to cover it. The prompt itself stays byte-identical. After the last attempt the fewest-findings version goes on, as today.

- [ ] **Step 1: Write failing tests**
  - `test_an_episode_that_skips_a_briefing_issue_is_asked_to_cover_it`: drives `write_plan` the way `tests/test_video_motion.py` does. The provider returns an episode that leaves issue 2 uncovered and then one that covers it. Assert:
    - the first attempt is `check_failed` with the finding text above
    - the second request's prompt carries that finding in its revision feedback
    - the job reaches `reviewing`
  - `test_a_facts_edition_has_no_coverage_finding`.
- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** `tests/test_video_lineup.py tests/test_video_motion.py -q`. All pass: the existing fixture brief has one issue, covered by the fixture episode.
- [ ] **Step 5: Commit** — `feat(video): ask the episode script again when it leaves a briefing issue out`

---

### Task 3: ShortsPlan v2, fixed lines and length estimate

**Files:**
- Modify: `src/quant_company/video/shorts.py` (schema, constants, `agenda_line`, `outro_line`, `estimated_seconds`, `settle`, `ShortsRenderer.scenes` only as far as the new fields need; the template comes in Task 6)
- Test: `tests/test_video_shorts.py` (unit part: rewrite the `shorts()` fixture and the schema/fixed-line tests)

**Interfaces:**
- Produces (in `shorts.py`):
  - `class ShortsIssue(StrictModel)`. Fields:
    - `id` (pattern unchanged)
    - `issue: int` (1–7, the lineup number)
    - `label` (2–12)
    - `agenda` (2–26)
    - `big: str = ''` (≤12)
    - `big_label: str = ''` (≤12; required when `big` is set)
    - `dir` (unchanged)
    - `body` (10–70)
    - `meaning: str = ''` (≤40)
    - `narration` (15–110)
    - `claim_ids` (1–8)
    - No `scene_ids`, `note_kind` or `note`.
    - `screen()` returns the shown strings.
  - `class ShortsClose(StrictModel)`. Fields:
    - `conclusion` (8–40)
    - `signals: list[str]` (2–3, each 4–30)
    - `signals_narration` (10–60)
    - `claim_ids` (1–8)
  - `class ShortsPlan(StrictModel)`:
    - `title`, `description`, `hook: ShortsHook` (unchanged)
    - `issues: list[ShortsIssue]` (1–7)
    - `close: ShortsClose`
    - The `shape()` validator keeps unique IDs, no fixed-scene IDs, no delivery tags, signs as words and no series name in the title. The total spoken characters stay ≤ 700 (schema guard only).
    - `narrations()` returns `[hook.narration, *issue narrations, close.conclusion, close.signals_narration]`.
  - Constants:
    - `KO_COUNT` (Global Constraints), `CHECKS = 5`
    - `CORE_NARRATION = (60, 85)`, `BRIEF_NARRATION = (25, 40)`, `CLOSE_NARRATION = 70`
    - `OUTRO = {'say': '자세한 분석은 본편에서, 매일 리포트 메일은 프로필 링크에서 신청하세요.', 'button': '본편 보기', 'mail_line': '구독자 전용 · 매일 증시 리포트 메일 신청은 채널 프로필 링크에서'}`
    - `TEASER` removed.
  - `agenda_line(count: int) -> str`; `agenda_hold(count) -> 2.0 + 0.4 * count`.
  - `outro_line(close: ShortsClose) -> str` = `f"{sentence(close.conclusion)} {sentence(close.signals_narration)} {OUTRO['say']}"`.
  - `estimated_seconds(plan: ShortsPlan) -> float`: same layout rule as today with the new lines.
  - `settle(plan, source)`: unchanged behaviour for `dir`; an issue with `big == ''` keeps no arrow.

- [ ] **Step 1: Rewrite the unit fixture and tests (failing)**
  - The `shorts()` fixture becomes a v2 plan of the one-issue fixture brief:
    - one issue: `issue=1`, `id='semis'`, `label='반도체 주도'`, a 60–85 character narration, `meaning='수익률 하락은 할인 부담을 낮출 수 있음'`
    - `close.conclusion='정리하면, 반도체가 이끌었지만 확산은 아직입니다.'`
  - Tests:
    - `test_a_cut_is_a_hook_one_to_seven_issues_and_a_close` (0 and 8 issues fail validation)
    - `test_fixed_lines` (`agenda_line(6) == '오늘 이슈는 여섯 가지입니다.'`, `outro_line` ends with `OUTRO['say']`, no `TEASER` symbol)
    - `test_six_issues_at_their_longest_are_found_before_speech`: 3 core at 85 + 3 brief at 40 + a 70-character close ⇒ `estimated_seconds` between 85 and 90. Adding a 7th brief issue ⇒ over 90.
    - `test_four_issues_at_their_middle_run_about_a_minute` (2 core at 72, 2 brief at 33, close 60 ⇒ 55–66 s)
  - Delete tests of removed behaviour (`label_times` list reveal, `TEASER`, the comma-in-label rule).
- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** `tests/test_video_shorts.py -q -k "not tick and not delivered"`. Pass, except tests owned by Tasks 4–8: mark them `pytest.mark.skip(reason='Task N')` and list them in the report.
- [ ] **Step 5: Commit** — `feat(video): Shorts plan v2 with issues bound to the lineup and an answering close`

---

### Task 4: Shorts checks v2

**Files:**
- Modify: `src/quant_company/video/shorts.py` (`shorts_violations`, `issue_citations`/`claim_binding` replaced, `episode_text` kept)
- Test: `tests/test_video_shorts.py`

**Interfaces:**
- Consumes: `LineupIssue`, `lineup` (Task 1); v2 schema (Task 3).
- Produces: `shorts_violations(plan: ShortsPlan, source: dict, episode: dict, issues: list[LineupIssue]) -> list[str]`. Callers compute `issues = lineup(source, episode)` once.

It keeps every existing rule, applied per unit (hook, each issue, the close):
- numbers bound to the cited lines and present in the delivered episode
- direction
- `종가` / `마감` labels
- tool wording, buy/sell wording, spoken attribution
- title/description numbers
- hook ≤ 32 characters, estimate ≤ 90 s

New findings (exact wording up to the implementer, English):
- `len(plan.issues) != len(issues)` → name the missing or extra issue numbers.
- `plan.issues[k].issue != issues[k].number` → out of order.
- An issue cites a claim outside `issues[k].claim_ids` ∪ the claims of `issues[k].scene_ids`.
- Narration length outside `CORE_NARRATION` for a core issue or `BRIEF_NARRATION` for a brief one.
- A core issue with an empty `meaning`.
- `len(close.conclusion) + len(close.signals_narration) > CLOSE_NARRATION`.
- Hook and close claims must be used somewhere in the episode or belong to some lineup issue.

An uncovered issue's numbers need only its briefing lines (not the episode text).

- [ ] **Step 1: Failing tests**, using `issue_body` / `issue_episode` from Task 1 and a helper that builds a passing six-issue plan:
  - `test_every_briefing_issue_once_in_order` (split, missing, swapped and extra issue each produce a finding)
  - `test_an_issue_cites_only_its_own_lines`
  - `test_core_and_brief_narration_lengths`
  - `test_a_core_issue_says_what_it_means`
  - `test_the_close_fits_its_budget`
  - `test_an_issue_the_episode_left_out_needs_only_its_briefing_lines`
  - Keep the parametrized binding-rules test, rewritten for v2.
- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** the unit tests
- [ ] **Step 5: Commit** — `feat(video): check that a Shorts carries the briefing's issues in order`

---

### Task 5: Writer, review, visual and fix prompts

**Files:**
- Modify: `src/quant_company/video/shorts.py` (`shorts_prompt`)
- Create: `src/quant_company/video/shorts_review.py`
- Test: `tests/test_video_shorts.py` (prompt tests), `tests/test_video_shorts_review.py`

**Interfaces:**
- `shorts_prompt(source, episode, issues: list[LineupIssue], concerns=(), feedback='', lessons=(), outlook=False, previous: dict | None = None) -> str`
- In `shorts_review.py`:
  - `review_prompt(source, episode, issues, plan: ShortsPlan, concerns: list[str], seconds: float) -> str`, answered as `VideoReview`
  - `visual_prompt(source, plan, frames: list[tuple[str, str]], heard: dict[str, str]) -> str`, answered as `VisualReview`
  - `fix_prompt(source, episode, issues, plan, screen: list[dict]) -> str`, answered as `ShortsPlan`
  - `keep_spoken(original: ShortsPlan, fixed: ShortsPlan) -> ShortsPlan` returns `fixed` with every spoken field (`hook.narration`, each issue `narration`, `close.conclusion`, `close.signals_narration`) and every issue's `issue`/`id`/`claim_ids` restored from `original`.

The writer prompt must state each of these (spec "목표", "구성", "이슈 목록 맞추기"):
1. The Shorts is the short, clear summary of this episode. A viewer who watches only the Shorts understands the day: what happened, why, what it means, what to watch.
2. The lineup as JSON, one entry per issue (number, title, tier, its briefing lines, and the headline and narration of the episode scenes that covered it). Write exactly one issue per entry, same order, never split, merged or replaced.
3. Hook: the tension of the episode's title/thumbnail, as a contrasting fact where possible (`외국인은 샀는데, 코스피는 급락`); no plain index move without tension; one breath ≤ 32 characters; no greeting. If it is a question, the Shorts itself answers it.
4. Core issue: what happened → why → so what (narration 60–85 characters); `body` is what happened; `meaning` is the 그래서 line from the briefing's interpretation or mechanism. A counterpoint only as a concrete alternative from a `다르게 볼 점` line, never a bare hedge.
5. Brief issue: one sentence with its number and what it means (narration 25–40 characters).
6. No line whose only content is a hedge (`근거는 부족`, `미확정`, `확인되지 않았다`). Keep the briefing's strength: interpretation stays interpretation, no certainty, no new prediction.
7. `label` ≤ 12 characters names the issue in the list; `agenda` ≤ 26 characters is its one-line takeaway (keep the existing guidance and example).
8. Close: `conclusion` starts with `정리하면` and answers the hook in one sentence. Then the signals and, when `outlook`, the conditional outlook (`OUTLOOK_SHORTS` wording kept). The two together are ≤ 70 characters.
9. Speak at most one or two numbers per issue. Explain jargon in plain words.
10. Unchanged rules: fixed lines the service adds (date line after the hook; the count line; the closing tail `OUTRO['say']`), numbers bound to cited lines and to the delivered episode (except an issue the episode did not cover), never `REPORT_LABEL` or an outlet aloud, no buy/sell advice, no AI/tool mentions, title rules, lessons rule, flagged concerns kept out.
11. With `previous`: revise that version; keep what already follows the rules (the episode's `REVISION_GUIDE` idea; do not import it if that pulls render inputs you would change: wording your own is fine).

The review prompt asks for the five `VideoReview` booleans with these meanings, and Korean concerns ≤ 12, each with a concrete fix:
- facts: nothing beyond the briefing and the episode, and no error the episode review flagged (concerns are given)
- numbers: numbers, units, directions, 종가/마감 labels
- conditions: the briefing's strength kept, no hedge-only line
- coverage: the same issues in order, each issue's main interpretation present, the hook answered inside the Shorts, a conclusion line
- readability: a phone viewer understands each issue on first hearing (jargon explained, one idea per line, at most two spoken numbers per issue, short screen text)

It must not ask to soften a hook or strong words unless they overstate the lines (the owner's 2026-10-15 rule, as in `video.contracts.review_prompt`).

The visual prompt mirrors `video.contracts.visual_review_prompt` for a vertical Shorts:
- the frame order is given
- it checks clipped or overlapping text, text inside YouTube's control areas (top ~220 px, bottom ~380 px, right ~140 px), unreadable contrast or size, pictures unrelated to the issue, numbers on screen that differ from the narration or the briefing, and broken or empty layouts
- it judges each speech-to-text finding as `audio_text` or not
- it includes `OWNER_DESIGN_RULES`
- detail and fix are written in Korean

The fix prompt asks for the whole plan back with only screen fields changed (`headline`, `question`, `label`, `agenda`, `big`, `big_label`, `dir`, `body`, `meaning`, `signals`, `title`, `description`) to resolve the listed issues. Every spoken field stays identical.

- [ ] **Step 1: Failing tests**:
  - `test_the_prompt_lists_the_lineup_and_asks_one_issue_each`: prompt contains each lineup title and number and the tier words
  - `test_the_prompt_revises_the_previous_version`
  - `test_the_prompt_carries_only_given_lessons` (kept)
  - `test_review_prompt_names_the_five_checks_and_the_episode_concerns`
  - `test_keep_spoken_restores_every_spoken_field`
  - `test_fix_prompt_lists_the_screen_issues`
- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run, confirm pass**
- [ ] **Step 5: Commit** — `feat(video): Shorts writer, review, visual and fix prompts`

---

### Task 6: Renderer and template v2

**Files:**
- Modify: `src/quant_company/video/shorts.py` (`cut_pictures`, `ShortsRenderer.scenes`, `_pictures`, `build`)
- Modify: `src/quant_company/video/shorts_design/shorts.js`, `src/quant_company/video/shorts_design/index.html`
- Test: `tests/test_video_shorts.py` (render tests)

**Interfaces:**
- Consumes: `lineup` (Task 1), v2 schema (Task 3).
- Produces:
  - `cut_pictures(job, episode, plan, settings, library, issues: list[LineupIssue]) -> dict`: same keys. An issue's own picture is the first picture of its lineup `scene_ids`. `nearby` is the other scenes of those chapters. An uncovered issue gets no own picture and no nearby list, so it falls to the cover.
  - `ShortsRenderer.render(job, plan, episode, directory, audio_paths, stop=None)` signature is unchanged. It computes `lineup(job['source'], episode)` itself.
  - The manifest gains `review['audio']: dict[str, str]`, scene id → that scene's aligner findings joined, only for scenes with findings. `review['frames']` is unchanged.

Scenes:
- **agenda:** `data` = `{'kicker': f'오늘 볼 이슈 {n}가지', 'rows': [{'label', 'agenda'}]}` with no `at`. The template's existing fallback staggers the rows; change it to `0.15 + 0.3 * i` seconds.
- **issue:** `data` adds `tier` and `meaning`. Drop `note_kind`/`note`.
  - Core card: number row (if `big`), body card and a `그래서 · {meaning}` tag (class `k1`).
  - Brief card: number row (if `big`) and the body as one line, no tag, slightly smaller card (`.card.brief`).
  - The pill shows `k/N` as today.
- **outro:** `data` = `{'kicker': '정리', 'conclusion', 'signals', 'button': OUTRO['button'], 'mail': OUTRO['mail_line']}`.
  - The conclusion is drawn large first (class `oq`), then the signals, then the play button and the mail row.
  - The button comes in when `본편` is said, as today.
- The question bar (`#qbar`, `오늘의 질문`) keeps showing over the agenda and the issues.

- [ ] **Step 1: Failing tests** (real Chromium; `render_fixture` reworked to v2):
  - `test_six_issues_render_inside_the_safe_area`: six-issue plan (3 core at their longest, 3 brief) on `issue_body`/`issue_episode`; `QA_JS` finds nothing in any scene; the agenda shows 6 rows
  - `test_a_core_card_shows_what_it_means_and_a_brief_card_does_not`
  - `test_an_issue_without_scenes_takes_the_cover`
  - `test_the_end_screen_answers_with_the_conclusion_and_points_to_the_episode`: replaces `…leaves_it_for_the_episode`; text `본편 보기` present, the conclusion present
  - `test_heard_findings_are_kept_per_scene`
  - Keep and adapt the existing safe-area, picture, headline-break and music tests.
- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** `uv run --with imageio-ffmpeg pytest tests/test_video_shorts.py -q -k "render or picture or scene or music or headline or safe"`
- [ ] **Step 5: Commit** — `feat(video): Shorts template for the full lineup, core and brief cards and an answering end screen`

---

### Task 7: Runner — writing as revision, and the review state

**Files:**
- Modify: `src/quant_company/video/runner.py` (`shorts_step` split into `shorts_write`, `shorts_review`, `shorts_synthesize`, `shorts_render`; `shorts_fallback`; `shorts_takes`)
- Modify: `src/quant_company/video/shorts_store.py` (`ACTIVE`)
- Modify: `deploy/release.py` (Shorts busy states)
- Test: `tests/test_video_shorts.py` (flow part), `tests/test_release*.py` if one pins the SQL

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces:
  - `ShortsStore.ACTIVE = ('writing', 'reviewing', 'synthesizing', 'rendering', 'inspecting')`
  - `SHORTS_REVIEW_ROUNDS = 2` and `SHORTS_REVIEW_RUNTIME_RETRIES = 4` in `runner.py`
  - qa keys: `review_rounds` (`[{'key', 'passed', 'concerns', 'outcome'?}]`), `review_concerns` (open concerns fed to the next write), `review_findings` (left after the last round)

Flow:
- **writing:**
  - The issue skip becomes "no numbered issues in the briefing" (reason `too_few_issues`, text `쇼츠는 브리핑에 번호 붙은 이슈가 없어 만들지 않았습니다.`).
  - Each turn uses key `shorts-{n}` with effort from the policy (Global Constraints).
  - The prompt carries `previous` (the last written version) and feedback = last findings + `review_concerns`.
  - Passing the checks moves to `reviewing` with the plan.
  - After `SHORTS_ATTEMPTS` written versions, the fewest-findings version moves to `reviewing` (`shipped_with_findings` as today).
  - Recheck and lost-turn handling stay as today.
- **reviewing:**
  - Key `shorts-review-{r}`, contract `video_review_v1`, schema `VideoReview`, review effort.
  - Concerns = `review.concerns` + `f'{name} check failed'` for each false boolean (as `review_plan`).
  - Concerns, with fewer than `SHORTS_REVIEW_ROUNDS` reviewed rounds and fewer than `SHORTS_ATTEMPTS` written versions: store `review_concerns` and go back to `writing`.
  - Otherwise: store `review_findings` and move to `synthesizing`.
  - A lost review turn (uncertain or timeout) reviews the same plan again, up to `SHORTS_REVIEW_RUNTIME_RETRIES`, with the episode's pacing.
  - A review contract failure becomes one concern and production continues.
- **synthesizing / rendering:**
  - As today, with v2 plans.
  - A stored plan that does not validate as v2 is saved back to `writing` with `qa['older_shape'] = True` and attempts kept.
  - Rendering ends in `inspecting` (Task 8). Until Task 8 lands, keep delivering from `rendering` so this task's tests run.
- **release.py:** Shorts busy states become `('writing','reviewing','synthesizing','rendering','inspecting')`.

- [ ] **Step 1: Failing tests** (real PostgreSQL; `delivered_episode` with an `issue_body` brief of four issues where needed, provider queues for `video_shorts_v1` and Shorts reviews distinguished by request ID: extend `InspectingProvider` with an optional `by_id` prefix map rather than changing its contract keys):
  - `test_a_delivered_episode_gets_its_shorts_in_the_same_thread`: adapted. Takes count = 2 + issues + 1; the last take ends with `OUTRO['say']`; the request IDs are `shorts-1`, then `shorts-review-1`, both with the policy efforts.
  - `test_review_concerns_are_written_into_the_next_version`
  - `test_concerns_left_after_two_reviews_travel_with_the_cut`
  - `test_a_lost_review_turn_reviews_the_same_plan_again`
  - `test_a_plan_of_the_old_shape_is_written_again`
  - `test_a_briefing_without_numbered_issues_spends_no_turn`: replaces the under-two-issue-scenes test
  - Supersede, credit-reserve, quiet-window and lost-turn tests: adapted, still passing.
- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** `tests/test_video_shorts.py tests/test_video_motion.py -q`, plus the release tests if touched
- [ ] **Step 5: Commit** — `feat(video): Shorts are revised from their last version and reviewed like the episode`

---

### Task 8: Runner — inspecting, screen fix, retake and delivery

**Files:**
- Modify: `src/quant_company/video/runner.py` (`shorts_inspect`, `shorts_takes(job, plan, retake=())`)
- Modify: `src/quant_company/video/shorts_store.py` (`deliver` accepts `inspecting`; `delivery_text`)
- Modify: `docs/daily-video.md` (the Shorts section: lineup, review, inspection, end screen)
- Test: `tests/test_video_shorts.py`, `tests/test_video_delivery.py` if it pins the text

**Interfaces:**
- Consumes: Tasks 5–7.
- Produces:
  - `INSPECT_ROUNDS` is reused (3).
  - qa keys: `inspections` (`[{'key', 'issues', 'heard', 'findings', 'fix_reverted'?, 'fix_error'?}]`), `retake` (scene IDs), `inspection_findings`.
  - `delivery_text(job, artifacts, script_findings=(), review_findings=(), inspection_findings=())`. It adds `검토: …` and `화면: …` lines; the count includes them; the `이슈 N개` count is unchanged.

Flow:
- **inspecting:**
  - Key `shorts-visual-{v}`, contract `video_visual_review_v1`, review effort. Attachments are the manifest's `review.frames` (JPEG); `heard` is `review.audio`.
  - Retake = scenes with an `audio_text` issue, or every heard scene when the visual turn is unavailable.
  - Screen issues are those with `fix != 'none'` and kind other than `audio_text`. They get a fix turn: key `shorts-fix-{v}`, contract `video_shorts_v1`, write effort, then `keep_spoken`, then `settle`, then `shorts_violations`.
  - A fix with more findings than the inspected plan is reverted (`fix_reverted`).
  - If the plan changed or there is a retake, and fewer than `INSPECT_ROUNDS` inspections were done: save the plan and go to `synthesizing` (retake) or `rendering` (screen only).
  - Otherwise: store `inspection_findings` and deliver.
- **shorts_takes:** a scene in `retake` is voiced with `korean_numbers(...)` instead of `spoken(...)`. Scene IDs are `opener`, `agenda`, issue IDs and `outro`.

- [ ] **Step 1: Failing tests:**
  - `test_a_cut_is_inspected_before_it_is_delivered` (state passes `inspecting`; the visual request carries the frames)
  - `test_a_screen_fix_never_changes_what_is_said` (no new speech effects after a screen-only fix)
  - `test_a_fix_that_adds_findings_is_reverted`
  - `test_a_misread_scene_is_voiced_again_in_korean_numbers`
  - `test_inspection_findings_after_the_last_round_travel_with_the_cut`
  - `test_the_delivery_text_lists_review_and_screen_findings`
- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement, update `docs/daily-video.md`**
- [ ] **Step 4: Run** `uv run --with imageio-ffmpeg pytest tests/test_video_shorts.py tests/test_video_delivery.py tests/test_video_motion.py -q`
- [ ] **Step 5: Commit** — `feat(video): inspect each Shorts like the episode and deliver what is left`

---

### Task 9: API-first lane for every Shorts turn

**Files:**
- Modify: `src/quant_company/providers/claude_runner.py`
- Test: `tests/test_claude_api_lane.py`

**Interfaces:**
- Produces:
  - `SHORTS_REQUEST = re.compile(r'^video-[0-9a-f-]{36}-shorts-')`
  - `SHORTS_SHARED = frozenset({'video_review_v1', 'video_visual_review_v1'})`
  - `api_first(request) -> bool`: true for `output_contract in API_FIRST`, or for a `SHORTS_SHARED` contract whose request ID matches `SHORTS_REQUEST`.
  - Both places that test `request.output_contract in API_FIRST` (`_api_instead`, `_route`) use `api_first(request)`. `API_FIRST` itself is unchanged, so its pinned test still holds.

- [ ] **Step 1: Failing tests**:
  - `test_a_shorts_review_runs_on_the_api_first`
  - `test_an_episode_review_stays_on_the_subscription_first` (same contract, request ID `video-<uuid>-review-1`)
  - `test_a_shorts_visual_review_runs_on_the_api_first_with_its_frames`
  - Follow `test_api_first_contract_runs_on_the_api_and_receipt_says_so`.
- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** `tests/test_claude_api_lane.py -q`
- [ ] **Step 5: Commit** — `feat(claude): Shorts reviews and inspections take the API credit lane first`

---

### Final verification (controller)

- Full suite: `TEST_DATABASE_URL=… uv run --with imageio-ffmpeg pytest -q` and `uv run ruff check .`. Report passed/failed/skipped counts.
- The render-input check from Global Constraints prints nothing.
- Preview for the owner (not committed, nothing written to production):
  - Read the 10/26 close and 10/27 morning jobs (source, plan, policy) from production, read-only.
  - Run writer → checks → review → revision through a local `ClaudeRunner` on the subscription login.
  - Render with silent takes of the estimated length, a word-spread aligner and `ShortsRenderer`.
  - Show the owner the scripts and the frame contact sheets.
