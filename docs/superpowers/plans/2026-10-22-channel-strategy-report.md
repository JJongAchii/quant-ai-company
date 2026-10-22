# Channel Strategy Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Monday weekly review with a Monday/Thursday 09:00 KST channel strategy report over every measured episode and Shorts. Post per-video cards only for outliers. Add a Monday benchmark of fast-growing public videos. Route adopted `shorts` lessons into the Shorts script only. Ask a malformed model output once more (`-r2`); if it still fails, post a failure note with the numbers instead of nothing.

**Architecture:**
- The report reuses the weekly flow end to end:
  - `video_analyses.kind='weekly'`, with `week` holding the report day
  - request ID `video-analytics-weekly-<day>`, plus `-r2` for the retry
  - post key `weekly:<day>`
  - lesson buttons through `analytics_slack.blocks`
  - owner questions in its thread
- The service computes all the numbers and freezes them in `request.extra.stats`:
  - period figures for episodes and Shorts
  - traffic shares
  - search terms
  - Shorts→episode pairs
  - feature comparisons
- The model writes a typed `StrategyReport`, served under the unchanged contract name `video_weekly_v1`. `contracts.py` is a render input and lists the contract names, so the name cannot change.
- Shorts uploads are linked to their cut by title (`edition_kind='shorts'`) and measured like episodes.
- Benchmark candidates come from the Data API using the existing OAuth token (GET only), and are frozen with the first ask.

**Tech Stack:** Python 3.12, pydantic, httpx, PostgreSQL (psycopg), Temporal (workflow unchanged), claude-runtime structured output. Tests run with `uv run pytest` against real PostgreSQL (`TEST_DATABASE_URL`). YouTube, claude-runtime and Slack are simulated fixtures.

**Spec:** `docs/superpowers/specs/2026-10-22-channel-strategy-report-design.md`

## Global Constraints

**Render inputs**
- Never touch these render-image inputs:
  - `src/quant_company/contracts.py`
  - `src/quant_company/briefing/contracts.py`, `briefing/execution.py`, `web_fetch.py`
  - `video/contracts.py`, `video/episode.py`, `video/motion.py`, `video/remote.py`, `video/render.py`, `video/upload.py`, `video/photos.py`, `video/illustrations.py`, `video/body.py`, `video/design/`
- These are not render inputs: `runner.py`, `shorts.py`, `analytics*.py`, `schema.sql`, `providers/claude_runner.py`.
- Before every commit, this must print `[]`:
  `uv run python -c "import subprocess,sys; from pathlib import Path; sys.path.insert(0,'deploy'); import release; p=subprocess.run(['git','diff','--name-only','origin/main...HEAD'],capture_output=True,text=True).stdout.split(); print(release.render_changes(p,[Path('.')]))"`

**Contract name**
- The contract name stays `video_weekly_v1`. Any new `output_contract` value would require editing `contracts.py`.
- `claude_runner.output_schema` maps `video_weekly_v1` to `StrategyReport`.

**Request identity**
- Every model request is built once and frozen with its `video_analyses` row (`AnalyticsRunner.analyze`/`frozen`). A re-ask sends the identical request.
- Request IDs:
  - `video-analytics-weekly-<YYYY-MM-DD>`
  - `video-analytics-weekly-<YYYY-MM-DD>-r2`, only after an `invalid_output` receipt
- The r2 request is the frozen r1 request with a retry note appended to its prompt. Model, effort and attachments are identical.
- Benchmark candidates are fetched only while no analysis row exists for the r1 ID.
- A job created before this change keeps identical episode and Shorts prompts. With no lesson of that kind, the prompt text is byte-identical.

**Schema**
- Migrations go only in `src/quant_company/video/schema.sql`.
- Use the existing `DROP CONSTRAINT IF EXISTS` / `ADD CONSTRAINT` pattern (v5).
- Every change only widens a CHECK.

**Exact values** (verbatim from the spec)
- Schedule: Monday and Thursday at 09:00 KST, caught up within 48 hours.
  - `STRATEGY_WEEKDAYS = (0, 3)`, `STRATEGY_HOUR = 9`, `STRATEGY_CATCH_UP = timedelta(hours=48)`
- Outlier: compared with the median of the same kind (am, close, shorts) over the previous 28 days of d3 snapshots.
  - Views ≥ 2× or ≤ 0.5×
  - CTR ≥ 1.5× or ≤ 0.67× (`2 / 3`)
  - 30-second hold ≥ 1.3× or ≤ 0.77×
  - Fewer than 3 same-kind episodes → no card (`OUTLIER_MIN = 3`).
  - Non-outliers get no diagnosis and no 7-day update.
- Report items are at most 500 characters (`ITEM = 500`), and the prompt states the limits.
- Slack body is at most 4,000 characters.
  - The existing analytics post cap is 2,900 (`AnalyticsStore.post` uses `text[:2900]`; `gate` allows ≤ 3,000; one Slack section block allows ≤ 3,000).
  - So the root is held to `REPORT_LIMIT = 2900`, which is within 4,000.
  - The overflow (viewers, traffic, search, Shorts→episode, benchmark) goes to the first thread reply.
- Benchmark (Monday only): the top 5 picked from these reads:
  - `videos.list chart=mostPopular regionCode=KR maxResults=50` for all categories plus `22`, `27`, `28`
  - then `channels.list`
  - then `commentThreads.list` (5 comments, ordered by relevance)
- Quota is at most about 20 units per report; this design uses at most 13. Never call `search.list`. Use read-only GETs with the existing token.
- Lesson scope `shorts` goes into the Shorts script prompt only, never the episode prompt.

**Honesty**
- FUNNEL/HONESTY stay.
- The prompt names small samples and forbids reading correlation as cause.

**Tests and commits**
- Tests: `export TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55471/company_test_strategy`. Lint: `uv run ruff check .`.
- Every commit message ends with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Do not push or open a PR.

## Review Focus
1. **Request identity.**
   - r2 is derived from the frozen r1 request and the stored r1 receipt detail. A pending r2 re-ask reads both back and sends the same bytes.
   - The Monday benchmark is never refetched for an existing r1 row.
   - Tests:
     - Task 5: `test_an_invalid_report_is_asked_once_more_with_the_contract_errors`
     - Task 6: `test_thursday_reports_and_re_asks_fetch_no_benchmark`
2. **Lesson routing.**
   - A `shorts` lesson never reaches `episode_prompt`, and only `shorts` lessons reach `shorts_prompt`.
   - Prompts with no such lessons are unchanged.
   - Tests in Task 1.
3. **Shorts linked to the episode's `job_id`.**
   - Shorts scene drops use the Shorts manifest, not the episode's scenes.
   - Report rows read the Shorts plan.
   - No query assumes one publication per job.
   - Tests in Task 2.
4. **The schedule.**
   - The 48h catch-up never overlaps two reports.
   - Quiet windows are Wed 09:00–Thu 09:00 KST and Sat 09:00–Mon 09:00 KST, and the test constants move into them (`AFTER_D3`, the old-uploads test).
   - Task 4 test, plus the Task 5 fixture changes.
5. **Slack length.**
   - The root stays at most 2,900 characters, with the strategies and the buttons.
   - The detail goes out once, after the root's receipt, and only when the root does not already hold it.
   - Task 5: `test_a_long_report_moves_details_to_the_first_thread_reply`.
6. **The contract swap.**
   - The runtime and the service swap `video_weekly_v1` → `StrategyReport` in the same commit (Task 5).
   - During a release, an old service against a new runtime gives `invalid_output`, then the r2 retry, and at worst the failure post.

---

### Task 1: The `shorts` lesson scope, routed to the Shorts script only

**Files:**
- Modify: `src/quant_company/video/schema.sql` (append the v6 block)
- Modify: `src/quant_company/video/analytics_contracts.py` (`LessonScope`, the `answer_prompt` scope list)
- Modify: `src/quant_company/video/analytics_slack.py` (`SCOPES`)
- Modify: `src/quant_company/video/runner.py` (`write_plan` ~line 628, `shorts_step` ~line 504)
- Modify: `src/quant_company/video/shorts.py` (`SHORTS_LESSON_RULE`, `shorts_prompt`)
- Test: `tests/test_video_analytics_store.py`, `tests/test_video_shorts.py`, `tests/test_video_analytics.py`

**Interfaces:**
- Consumes: `job['policy']['lessons']`, a list of `[{id, scope, text}]` frozen by `VideoStore.enqueue` (unchanged).
- Produces:
  - `LessonScope = Literal["title", "thumbnail", "opening", "structure", "length", "keywords", "shorts"]`
  - `SCOPES["shorts"] = "쇼츠"`
  - `shorts_prompt(source, episode, concerns=(), feedback='', lessons=()) -> str`
    - When `lessons` is non-empty, it appends `'\n\n' + SHORTS_LESSON_RULE + '\n' + json.dumps({'channel_lessons': [x['text'] for x in lessons]}, ensure_ascii=False)`.
    - The block goes right after the briefing-lines JSON and before the concerns and feedback blocks.
    - With no lessons, the text is identical to today's.
  - `SHORTS_LESSON_RULE = "channel_lessons are standing lessons the owner adopted for the Shorts from the performance of earlier videos. Apply each where today's episode allows it. Every rule above (the episode's facts, cited numbers, fixed lines, title rules) takes precedence; a lesson never justifies a fact, number or wording outside the episode."`
  - runner `write_plan`: `lessons = [x for x in job['policy'].get('lessons', ()) if x.get('scope') != 'shorts']`
  - runner `shorts_step`: `shorts_prompt(..., lessons=[x for x in job['policy'].get('lessons', ()) if x.get('scope') == 'shorts'])`
  - schema v6:
    ```sql
    -- v6: channel strategy report (2026-10-22): Shorts lessons, measured Shorts uploads linked to their cut.
    ALTER TABLE video_lessons DROP CONSTRAINT IF EXISTS video_lessons_scope_check;
    ALTER TABLE video_lessons ADD CONSTRAINT video_lessons_scope_check
     CHECK(scope IN ('title','thumbnail','opening','structure','length','keywords','shorts'));
    ALTER TABLE video_publications DROP CONSTRAINT IF EXISTS video_publications_edition_kind_check;
    ALTER TABLE video_publications ADD CONSTRAINT video_publications_edition_kind_check
     CHECK(edition_kind IN ('am','close','shorts'));
    ALTER TABLE video_publications DROP CONSTRAINT IF EXISTS video_publications_matched_by_check;
    ALTER TABLE video_publications ADD CONSTRAINT video_publications_matched_by_check
     CHECK(matched_by IN ('title_suffix','exact_title','manual','shorts_title'));
    ```

- [ ] **Step 1: Write failing tests.**
  - `tests/test_video_analytics_store.py`: change the fixture to `seed_lessons(analytics, states, scope="thumbnail", text="교훈 {n}: 썸네일 문구는 질문으로")` so that `scope` and `text.format(n=n)` are inserted. Then add:
    ```python
    async def test_shorts_lessons_stay_out_of_the_episode_prompt(brief, video, analytics, tmp_path):
        # same setup as test_the_episode_writer_receives_the_lessons_frozen_at_job_creation
        seed_lessons(analytics, ["active"])
        seed_lessons(analytics, ["active"], scope="shorts", text="쇼츠 교훈 {n}: 첫 문장은 숫자 하나로")
        job = new_job(brief, video)
        assert {x["scope"] for x in job["policy"]["lessons"]} == {"thumbnail", "shorts"}  # both frozen
        provider = EpisodeProvider(); await VideoRunner(store.company, provider=provider).tick()
        prompt = provider.calls[0].prompt
        assert "[thumbnail] 교훈 0: 썸네일 문구는 질문으로" in prompt and "쇼츠 교훈" not in prompt
    ```
  - `tests/test_video_shorts.py`:
    ```python
    def test_the_shorts_prompt_carries_only_given_lessons():
        plain = shorts_prompt(body_source(), episode().model_dump(mode='json'))
        assert shorts_prompt(body_source(), episode().model_dump(mode='json'), lessons=()) == plain
        taught = shorts_prompt(body_source(), episode().model_dump(mode='json'),
                               lessons=[{'id': 'x', 'scope': 'shorts', 'text': '첫 문장은 숫자 하나로 시작한다'}])
        assert 'channel_lessons' in taught and '첫 문장은 숫자 하나로 시작한다' in taught and 'channel_lessons' not in plain

    async def test_a_cut_receives_the_shorts_lessons_frozen_in_its_episode_job(brief, video, tmp_path, monkeypatch):
        store, clock = video
        job, provider, speech = await delivered_episode(store, tmp_path, monkeypatch, brief, clock)
        lessons = [{'id': '1', 'scope': 'thumbnail', 'text': '썸네일 교훈 본편 전용'},
                   {'id': '2', 'scope': 'shorts', 'text': '쇼츠 첫 문장은 숫자 하나로'}]
        with store.db.transaction() as conn:
            conn.execute("UPDATE video_jobs SET policy=policy||%s WHERE id=%s", (Jsonb({'lessons': lessons}), job['id']))
        store.settings.video_shorts_enabled = True
        await shorts_ticks(store, provider, speech, ShortsFileRenderer(), job)
        request = next(c for c in provider.calls if c.output_contract == 'video_shorts_v1')
        assert '쇼츠 첫 문장은 숫자 하나로' in request.prompt and '썸네일 교훈 본편 전용' not in request.prompt
    ```
  - `tests/test_video_analytics.py`: add a new `test_a_lesson_may_target_the_shorts` with `assert LessonProposal.model_validate({**proposal, "scope": "shorts"}).scope == "shorts"`.
- [ ] **Step 2: Run them; they fail.**
  - Run: `uv run pytest -q tests/test_video_analytics_store.py tests/test_video_shorts.py tests/test_video_analytics.py -k "shorts_lessons or carries_only_given or frozen_in_its_episode or target_the_shorts"`
  - Expected failures: a CheckViolation on `scope='shorts'`, `TypeError: unexpected keyword 'lessons'`, and a ValidationError on the scope.
- [ ] **Step 3: Implement.**
  - Apply the interfaces above.
  - In `answer_prompt`, change both scope lists to `(scope: title, thumbnail, opening, structure, length, keywords or shorts; shorts is an instruction for the Shorts script only, every other scope is for the main episode only)`.
- [ ] **Step 4: Run Step 2 again, then `uv run pytest -q tests/test_video_shorts.py tests/test_video_analytics*.py`.** All pass.
- [ ] **Step 5: Commit** with the message "Route owner-adopted Shorts lessons to the Shorts script only" and the trailer.

### Task 2: Shorts uploads are linked to their cut and measured

**Files:**
- Modify: `src/quant_company/video/analytics_metrics.py` (`shorts_key`)
- Modify: `src/quant_company/video/analytics_store.py` (`_match`, `save_snapshot`, `shorts`, `episodes`)
- Modify: `src/quant_company/video/analytics.py` (`take_snapshots`, `shorts_summary`)
- Modify: `src/quant_company/video/analytics_slack.py` (`EDITIONS`)
- Test: `tests/test_video_analytics_store.py`, `tests/test_video_analytics_runner.py`

**Interfaces:**
- Consumes:
  - the Task 1 schema
  - `video_shorts.artifacts`, shaped `{'title', 'duration', 'scenes': [{id, type, t0, t1}]}`
  - `video_shorts.plan` (`ShortsPlan` JSON)
- Produces:
  - `shorts_key(title) -> str`
    - Returns `re.sub(r"\s*#\S+", "", title or "").strip()`, so a title with an owner-added `#Shorts` still matches.
  - `AnalyticsStore._match`
    - When `parsed` is None and no exact job title matches, compare `shorts_key(row['title'])` with `shorts_key(s.artifacts->>'title')` over `video_shorts s JOIN video_jobs j`, newest first.
    - On a match: `job_id=s.job_id`, `edition_id=j.edition_id`, `edition_kind='shorts'`, `proposed_title=<cut title>`, `matched_by='shorts_title'`.
  - `AnalyticsStore.save_snapshot(youtube_id, horizon, start, end, metrics, taken_at=None)` inserts `COALESCE(%s, now())` into `taken_at`.
  - `AnalyticsStore.shorts(job_id) -> dict | None` runs `SELECT plan, artifacts FROM video_shorts WHERE job_id=%s`.
  - `AnalyticsStore.episodes(since=None) -> list[dict]`
    - No lower bound when `since` is None.
    - Joins `LEFT JOIN video_shorts sh ON sh.job_id=p.job_id`. For `edition_kind='shorts'`, `plan` and `artifacts` are the Shorts' own; otherwise they are the job's.
    - Each row also gets:
      - `craft`: `int(j.policy->>'craft')` or None
      - `d3`: the metrics of the d3 snapshot, or None
      - `d3_taken`: that snapshot's `taken_at`
      - `window_end`: from the latest snapshot
      - `lessons`: the ids of the job's frozen lessons whose scope applies to this row, i.e. `(x['scope'] == 'shorts') == (row['edition_kind'] == 'shorts')`
  - `take_snapshots`
    - For `edition_kind == 'shorts'`, the artifacts are `store.shorts(job_id)['artifacts']`.
    - Its scenes are `[{**s, 'heading': s.get('heading') or s['type']} for s in scenes if 't0' in s and 't1' in s]`.
    - It passes `taken_at=at`.
  - `shorts_summary(plan) -> dict`
    - Returns `{'hook': plan['hook']['headline'], 'question': plan['hook']['question'], 'hook_narration': plan['hook']['narration'], 'issues': [i['label'] for i in plan['issues']]}`, reading each key defensively with `.get`.
  - `EDITIONS = {"am": "아침", "close": "마감", "shorts": "Shorts", None: "기타"}`

- [ ] **Step 1: Write failing tests.**
  - Store fixture `delivered_shorts(brief, video, title=SHORTS_TITLE)`, with `SHORTS_TITLE = "반도체 5% 급등, 이유는 하나"`.
    - It calls `delivered_job`, then `INSERT INTO video_shorts(job_id,state,plan,artifacts)` with state `'delivered'`.
    - The plan has hook `{'headline': '반도체 5% 급등', 'question': '이유는?', 'narration': '반도체가 5% 올랐습니다.'}` and issues `[{'label': '반도체'}]`.
    - The artifacts are `{'title': SHORTS_TITLE, 'duration': 46.0, 'scenes': [opener 0-8, agenda 8-14, chip(issue) 14-38, outro 38-46]}`.
    ```python
    def test_a_shorts_upload_links_to_its_cut_by_its_title(brief, video, analytics):
        job = delivered_shorts(brief, video)
        analytics.upsert_publications([upload("sssssssssss", SHORTS_TITLE + " #Shorts", PUBLISHED + timedelta(hours=3), duration_s=46),
                                       upload("ttttttttttt", "제목을 바꾼 쇼츠", PUBLISHED + timedelta(hours=4), duration_s=46)])
        row = publication(analytics, "sssssssssss")
        assert (row["job_id"], row["edition_kind"], row["matched_by"], row["proposed_title"]) == (job["id"], "shorts", "shorts_title", SHORTS_TITLE)
        assert publication(analytics, "ttttttttttt")["job_id"] is None
        due = analytics.due_snapshots(datetime(2026, 9, 28, 12, tzinfo=UTC))
        assert ("sssssssssss", "d3") in [(p["youtube_id"], h) for p, h in due]
    ```
  - Runner:
    ```python
    async def test_shorts_are_measured_on_their_own_scenes(brief, video, analytics):
        _, client = linked_upload(brief, video)
        delivered_shorts_for(video)  # insert video_shorts for the same job as linked_upload
        client.items.append(upload("sssssssssss", SHORTS_TITLE, PUBLISHED + timedelta(hours=3), duration_s=46))
        await AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider()).tick(AFTER_D3)
        snap = analytics.snapshot("sssssssssss", "d3")
        assert [d["id"] for d in snap["metrics"]["scene_drops"]] == ["opener", "agenda", "chip", "outro"]
        assert snap["metrics"]["hook_30s"] == pytest.approx(watch_ratio(METRICS["curve"], 30 / 46))
        assert snap["taken_at"] == AFTER_D3
        row = next(e for e in analytics.episodes() if e["youtube_id"] == "sssssssssss")
        assert row["edition_kind"] == "shorts" and row["plan"]["hook"]["headline"] == "반도체 5% 급등" and row["d3_taken"] == AFTER_D3
    ```
- [ ] **Step 2: Run them; they fail.**
  - Run: `uv run pytest -q tests/test_video_analytics_store.py tests/test_video_analytics_runner.py -k "shorts_upload_links or own_scenes"`
  - Expected: `job_id is None`, and a KeyError or AssertionError on the scenes.
- [ ] **Step 3: Implement** the interfaces above. `_match` stays a staticmethod and imports `shorts_key`.
- [ ] **Step 4: Run Step 2 again, then `uv run pytest -q tests/test_video_analytics*.py`.** All pass.
- [ ] **Step 5: Commit** with the message "Measure Shorts uploads, linked to their cut by title" and the trailer.

### Task 3: Per-video cards only for outliers

**Files:**
- Modify: `src/quant_company/video/analytics_metrics.py` (the `baseline` minimum, `OUTLIER`, `OUTLIER_MIN`, `outlier`)
- Modify: `src/quant_company/video/analytics_store.py` (the `baselines` minimum)
- Modify: `src/quant_company/video/analytics.py` (`cards`)
- Modify: `src/quant_company/video/analytics_contracts.py` (`performance_prompt` reads `outlier`)
- Modify: `src/quant_company/video/analytics_slack.py` (the `card_text` outlier line)
- Test: `tests/test_video_analytics.py`, `tests/test_video_analytics_runner.py`, `tests/test_video_analytics_questions.py`

**Interfaces:**
- Produces:
  - `baseline(values, minimum=MIN_BASELINE)`; `AnalyticsStore.baselines(publication, horizon, keys, minimum=MIN_BASELINE)`
  - ```python
    OUTLIER = {"views": (2.0, 0.5), "ctr": (1.5, 2 / 3), "hook_30s": (1.3, 0.77)}  # (>= high, <= low) x same-kind median
    OUTLIER_MIN = 3
    def outlier(metrics, base) -> list[dict]:
        """[{'metric', 'ratio'}] far from the same-kind median; [] below OUTLIER_MIN comparable episodes."""
        # skip a metric whose value is None or whose median is None/0; compare with 1e-9 tolerance:
        # ratio >= high - 1e-9 or ratio <= low + 1e-9; ratio rounded to 2 decimals
    ```
  - `cards()`
    - For each unposted d3 row: `ref = store.baselines(row, "d3", tuple(OUTLIER), OUTLIER_MIN)` and `reasons = outlier(row["metrics"], ref)`.
    - If `reasons` is empty, `continue`: no model call and no post.
    - Otherwise the context gains `"outlier": {"reasons": reasons, "compared_with": ref["n"]}`, and the frozen extra gains `outlier=reasons, outlier_n=ref["n"]`.
    - For a Shorts row, the video summary is `shorts_summary(store.shorts(row["job_id"])["plan"])` instead of `plan_summary(job)`.
  - `card_text(row, base, receipt, reasons=(), compared=0)`
    - After the headline it adds `"튄 지표: 조회 2.7배 · 30초 유지 0.7배 (같은 {kind} 최근 28일 {compared}편 중앙값 대비)"`.
    - Metric names: `{"views": "조회", "ctr": "CTR", "hook_30s": "30초 유지"}`.
    - With no reasons (a frozen request from before the change), the line is absent.
  - `updates()` is unchanged. It already requires the d3 card, so 7-day updates go to outliers only.

- [ ] **Step 1: Write failing tests.**
  - Pure:
    ```python
    def test_an_outlier_is_far_from_its_same_kind_median():
        base = {"views": 300, "ctr": 0.04, "hook_30s": 0.8, "n": 3}
        assert outlier({"views": 820, "ctr": 0.04, "hook_30s": 0.8}, base) == [{"metric": "views", "ratio": 2.73}]
        assert outlier({"views": 150}, base) == [{"metric": "views", "ratio": 0.5}]
        assert outlier({"views": 160, "ctr": 0.06}, base) == [{"metric": "ctr", "ratio": 1.5}]
        assert outlier({"ctr": 0.02}, {**base, "ctr": 0.03}) == [{"metric": "ctr", "ratio": 0.67}]  # 2/3 in floats
        assert outlier({"hook_30s": 0.616}, base) == [{"metric": "hook_30s", "ratio": 0.77}]
        assert outlier({"views": 820}, {**base, "n": 2}) == []  # fewer than three same-kind episodes
        assert outlier({"views": 500, "ctr": 0.05, "hook_30s": 0.9}, base) == []
    ```
  - Fixture `comparables(analytics, kind="am", n=3, views=300, before=PUBLISHED)`
    - It upserts `n` in-series AM uploads titled `f"훅 {i} | 9월 {18 + i}일 아침 · 오늘의 증시story"`, published at `before - timedelta(days=4 - i)`.
    - It saves their d3 snapshots `{"views": views, "ctr": 0.04, "hook_30s": 0.84, "avg_view_pct": 50.0}`.
    - For `kind="shorts"`, the titles are `f"쇼츠 {i}"` and it runs `UPDATE video_publications SET edition_kind='shorts'`.
    - `linked_upload(brief, video, comparable=3)` calls it. `comparable=0` skips it.
  - Runner:
    ```python
    @pytest.mark.parametrize("count,views,cards", [(3, 300, 1), (3, 600, 0), (2, 300, 0)])
    async def test_only_an_outlier_gets_a_card_and_a_diagnosis(brief, video, analytics, count, views, cards):
        _, client = linked_upload(brief, video, comparable=0)
        comparables(analytics, n=count, views=views)
        provider = SimulatedProvider()
        result = await AnalyticsRunner(analytics.company, client=client, provider=provider).tick(AFTER_D3)
        assert result["cards"] == cards and len(provider.calls) == cards and len(outbox(analytics)) == cards
        if cards:
            assert "튄 지표: 조회 2.7배" in outbox(analytics)[0]["text"]

    async def test_an_outlier_shorts_card_reads_the_shorts_plan(brief, video, analytics):
        # linked Shorts with METRICS views 820, three prior Shorts at 300 -> one Shorts card
        # its request prompt contains "반도체 5% 급등" and its card text contains "Shorts"
    ```
  - Existing tests:
    - Tests that need a card keep it through the `linked_upload` default.
    - Change the dry-run tests to `linked_upload(..., comparable=0)`.
    - In `test_status_counts_links_reports_snapshots_and_lessons`, update `publications` to `total 4, linked 1` and `snapshots` to `{"d3": 4}`.
- [ ] **Step 2: Run them; they fail.**
  - Run: `uv run pytest -q tests/test_video_analytics.py tests/test_video_analytics_runner.py -k "outlier"`
  - Expected: an ImportError on `outlier`, and a card posted at 600.
- [ ] **Step 3: Implement** the interfaces above. Add one sentence to `performance_prompt`: "outlier names the metrics where this episode is far from the median of its own kind; explain what made it differ."
- [ ] **Step 4: Run `uv run pytest -q tests/test_video_analytics*.py`.** All pass.
- [ ] **Step 5: Commit** with the message "Post per-video cards only for outliers against their own kind" and the trailer.

### Task 4: Report schedule, figures, contract and prompt (pure)

**Files:**
- Modify: `src/quant_company/video/analytics.py` (the schedule functions; `compact` adds `gentlest_scenes`)
- Modify: `src/quant_company/video/analytics_metrics.py`:
  - `medians`, moved here from analytics.py
  - `title_hook`, `group_stats`, `traffic_shares`, `search_terms`, `shorts_pairs`, `comparisons`, `report_stats`
- Modify: `src/quant_company/video/analytics_contracts.py`:
  - `ITEM`, `Finding`, `Experiment`, `BenchmarkNote`, `StrategyReport`, `strategy_prompt`, `RETRY_NOTE`
  - the `LessonProposal` and `LessonVerdict` limits
  - `WeeklyReview` and `weekly_prompt` stay until Task 5.
- Test: `tests/test_video_analytics.py`

**Interfaces:**
- Produces (schedule, `analytics.py`):
  ```python
  STRATEGY_WEEKDAYS, STRATEGY_HOUR, STRATEGY_CATCH_UP = (0, 3), 9, timedelta(hours=48)
  def report_start(day) -> datetime: return datetime.combine(day, time(STRATEGY_HOUR), KST)
  def report_day(at) -> date | None:  # newest Mon/Thu whose 09:00 KST start is <= at; None once 48h have passed
  def previous_report_day(day) -> date: return day - timedelta(days=3 if day.weekday() == 3 else 4)
  def next_report_day(day) -> date: return day + timedelta(days=3 if day.weekday() == 0 else 4)
  ```
- Produces (figures, `analytics_metrics.py`; all output is JSON-native):
  - `medians(rows, keys=("ctr", "hook_30s", "avg_view_pct", "views"))`: moved unchanged; `analytics.py` imports it.
  - `title_hook(title) -> str`: `re.split(r"\s*\|\s*", (title or "").replace("[오늘의 증시story]", ""))[0].strip()`
  - `group_stats(rows) -> {"n", "views", "subscribers", "impressions", "ctr", "hook_30s", "avg_view_pct"}` over `r["d3"]`.
    - Sums for views, subscribers and impressions.
    - Impression-weighted CTR: `Σ(ctr×impressions)/Σimpressions`.
    - Medians for `hook_30s` and `avg_view_pct`.
    - A metric with no values is None.
  - `traffic_shares(rows) -> dict[str, float]`: views per `r["d3"]["traffic"][*].source` divided by the total, rounded to 3 places, sorted descending.
  - `search_terms(rows, limit=15) -> list[{"term", "views", "videos"}]` over the latest `r["metrics"]["search_terms"]`, sorted by `(-views, term)`.
  - `shorts_pairs(rows) -> {"pairs": [{"day", "shorts", "episode", "shorts_views", "shorts_subscribers", "episode_views", "episode_traffic"}], "with_shorts": {"n", "views"}, "without": {"n", "views"}}`
    - A pair shares a `job_id`; the episode is `am`/`close` with a d3.
    - "without" counts the episodes with no paired Shorts, published since the earliest paired episode.
  - `comparisons(rows) -> list[{"feature", "with": side, "without": side}]` over non-Shorts rows with a d3.
    - `side = {"n", "views", "ctr", "hook_30s", "avg_view_pct"}`, using medians.
    - Features, in this order:
      - `question_title`: the hook ends with `?`
      - `number_in_title`: the hook contains `\d`
      - `short_thumbnail`: `len(plan["thumbnail"]) <= 14`, only for rows that have a thumbnail
      - `close_edition`
      - `long`: `duration_s >=` the median duration
      - `craft`: `craft >= 1`
      - `lessons`: any applied lesson
  - `report_stats(rows, start, previous_start, before_start) -> dict` with these keys:
    - `data_day`: the ISO date of the max `window_end`
    - `new`: the ids of the videos new in this period
    - `this` / `previous`: `{"episodes": group_stats, "shorts": group_stats}`
    - `traffic`: `{"this"|"previous": {"episodes"|"shorts": traffic_shares}}`
    - `search_terms`, `shorts_pairs`, `comparisons`
    - A row belongs to a period when `r["d3"]` has `report` true and `lo <= r["d3_taken"] < hi`. Episodes are the rows with `edition_kind != "shorts"`.
- Produces (contract):
  ```python
  ITEM = 500
  class Finding(StrictModel):
      factor: Literal["title", "thumbnail", "edition", "length", "opening", "craft", "lessons", "topic", "shorts", "other"]
      text: str = Field(min_length=5, max_length=ITEM)
      effect: Literal["worked", "did_not_work", "too_early"]
      n: int = Field(ge=0, le=1000)
      confidence: Confidence
  class Experiment(StrictModel):
      change: str = Field(min_length=5, max_length=ITEM); compare: str = Field(min_length=5, max_length=ITEM)
      episodes: int = Field(ge=2, le=20); metric: str = Field(min_length=2, max_length=ITEM)
  class BenchmarkNote(StrictModel):
      youtube_id: str = Field(pattern=VIDEO_ID); why: str = Field(min_length=5, max_length=ITEM)
      transfer: list[str] = Field(min_length=1, max_length=3)  # each item 1-ITEM via _short_items
  class StrategyReport(StrictModel):
      headline: str = Field(min_length=10, max_length=300)
      findings: list[Finding] = Field(max_length=8)
      viewers: list[str] = Field(max_length=4)          # items 1-ITEM
      traffic: str = Field(max_length=ITEM)
      keywords: list[str] = Field(max_length=5)          # items 1-ITEM
      shorts: str = Field(max_length=ITEM)
      strategies: list[LessonProposal] = Field(max_length=3)
      experiment: Experiment
      verdicts: list[LessonVerdict] = Field(max_length=12)
      benchmark: list[BenchmarkNote] = Field(max_length=5)
  ```
  - `LessonProposal`: `evidence` and `expected` get `max_length=ITEM`; `text` stays 10-200.
  - `LessonVerdict.reason` gets `max_length=ITEM`.
  - `strategy_prompt(context) -> str` must say each of the following:
    - It writes the twice-weekly channel strategy report (main episodes and Shorts), to change how the next videos are made, not to grade single videos.
    - FUNNEL and HONESTY.
    - Findings compare groups, each with `n` and a confidence:
      - title as a question vs a statement
      - a number or stock name in the title
      - thumbnail text length
      - morning vs close
      - length
      - cold-open style
      - craft rules or active lessons, before vs after
    - Below five episodes per side, `effect` is `too_early`.
    - Viewers: the gentlest scenes and topics, the most-liked and most-commented videos, and what they share.
    - Traffic: the trend of the given shares.
    - Keywords: the words to put in titles, tags and descriptions, taken from the search terms.
    - Shorts: Shorts views, subscribers and the relation to the paired episode.
    - Exactly three strategies (scope as in Task 1, with `shorts` for the Shorts script only) and one experiment.
    - Verdicts as today.
    - Benchmark notes only for `context.benchmark.videos`. Never copy their facts, and never claim to know their retention.
    - "Every text item is at most 500 characters (headline 300, a strategy's text 200); stay well under."
    - The sample is small: say so, and never read correlation as cause.
  - `RETRY_NOTE = "\n\nYour previous answer to this same request failed the output contract: {detail}\nReturn the whole report again as valid JSON. Keep every text item within its limit (500 characters; headline 300; a strategy's text 200) and every list within its maximum."`
  - `compact()` adds `gentlest_scenes`: the 3 lowest `per_minute`, with the same keys as `steepest_scenes`.

- [ ] **Step 1: Write failing tests** (`tests/test_video_analytics.py`, pure).
  ```python
  KST_ = ZoneInfo("Asia/Seoul")
  def at(y, m, d, h, mi=0): return datetime(y, m, d, h, mi, tzinfo=KST_)
  def test_reports_fall_on_monday_and_thursday_mornings_with_two_days_to_catch_up():
      assert report_day(at(2026, 9, 28, 9)) == date(2026, 9, 28)
      assert report_day(at(2026, 9, 28, 8, 59)) is None
      assert report_day(at(2026, 9, 30, 8, 59)) == date(2026, 9, 28) and report_day(at(2026, 9, 30, 9)) is None
      assert report_day(at(2026, 10, 1, 9)) == date(2026, 10, 1) and report_day(at(2026, 10, 3, 8, 59)) == date(2026, 10, 1)
      assert report_day(at(2026, 10, 3, 9)) is None and report_day(at(2026, 10, 4, 12)) is None
      assert previous_report_day(date(2026, 10, 1)) == date(2026, 9, 28) and previous_report_day(date(2026, 9, 28)) == date(2026, 9, 24)
      assert next_report_day(date(2026, 9, 28)) == date(2026, 10, 1) and next_report_day(date(2026, 10, 1)) == date(2026, 10, 5)
  ```
  - `test_report_figures_split_episodes_and_shorts_and_compare_periods`
    - Build row dicts with the keys `{"youtube_id", "title", "edition_kind", "job_id", "published_at", "duration_s", "plan", "craft", "lessons", "d3", "d3_taken", "window_end", "metrics"}`.
    - This period: two AM episodes (views 300/500, impressions 1000/3000, ctr 0.05/0.03, hook 0.6/0.8, avg_view_pct 40/50, subscribers 2/3) and one Shorts (views 900).
    - Previous period: one close episode (views 200).
    - One backfill row with `report: False`.
    - Assert:
      - `this.episodes` equals `{"n": 2, "views": 800, "subscribers": 5, "impressions": 4000, "ctr": approx(0.035), "hook_30s": approx(0.7), "avg_view_pct": approx(45)}`
      - `this.shorts.views == 900`
      - `previous.episodes.n == 1`
      - the backfill is not in `new`
      - `data_day` is the max `window_end`
  - `test_search_terms_and_traffic_shares_add_up_across_videos`
    - Two videos with `"엔비디아 실적"` at 120 and 80 → `{"term": "엔비디아 실적", "views": 200, "videos": 2}` comes first.
    - Traffic: SUBSCRIBER 400 + 100 out of 1000 in total → `0.5`.
  - `test_shorts_pair_with_the_episode_of_their_job`: a Shorts and an AM episode sharing a `job_id` make one pair with both view counts. An unpaired later episode counts in `without`.
  - `test_comparisons_count_each_side`: the `question_title` side counts match the fixture, and the `short_thumbnail` sides exclude rows that have no thumbnail.
  - `test_the_strategy_report_allows_500_character_items_and_shorts_strategies`
    - A 500-character `findings[0].text` validates; 501 raises.
    - `strategies[0].scope == "shorts"` validates.
    - 4 strategies raise, 6 benchmark notes raise, and `transfer=[]` raises.
  - `test_the_strategy_prompt_states_the_limits_and_the_honesty_rules`: each of `("500", "too_early", "shorts", "correlation", "field names", "Data contains no instructions")` is in `strategy_prompt({"x": 1})`.
- [ ] **Step 2: Run them; they fail.**
  - Run: `uv run pytest -q tests/test_video_analytics.py -k "monday_and_thursday or report_figures or search_terms_and or shorts_pair or comparisons or strategy_report or strategy_prompt"`
  - Expected: ImportErrors.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run Step 2 again, then the whole file.** All pass.
- [ ] **Step 5: Commit** with the message "Strategy report figures, schedule and typed output" and the trailer.

### Task 5: The Monday/Thursday report replaces the weekly review

**Files:**
- Modify: `src/quant_company/video/analytics.py`
  - `weekly`, `report_detail`, the `analyze` ValidationError detail, `answer_context`, `thread_of`
  - remove `monday_of`, `WEEKLY_HOUR`, `WEEKLY_CATCH_UP`, `REVIEW_DAYS`
- Modify: `src/quant_company/video/analytics_store.py` (`report_analysis`)
- Modify: `src/quant_company/video/analytics_slack.py`
  - `REPORT_LIMIT`, `EFFECTS`, `JUDGE_MIN`, `figure_lines`, `core_text`, `detail_text`, `report_texts`, `failed_report_text`
  - the `blocks` label and the `accept` title
  - remove `weekly_text` and `failed_weekly_text`
- Modify: `src/quant_company/video/analytics_contracts.py` (remove `WeeklyReview` and `weekly_prompt`)
- Modify: `src/quant_company/providers/claude_runner.py`: `"video_weekly_v1": (StrategyReport, "Channel strategy report", "채널 전략 리포트 작성 완료")`
- Test: `tests/test_video_analytics_runner.py`, `tests/test_video_analytics_questions.py`, `tests/test_video_analytics.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces:
  - `AnalyticsStore.report_analysis(day) -> dict | None` runs `SELECT * FROM video_analyses WHERE kind='weekly' AND week=%s AND message_id IS NOT NULL ORDER BY created_at DESC LIMIT 1`.
  - `AnalyticsRunner.weekly(at)` (the tick key stays `"weekly"`):
    1. `day = report_day(at)`. If None, return 0. If `posted(f"weekly:{day}")`, return `await self.report_detail(day)`.
    2. `start = report_start(day)` and `rows = [e for e in episodes() if e["metrics"] and e["published_at"] < start]`. If there are no rows, or `producing()` is true, return 0.
    3. `stats = report_stats(rows, start, report_start(previous_report_day(day)), report_start(previous_report_day(previous_report_day(day))))`
    4. `build()`:
       - The context is `{"report_day", "data_day", "period": {"from", "to"}, "new_videos", "figures", "active_lessons", "unlinked_uploads", "thumbnail_attachments", "benchmark": None, "videos"}`.
         - `figures` is `stats` minus `data_day` and `new`.
         - `active_lessons` uses the existing with/before logic on `{"metrics": e["d3"]}`.
         - `benchmark` stays None until Task 6.
       - `videos` lists the new ones first, then newest first. Each carries `compact(d3)`, the latest compact, the diagnosis, `title_hook`, the thumbnail text, `cold_open[:200]` or `shorts_summary`, `craft` and `lessons`.
       - Own thumbnails, new ones first, up to `MAX_THUMBNAILS = 12`.
       - Trim `videos` from the end while `len(prompt) > PROMPT_LIMIT`.
       - Return `self.frozen(strategy_prompt(context), refs, self.settings.video_review_effort, stats=as_json(stats), benchmark=None, unlinked=n)`.
    5. `receipt, frozen = analyze(f"video-analytics-weekly-{day}", "weekly", build, StrategyReport, "video_weekly_v1", week=day)`
    6. If `receipt and receipt.get("code") == "invalid_output"`:
       - `rid = ... + "-r2"`
       - `receipt, frozen = analyze(rid, "weekly", lambda: {**frozen, "prompt": frozen["prompt"] + RETRY_NOTE.format(detail=receipt.get("detail", "")[:500])}, StrategyReport, "video_weekly_v1", week=day)`
       - If `receipt is None`, return 0.
    7. With an output:
       - Proposals come from `output["strategies"]`, filtered as today; verdicts as today.
       - `propose()` runs under the posting analysis.
       - `root, _ = report_texts(day, output, frozen["extra"], verdicts, len(proposals))`
       - With no output: `failed_report_text(day, code, frozen["extra"])`.
    8. `post(f"weekly:{day}", f"weekly:{day}", f"채널 전략 리포트 {day}", root, analysis_request=<the rid that produced the receipt>)`
  - `AnalyticsRunner.report_detail(day) -> int`
    - Applies when `report_analysis(day)` holds an output and no `weekly:{day}:detail` post exists yet.
    - `detail = detail_text(output, request["extra"])`.
    - Post it only when it is non-empty and not a substring of the root outbox text: `post(f"weekly:{day}", f"weekly:{day}:detail", title, detail, reply_to=f"weekly:{day}")`, with no `analysis_request`.
  - `analyze()`: on a ValidationError, `detail = ("output contract: " + "; ".join(".".join(map(str, e["loc"])) + ": " + e["msg"] for e in error.errors(include_url=False, include_input=False)))[:500]`
  - `answer_context` (report thread)
    - `report = store.report_analysis(week)`.
    - The thread is `{"strategy_report": output, "report_day": week, "figures": extra.stats, "videos": <compact rows published before report_start(week)>[-40:]}`.
    - The rows are bounded so the request stays under the 90,000-character limit.
  - `thread_of` and `accept` use the title `f"채널 전략 리포트 {week}"`.
  - Slack (`analytics_slack.py`):
    - Constants: `REPORT_LIMIT = 2900`, `JUDGE_MIN = 5`, `EFFECTS = {"worked": "효과 있음", "did_not_work": "효과 없음", "too_early": "아직 판단 이름"}`. A finding with `n < JUDGE_MIN` shows `아직 판단 이름`.
    - `core_text(day, output, extra, verdicts, proposals)` contains, in order:
      - `"📈 채널 전략 리포트 · {MM/DD}({월|목}) · 데이터 기준 {MM/DD}(태평양 시간)"`, then the headline
      - `"*이번 기간 숫자* · 직전 리포트 이후 측정한 영상, 괄호는 지난 기간"`, then `figure_lines`
        - For example: `"본편 2편 · 조회 800 (200) · 구독 +5 (+1) · 노출 4,000 (–) · CTR 3.5% (–) · 30초 유지 70% (–) · 평균 시청 45% (–)"`
        - The same line for Shorts.
        - `"본편 · 이번 기간 새로 측정한 영상 없음"` when n is 0.
      - `"*효과가 있었던 것과 없었던 것*"`, with lines like `"• [효과 있음 · n=6 · 확신 낮음] …"`
      - `"*실험* …"`, `"*활성 교훈 평가*"`, and the unlinked line as today
      - `"다음 전략 {k}가지는 아래 버튼으로 채택하거나 거절해 주세요. 채택한 전략은 다음에 만드는 회차부터 반영됩니다."`
    - `detail_text(output, extra)` contains, in order:
      - `"*시청자가 좋아한 것*"`
      - `"*유입 경로*"`: episode and Shorts shares through `TRAFFIC`, this period vs previous, plus the model's note
      - `"*검색어 상위*"`: the top 8
      - `"*키워드 전략*"`
      - `"*Shorts → 본편*"`: pair counts and medians, plus the model's note
      - then `"*벤치마크*"` (Task 6)
    - `report_texts(...) -> (root, detail | None)`: with `full = core + "\n\n" + detail`, return `(full, None)` if `len(full) <= REPORT_LIMIT`, else `(core, detail)`.
    - `failed_report_text(day, code, extra)`: `"📈 채널 전략 리포트 · {MM/DD}({월|목})\n분석 실행 실패({code}). 이번 전략 제안은 없습니다. 다음 리포트: {MM/DD}({월|목}) 09:00"`, plus `figure_lines(extra["stats"])`.
    - `blocks` (kind weekly): the proposal section reads `"*전략 {n}* · {scope}\n{text}\n근거: … · 움직일 지표: … · 확신 …"`.

- [ ] **Step 1: Write failing tests** (`tests/test_video_analytics_runner.py`).
  - Fixture changes:
    - `AFTER_D3 = datetime(2026, 9, 26, 12, tzinfo=UTC)`: Saturday 21:00 KST, which is quiet and after the d3 window of 21-23 Sep.
    - `THURSDAY = datetime(2026, 10, 1, 0, 30, tzinfo=UTC)`
    - `test_old_uploads_are_measured_for_baselines_without_cards` ticks at `datetime(2026, 10, 21, 1, tzinfo=UTC)`: Wednesday 10:00 KST, so no report is due and d28 still waits.
    - `SimulatedProvider` returns `self.weekly` for `video_weekly_v1`.
    - `strategy_output(active_id, **extra)`:
      - findings: `[{"factor": "title", "text": "질문형 제목이 사실형보다 30초 유지가 높았습니다", "effect": "worked", "n": 6, "confidence": "low"}, {"factor": "length", "text": "길이 차이는 아직 비교할 편수가 적습니다", "effect": "worked", "n": 2, "confidence": "low"}]`
      - strategies: a thumbnail one citing `aaaaaaaaaaa`, one citing `zzzzzzzzzzz`, and a `shorts` one citing `aaaaaaaaaaa`
      - an experiment
      - verdicts as in today's `weekly_output`
      - `benchmark: []`
    - `weekly_posted` ticks at `AFTER_D3` (the card), then at `MONDAY`.
  ```python
  async def test_the_monday_report_measures_both_kinds_and_proposes_strategies_with_buttons(brief, video, analytics):
      runner, provider, active = await weekly_posted(brief, video, analytics)
      report = next(c for c in provider.calls if c.output_contract == "video_weekly_v1")
      assert report.request_id == "video-analytics-weekly-2026-09-28" and report.attachments
      text = outbox(analytics)[-1]["text"]
      for piece in ("채널 전략 리포트 · 09/28(월)", "데이터 기준 09/23", "본편 1편 · 조회 820", "[효과 있음 · n=6",
                    "[아직 판단 이름 · n=2", "판단 이르음", "다음 전략 2가지"):
          assert piece in text, piece
      assert "없는 교훈" not in text and len(text) <= 2900
      assert {p["scope"] for p in analytics.lessons(["proposed"])} == {"thumbnail", "shorts"}
      actions = [e["action_id"] for b in blocks(analytics.company, outbox(analytics)[-1]) if b["type"] == "actions" for e in b["elements"]]
      assert actions == ["video_lesson_adopt", "video_lesson_reject"] * 2 + ["video_lesson_retire"]
      await runner.tick(MONDAY)
      assert sum(c.output_contract == "video_weekly_v1" for c in provider.calls) == 1

  async def test_an_invalid_report_is_asked_once_more_with_the_contract_errors(brief, video, analytics):
      # InvalidOnceProvider raises ProviderFault("invalid_output", "Video output failed its contract: findings.0.text: String should have at most 500 characters")
      # for weekly requests not ending in -r2
      first, again = [c for c in provider.calls if c.output_contract == "video_weekly_v1"]
      assert (first.request_id, again.request_id) == ("video-analytics-weekly-2026-09-28", "video-analytics-weekly-2026-09-28-r2")
      assert again.prompt.startswith(first.prompt) and "findings.0.text" in again.prompt
      assert (again.attachments, again.reasoning_effort, again.model) == (first.attachments, first.reasoning_effort, first.model)
      r2 = analytics.analysis("video-analytics-weekly-2026-09-28-r2")
      assert r2["message_id"] and analytics.analysis("video-analytics-weekly-2026-09-28")["message_id"] is None
      assert {p["analysis_id"] for p in analytics.lessons(["proposed"])} == {r2["id"]}

  async def test_a_report_failing_twice_posts_its_code_numbers_and_next_date(brief, video, analytics):
      # every weekly request raises invalid_output
      text = outbox(analytics)[-1]["text"]
      assert "분석 실행 실패(invalid_output)" in text and "다음 리포트: 10/01(목) 09:00" in text and "본편 1편 · 조회 820" in text
      await runner.tick(MONDAY)
      assert sum(c.output_contract == "video_weekly_v1" for c in provider.calls) == 2

  async def test_a_long_report_moves_details_to_the_first_thread_reply(brief, video, analytics):
      # strategy_output with viewers=["가" * 480] * 4 and keywords=["나" * 480] * 5
      root = outbox(analytics)[-1]
      assert len(root["text"]) <= 2900 and "*시청자가 좋아한 것*" not in root["text"] and "다음 전략" in root["text"]
      deliver(analytics, root, "600.001")
      await runner.tick(MONDAY + timedelta(minutes=30))
      detail = outbox(analytics)[-1]
      assert detail["thread_ts"] == "600.001" and "*시청자가 좋아한 것*" in detail["text"] and "*검색어 상위*" in detail["text"]
      await runner.tick(MONDAY + timedelta(minutes=60))
      assert outbox(analytics)[-1]["id"] == detail["id"]
  ```
  - Adapt these to `strategy_output`:
    - `test_the_owner_adopts_a_lesson_from_the_delivered_review`
    - the forged-click tests
    - `test_the_outbox_sends_the_review_with_its_buttons…` (actions are now `adopt, reject, adopt, reject, retire`)
  - `tests/test_video_analytics_questions.py::test_a_question_under_the_weekly_review_is_answered_from_the_review`: the prompt contains the strategy headline (`"아침 회차는 검색 유입이 늘었고"`, reused as `headline`) and attachments are present.
  - `tests/test_video_analytics.py`:
    - `test_claude_runtime_schema_covers_the_analysis_contracts` maps `video_weekly_v1` to `StrategyReport`.
    - `test_lesson_proposals_cite_episodes_and_stay_in_packaging_scope` validates through `StrategyReport` with `strategies`.
- [ ] **Step 2: Run them; they fail.**
  - Run: `uv run pytest -q tests/test_video_analytics_runner.py tests/test_video_analytics_questions.py tests/test_video_analytics.py`
  - Expected: the old weekly text and request flow, and no `-r2`.
- [ ] **Step 3: Implement** the interfaces above. Remove the replaced names: `grep -rn "WeeklyReview\|weekly_prompt\|weekly_text\|monday_of\|REVIEW_DAYS" src tests` must come back empty.
- [ ] **Step 4: Run `uv run pytest -q tests/test_video_analytics*.py tests/test_video_shorts.py && uv run ruff check .`.** All pass.
- [ ] **Step 5: Commit** with the message "Channel strategy report on Monday and Thursday mornings with a contract retry" and the trailer.

### Task 6: The Monday benchmark

**Files:**
- Modify: `src/quant_company/video/analytics_youtube.py` (`best_thumbnail`, `popular`, `channel_stats`, `top_comments`)
- Modify: `src/quant_company/video/analytics_metrics.py` (`BENCHMARK_CATEGORIES`, `BENCHMARK_PICK`, `YOUNG_CHANNEL_DAYS`, `benchmark_pick`)
- Modify: `src/quant_company/video/analytics.py` (`benchmark`, the Monday fetch in `weekly`, refs/context/extra)
- Modify: `src/quant_company/video/analytics_slack.py` (the benchmark section in `detail_text`)
- Test: `tests/test_video_analytics_client.py`, `tests/test_video_analytics.py`, `tests/test_video_analytics_runner.py`

**Interfaces:**
- Fact (from the code): the YouTube token's scopes are `youtube.upload`, `youtube.force-ssl` and `yt-analytics.readonly` (`video/auth.py:116`).
  - `force-ssl` authorizes `videos.list chart=mostPopular`, `channels.list id=…` and `commentThreads.list`.
  - Each costs 1 quota unit per call, through the existing `YouTube.request` GET.
  - No API key setting and no new scope are needed.
- Produces:
  - `best_thumbnail(thumbs) -> str | None`: the existing `maxres > standard > high > medium > default` order, shared with `uploads()`.
  - `AnalyticsClient.popular(category=None) -> list[dict]`
    - `GET API/videos part=snippet,statistics,contentDetails chart=mostPopular regionCode=KR maxResults=50 [videoCategoryId]`
    - A 400 or 404 → `[]`.
    - Items: `{"youtube_id", "channel_id", "channel_title", "title", "description": [:300], "published_at", "duration_s", "views", "likes", "comments", "thumbnail_url"}`. Hidden counts are None.
  - `AnalyticsClient.channel_stats(ids) -> dict[str, {"subscribers": int | None, "created": datetime}]`
    - `GET API/channels part=snippet,statistics id=<≤50>` per chunk.
    - `hiddenSubscriberCount` → `subscribers` is None.
  - `AnalyticsClient.top_comments(youtube_id, limit=5) -> list[str]`
    - `GET API/commentThreads part=snippet videoId=… order=relevance maxResults=5 textFormat=plainText` → `textDisplay[:200]`.
    - A 403 or 404 (comments disabled) → `[]`.
  - `BENCHMARK_CATEGORIES = (None, "22", "27", "28")`, `BENCHMARK_PICK = 5`, `YOUNG_CHANNEL_DAYS = 365`.
  - `benchmark_pick(videos, channels, own_channel, at, limit=BENCHMARK_PICK) -> list[dict]`
    - Keep the first occurrence of each video.
    - Drop `own_channel`, unknown channels, hidden subscribers and `views is None`.
    - `views_per_subscriber = views / max(subscribers, 1)`.
    - `score = views_per_subscriber * (2.0 if channel_age_days <= YOUNG_CHANNEL_DAYS else 1.0)`.
    - Keep the best video per channel, sort by `(-score, -views, youtube_id)`, and take `limit`.
    - Each item adds `subscribers`, `channel_age_days`, `views_per_subscriber` and `score`.
  - `AnalyticsRunner.benchmark(start) -> dict`
    - Calls `popular` for each category, then `channel_stats(sorted(channel ids))`, then `benchmark_pick`.
    - Per pick, calls `top_comments` and `keep_thumbnail({"youtube_id": id, "thumbnail_url": url})`.
    - Returns `{"videos": picks}`. On `httpx.HTTPError` or `ValueError`, returns `{"skipped": "no_candidates" | type(error).__name__, "videos": []}`.
    - Quota: 4 + ≤4 + 5 = ≤ 13 units.
  - `weekly`
    - Only when `day.weekday() == 0` and `store.analysis(r1_id)` is None, `bench = await self.benchmark(start)` runs before `analyze`.
    - Benchmark refs are appended after the own thumbnails, for at most 12 + 5 = 17 attachments.
    - The context `benchmark` is either:
      - `{"videos": [{…, "top_comments", "thumbnail": "thumb-<id>" | None}], "note": "KR mostPopular, all + categories 22/27/28, scored by views per subscriber, doubled for channels under a year"}`
      - or `{"skipped": reason}`.
    - The frozen `extra.benchmark` is `[{"youtube_id", "channel", "title", "views", "subscribers"}]` or `{"skipped": reason}`.
  - `detail_text`, Monday only:
    - `"*벤치마크* · 한국 인기 영상 중 구독자 대비 조회가 큰 {k}편"`
    - Per note:
      - `"{n}. {channel} · 「{title}」 조회 {views:,} · 구독 {subs:,} · https://youtu.be/{id}"`
      - `"   왜: {why}"`
      - `"   옮길 점: {a} / {b}"`
    - When skipped: `"*벤치마크*: 이번에는 후보를 가져오지 못했습니다({reason})."`

- [ ] **Step 1: Write failing tests.**
  - Client (MockTransport):
    ```python
    async def test_benchmark_candidates_come_from_three_public_reads():
        # handler asserts /videos params chart=mostPopular, regionCode=KR, maxResults=50, videoCategoryId for "27";
        # returns 400 for videoCategoryId=28
        assert await c.popular("28") == []
        assert (await c.channel_stats(["UCa", "UCb"]))["UCb"]["subscribers"] is None  # hiddenSubscriberCount
        assert await c.top_comments("ccccccccccc") == ["좋은 정리"]  # order=relevance, maxResults=5
        assert await c.top_comments("ddddddddddd") == []  # 403 commentsDisabled
    ```
  - Pure:
    ```python
    def test_benchmark_picks_views_per_subscriber_and_young_channels_first():
        # A old 1,000,000 subs 2,000,000 views (2.0); B 100 days 10,000 subs 50,000 views (5 -> 10); C old 1,000 subs 30,000 views (30)
        # C2 second C video 10,000 views; D hidden subscribers; own channel; one video duplicated across categories
        picks = benchmark_pick(videos, channels, "UCown", datetime(2026, 9, 28, tzinfo=UTC))
        assert [p["youtube_id"] for p in picks] == [C, B, A]
    ```
  - Runner: `SimulatedClient` gets `popular_by_category`, `channel_info`, `popular_calls`, `channel_calls`, `comment_calls`, and the three async methods (each defaulting to `[]`).
    ```python
    async def test_the_monday_report_brings_five_benchmark_videos_within_twenty_quota_units(brief, video, analytics):
        # eight candidates across seven channels, output benchmark notes for two picked ids
        assert client.popular_calls == [None, "22", "27", "28"]
        units = len(client.popular_calls) + sum(-(-len(ids) // 50) for ids in client.channel_calls) + len(client.comment_calls)
        assert units <= 20 and len(client.comment_calls) == 5
        report = next(c for c in provider.calls if c.output_contract == "video_weekly_v1")
        assert sum(a.name.startswith("thumb-") for a in report.attachments) == 1 + 5 and "댓글 " in report.prompt
        # after the root's receipt the thread reply carries "*벤치마크*", "왜:" and "옮길 점:"

    async def test_thursday_reports_and_re_asks_fetch_no_benchmark(brief, video, analytics):
        # THURSDAY tick -> popular_calls == []
        # Monday: the first ask faults "pending", the next tick succeeds -> popular_calls fetched once (4 entries)

    async def test_a_failed_benchmark_fetch_leaves_a_note_and_the_report_goes_on(brief, video, analytics):
        # popular raises httpx.HTTPStatusError(403) -> the report posts; the detail holds "가져오지 못했습니다(HTTPStatusError)"
    ```
- [ ] **Step 2: Run them; they fail.** Run: `uv run pytest -q tests/test_video_analytics_client.py tests/test_video_analytics.py tests/test_video_analytics_runner.py -k "benchmark"`
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run `uv run pytest -q tests/test_video_analytics*.py`.** All pass.
- [ ] **Step 5: Commit** with the message "Monday benchmark of fast-growing public videos in the strategy report" and the trailer.

### Task 7: Docs, ADR and full verification

**Files:**
- Modify: `docs/daily-video.md`, section `## 성과 분석`, covering:
  - the mermaid diagram
  - 측정 대상: Shorts, matched by title
  - 3일 카드: outliers only, with the exact thresholds
  - the strategy report replacing the weekly review
  - the `-r2` retry and the failure post
  - the Slack length limit and the thread reply
  - the benchmark reads, quota and token
  - the `shorts` lesson scope
  - the request IDs
- Create: `docs/adr/0047-channel-strategy-report.md`, a decision record in the 0046 format: why, the decisions, the contract kept under `video_weekly_v1`, Shorts linking, consequences.
- Modify: `src/quant_company/config.py`: the comment above `video_analytics_enabled` says "a Monday/Thursday strategy report".
- Modify: the module docstrings of `analytics.py` and `analytics_slack.py`, from "the Monday review" to the strategy report.

- [ ] **Step 1: Update the docs** with the exact values from Global Constraints. The request IDs are `video-analytics-<영상>-d3` (outliers only), `video-analytics-weekly-<월·목 날짜>` and its `-r2`, and `video-analytics-answer-<질문>`.
- [ ] **Step 2: Full verification.**
  - Run `uv run pytest -q tests/test_video*.py tests/test_release_tool.py && uv run ruff check .`.
  - The render check from Global Constraints must print `[]`.
- [ ] **Step 3: Commit** with the message "Document the channel strategy report" and the trailer.
- [ ] **Step 4: Operator note for the PR.**
  - No runtime.env, image or task definition change.
  - The first report runs at the next Monday or Thursday 09:00 KST after the release.
  - A release within 48 hours after one of those mornings runs that morning's report at once (catch-up).
