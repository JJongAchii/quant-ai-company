import json
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from quant_company.briefing import schedule
from quant_company.briefing.contracts import BriefProposal, BriefReview, MarketObservation
from quant_company.briefing.data import query
from quant_company.briefing.data_reader import read_snapshot, summarize
from quant_company.briefing.editor import prompt, render, validate
from quant_company.briefing.qualification import qualify, replay
from quant_company.briefing.quality import assurance, reconcile

from .test_briefing import brief, bundle, complete, definition, proposal, response, review, seed  # noqa: F401


def snapshot(edition=None):
    edition = edition or collected_pm()
    day = edition.day
    dates = [day]
    for _ in range(21):
        day = schedule.previous("KR", day)
        dates.append(day)
    rows = [{"date": str(d), "index": name, "close": str(Decimal(base)-i), "value": "10000000000"}
            for name, base in (("KOSPI", 3000), ("KOSDAQ", 900)) for i, d in enumerate(dates)]
    return {"ok": True, "observed_at": (edition.cutoff-timedelta(minutes=10)).isoformat(),
        "qdata_code_commit": "synthetic", "errors": [], "datasets": {"krx_index": {
            "source": {"uri": "s3://example/qdata/clean/krx_index.parquet", "etag": "synthetic-v1"},
            "date_bounds": {}, "rows": rows}}}


def data_bundle(edition=None):
    edition = edition or collected_pm()
    derived = summarize(snapshot(edition), edition)
    data = bundle(edition)
    data.update(documents=derived["documents"], locked_observations=derived["observations"],
                market_context=derived["contexts"], data_diagnostics=derived["diagnostics"])
    return data


def collected_pm():
    # Collected same-day lake closes require their conservative availability time;
    # the earlier normal PM edition relies on independent closing reports instead.
    edition = definition("pm")
    return edition.model_copy(update={"cutoff": edition.cutoff.replace(hour=18, minute=30)})


def test_early_pm_lake_keeps_only_dated_previous_session_context():
    data = data_bundle(definition("pm"))
    assert data['locked_observations'] == []
    assert any(d['error'] == 'stale_session' for d in data['data_diagnostics'])
    assert all(str(definition("pm").day) not in c['text'] for c in data['market_context'])


def empty_proposal():
    return BriefProposal(summary=[], observations=[], issues=[], internals=[], watchpoints=[], watch_results=[], calendar=[])


def test_index_levels_and_comparisons_are_computed_from_same_frozen_data():
    data = data_bundle()
    p, conflicts = reconcile(empty_proposal(), data)
    assert not conflicts and len(p.observations) == 2
    assert validate(p, data) == {}
    kospi = next(v for v in p.observations if v.instrument == "kospi")
    assert kospi.value == 3000 and kospi.previous_value == 2999
    assert any("20거래일" in v["text"] for v in data["market_context"])
    assert assurance(p, data) == {"kosdaq": "collected", "kospi": "collected"}
    kospi.value = Decimal("9999")
    assert validate(p, data)[kospi.id] == "collected_observation_modified"


@pytest.mark.parametrize('day', [date(2026, 9, 22), date(2026, 10, 9), date(2026, 10, 10)])
def test_morning_locks_prior_kr_close_and_renders_its_actual_session(day):
    edition = definition(day=day)
    data = data_bundle(edition)
    assert len(data["locked_observations"]) == 2
    assert {v['session_date'] for v in data['locked_observations']} == {str(edition.previous_kr_session)}
    data['exchange_closes'] = {'KR_previous': schedule.close('KR', edition.previous_kr_session).isoformat()}
    accepted, conflicts = reconcile(empty_proposal(), data)
    assert not conflicts and validate(accepted, data) == {}
    main = render(accepted, data)[0][0]
    assert f'한국 전일장 {edition.previous_kr_session:%m/%d} · 코스피' in main
    assert all(str(edition.day) not in c["text"] for c in data["market_context"])
    assert any(str(edition.previous_kr_session) in c["text"] for c in data["market_context"])


def test_holiday_gap_keeps_only_the_exact_known_previous_kr_session():
    edition = definition().model_copy(update={'day': date(2026, 10, 6),
        'cutoff': datetime(2026, 10, 6, 9, 6, tzinfo=schedule.KST),
        'previous_kr_session': date(2026, 10, 2)})
    frozen = snapshot(edition)
    derived = summarize(frozen, edition)
    data = bundle(edition)
    data.update(documents=derived['documents'], locked_observations=derived['observations'],
                exchange_closes={'KR_previous': schedule.close('KR', edition.previous_kr_session).isoformat()})
    accepted, _ = reconcile(empty_proposal(), data)
    assert len(accepted.observations) == 2 and validate(accepted, data) == {}
    assert '한국 전일장 10/02 · 코스피' in render(accepted, data)[0][0]
    data['exchange_closes'] = {}
    assert set(validate(accepted, data).values()) == {'previous_close_time_unavailable'}
    frozen['datasets']['krx_index']['rows'] = [r for r in frozen['datasets']['krx_index']['rows']
        if r['date'] != '2026-10-02']
    older = summarize(frozen, edition)
    assert older['observations'] == []
    assert any(d['error'] == 'stale_session' for d in older['diagnostics'])


def test_stale_duplicate_and_after_cutoff_data_never_become_current_quotes():
    edition = definition("pm")
    old = snapshot(edition)
    old["datasets"]["krx_index"]["rows"] = [r for r in old["datasets"]["krx_index"]["rows"] if r["date"] != str(edition.day)]
    result = summarize(old, edition)
    assert not result["observations"]
    assert any(d["error"] == "stale_session" for d in result["diagnostics"])
    duplicate = snapshot(edition)
    duplicate["datasets"]["krx_index"]["rows"].append(duplicate["datasets"]["krx_index"]["rows"][0])
    assert any(d["error"] == "duplicate_session" for d in summarize(duplicate, edition)["diagnostics"])
    old["observed_at"] = (edition.cutoff+timedelta(seconds=1)).isoformat()
    assert summarize(old, edition)["documents"] == []


def test_conflicting_news_quotes_are_withheld_but_collected_values_are_preserved():
    original = proposal()
    changed = original.observations[0].model_copy(update={"id": "conflicting", "value": Decimal("5400")})
    original.observations.append(changed)
    result, conflicts = reconcile(original, bundle())
    assert "sp500" not in {v.instrument for v in result.observations}
    assert conflicts[0]["resolution"] == "withheld"
    data = data_bundle()
    core = MarketObservation.model_validate(data["locked_observations"][0])
    core.id, core.value = "reported-kospi", Decimal("9999")
    original.observations = [core]
    result, conflicts = reconcile(original, data)
    assert next(v for v in result.observations if v.instrument == "kospi").value == 3000
    assert conflicts[0]["resolution"] == "collected_data_retained"


def test_same_publisher_feeds_do_not_count_as_independent_confirmation():
    data = bundle()
    doc = deepcopy(data["documents"][0])
    doc["id"] = "source-2"
    data["documents"].append(doc)
    p = proposal()
    p.observations[0].evidence.append(p.observations[0].evidence[0].model_copy(update={"source_id": "source-2"}))
    assert assurance(p, data)["sp500"] == "single_source"
    data["documents"][1]["publisher"] = "Independent synthetic publisher"
    assert assurance(p, data)["sp500"] == "corroborated"


def test_explicit_editorial_checks_required_and_cannot_claim_full_pass():
    with pytest.raises(ValueError):
        BriefReview.model_validate({})
    raw = review().model_dump()
    raw["checks"]["materiality"] = False
    with pytest.raises(ValueError):
        BriefReview.model_validate(raw)
    raw.update(verdict="reduce", concerns=["핵심 변화 설명이 부족함"])
    assert BriefReview.model_validate(raw).verdict == "reduce"


def test_mobile_render_keeps_summary_numbers_and_next_steps_in_main_post():
    p, data = proposal(), bundle()
    parts, quality = render(p, data)
    assert all(label in parts[0] for label in ("오늘의 핵심", "주요 숫자", "다음 확인할 것"))
    assert "[1]" in parts[0] and "Synthetic fixture" not in parts[0]
    assert len(parts[0]) < 1800 and len(parts) <= 3
    assert quality["format_version"] == 22


def test_first_visible_citation_links_from_main_even_when_watchpoints_are_built_first():
    p, data = proposal(), bundle()
    parts, _ = render(p, data)
    summary = parts[0].split("*주요 숫자*", 1)[0]
    assert f"<{data['documents'][0]['url']}|[1]>" in summary
    assert parts[0].count(f"<{data['documents'][0]['url']}|[1]>") == 1
    assert parts[0].index("*주요 숫자*") < parts[0].index("*시장 전체 흐름*")


def test_source_directory_follows_citation_numbers_instead_of_opaque_ids():
    p, data = proposal(), bundle()
    other = {**data['documents'][0], 'id': 'alphabetically-first',
             'url': 'https://example.org/synthetic-second-source'}
    data['documents'].append(other)
    p.observations[1].evidence = [p.observations[1].evidence[0].model_copy(
        update={'source_id': other['id']})]
    parts, _ = render(p, data)
    directory = '\n'.join(parts[1:]).split('*출처*', 1)[1]
    assert directory.index('[1] <') < directory.index('[2] <')


def test_issue_layout_separates_analysis_counterevidence_and_confirmation_without_losing_text():
    p = proposal()
    issue = p.issues[0]
    issue.counterpoint = p.summary[0].model_copy(update={
        'id': 'counter', 'text': '이미 확인된 반대 방향의 흐름입니다.'})
    main = render(p, bundle())[0][0]
    assert f'*1. {issue.headline}* · 당일' in main
    assert f'\n\n{issue.analysis.mechanism.text}' in main
    assert f'\n\n*다르게 볼 점* · {issue.counterpoint.text}' in main
    assert f'\n\n{issue.analysis.alternative.text}' in main
    assert f'*확인할 신호* · {issue.next_check.text}' in main
    assert main.count(issue.analysis.alternative.text) == 1


def test_data_reader_receives_no_company_or_model_credentials(monkeypatch):
    import subprocess

    monkeypatch.setenv("DATABASE_URL", "private-canary")
    monkeypatch.setenv("MODEL_RUNTIME_TOKEN", "private-canary")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "private-canary")

    def run(command, **kw):
        assert "private-canary" not in json.dumps(kw)
        assert kw["timeout"] == 45 and kw["env"]["PYTHON_DOTENV_DISABLED"] == "1"
        raise subprocess.TimeoutExpired(command, 45)

    monkeypatch.setattr(subprocess, "run", run)
    assert query("s3://example/qdata", definition())["error"] == "lake_reader_timeout_or_invalid_result"


def test_reader_subprocess_uses_verified_worker_source_instead_of_inherited_path(tmp_path, monkeypatch):
    from quant_company.briefing import data

    for directory, marker in ((tmp_path/'active', 'verified-worker'), (tmp_path/'other', 'wrong-source')):
        package = directory/'quant_company'/'briefing'
        package.mkdir(parents=True)
        (package.parent/'__init__.py').write_text('')
        (package/'__init__.py').write_text('')
        (package/'data_reader.py').write_text(
            'import json, os, sys\n'
            'request = json.load(sys.stdin)\n'
            f'print(json.dumps({{"source": {marker!r}, "edition": request["definition"]["id"], '
            '"leaked": any(k in os.environ for k in ("DATABASE_URL", "MODEL_RUNTIME_TOKEN", "SLACK_BOT_TOKEN"))}))\n')
    monkeypatch.setattr(data, '__file__', str(tmp_path/'active'/'quant_company'/'briefing'/'data.py'))
    monkeypatch.setenv('PYTHONPATH', str(tmp_path/'other'))
    monkeypatch.setenv('MODEL_RUNTIME_TOKEN', 'private-canary')
    result = query('s3://example/qdata', definition())
    assert result == {'source': 'verified-worker', 'edition': definition().id, 'leaked': False}


def test_large_dataset_is_rejected_before_any_row_loader():
    class API:
        def inspect_dataset(self, name):
            return {"source": {"size_bytes": 500000000}, "row_count": 100, "columns": []}

    edition = definition("pm")
    result = read_snapshot(edition.model_dump(), API(), clock=lambda: edition.starts_at)
    assert not result["datasets"] and len(result["errors"]) == 4
    assert all(e["error"] == "dataset_exceeds_briefing_budget" for e in result["errors"])


def test_object_changed_during_query_is_not_accepted_as_a_snapshot():
    import pandas as pd

    class API:
        inspections = 0

        def inspect_dataset(self, name):
            if name != "krx_index":
                raise FileNotFoundError(name)
            self.inspections += 1
            return {"source": {"size_bytes": 100, "etag": str(self.inspections)},
                    "row_count": 0, "columns": [], "date_bounds": {}}

        def load_krx_index(self, **kwargs):
            return pd.DataFrame(columns=["date", "index", "close", "value"])

    edition = definition("pm")
    result = read_snapshot(edition.model_dump(), API(), clock=lambda: edition.starts_at)
    assert not result["datasets"]
    assert {"dataset": "krx_index", "error": "source_changed_during_read"} in result["errors"]


def test_stale_etf_prices_are_dated_context_and_never_index_observations():
    edition = definition()
    data = snapshot(edition)
    data["datasets"] = {"prices": {"source": {"etag": "synthetic"}, "rows": [
        {"date": "2026-09-18", "series": "SPY", "value": "510"},
        {"date": "2026-09-17", "series": "SPY", "value": "500"}]}}
    result = summarize(data, edition)
    assert not result["observations"]
    assert "2026-09-18" in result["contexts"][0]["text"]
    assert "ETF" in result["contexts"][0]["text"]
    assert result["diagnostics"][0]["expected"] == "2026-09-21"


@pytest.mark.asyncio
async def test_weekend_probe_finds_next_scheduled_edition(monkeypatch):
    from datetime import datetime

    from quant_company.briefing.commands import command
    from quant_company.config import Settings

    at = datetime(2026, 9, 19, 12, tzinfo=schedule.KST)
    monkeypatch.setattr(schedule, "utcnow", lambda: at)
    monkeypatch.setattr("quant_company.briefing.data.query", lambda root, edition: {
        "ok": True, "datasets": {}, "observed_at": at.isoformat()})
    result = await command(Settings(), "data-probe")
    assert result["edition"]["day"] == "2026-09-21"


def test_long_brief_keeps_observable_next_step_in_main_post():
    p = proposal()
    p.summary = [p.summary[0].model_copy(update={"id": f"summary-{i}", "text": "시장 변화 설명. "*25})
                 for i in range(3)]
    p.internals = [p.summary[0].model_copy(update={"id": f"internal-{i}"}) for i in range(2)]
    parts, _ = render(p, bundle())
    assert p.watchpoints[0].text in parts[0]
    assert parts[0].index("오늘의 핵심") < parts[0].index("다음 확인할 것")
    assert all(len(part) <= 3500 for part in parts)


def test_model_prompt_uses_verified_data_summary_without_raw_table_duplication():
    data = data_bundle()
    raw = prompt(data, "write")
    payload = json.loads(raw.split("BRIEF DATA JSON:\n")[1])
    assert "rows" not in payload["documents"][0]["receipt"]
    assert payload["locked_observations"]


def test_data_freezes_with_edition_and_late_collection_cannot_replace_it(brief):  # noqa: F811
    store, clock = brief
    edition = definition("am")
    clock["at"] = edition.cutoff-timedelta(minutes=10)
    claim = store.claim_data()
    data = summarize(snapshot(edition), edition)
    store.save_data(claim, data)
    seed(brief, edition)
    request = store.prepare()["request"]
    payload = json.loads(request["prompt"].split("BRIEF DATA JSON:\n")[1])
    assert len(payload["locked_observations"]) == 2
    store.save_data(claim, {**data, "observations": []})
    with store.db.transaction() as conn:
        row = conn.execute("SELECT * FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()
        assert len(row["bundle"]["locked_observations"]) == 2
        assert len(row["market_data"]["observations"]) == 2


def test_saved_input_replay_and_five_day_qualification_do_not_fake_live_acceptance(brief):  # noqa: F811
    store, clock = brief
    edition, _, _ = complete(brief)
    with store.db.transaction() as conn:
        row = conn.execute("SELECT * FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()
    assert replay(row)["ok"]
    row["rendered"][0] += "changed"
    assert not replay(row)["ok"]
    result = qualify(store.company, at=definition("pm").due_at+timedelta(minutes=15))
    assert len(result["completed_trading_days"]) == 5
    assert not result["ready_for_slack_acceptance"] and not result["publishing_changed"]
    check = next(c for c in result["editions"] if c["id"] == edition.id)
    assert "real_codex_not_verified" in check["reasons"]
    assert check["observed"]["committed_at"] and check["observed"]["delay_seconds"] is not None
    assert set(check["observed"]["core_quote_assurance"]) == {"sp500", "nasdaq"}
    store.company.settings.briefing_search_enabled = not store.company.settings.briefing_search_enabled
    changed = qualify(store.company, at=definition("pm").due_at+timedelta(minutes=15))
    changed_check = next(c for c in changed["editions"] if c["id"] == edition.id)
    assert "editorial_policy_changed" in changed_check["reasons"]
    with store.db.transaction() as conn:
        conn.execute("UPDATE brief_editions SET committed_at=%s WHERE id=%s",
                     (edition.due_at+timedelta(minutes=11), edition.id))
    late = qualify(store.company, at=definition("pm").due_at+timedelta(minutes=15))
    late_check = next(c for c in late["editions"] if c["id"] == edition.id)
    assert "late_brief" in late_check["reasons"] and late_check["observed"]["delay_seconds"] == 660


def test_withheld_editorial_review_is_corrected_and_ships_a_reduced_body(brief):  # noqa: F811
    store, clock = brief
    edition = seed(brief)
    store.commit(response(store.prepare()["request"]))
    verdict = review().model_copy(update={"verdict": "withhold", "concerns": ["중심 주장의 근거 부족"]})
    store.commit(response(store.prepare()["request"], verdict))
    # The finding goes to the one correction instead of replacing the edition with a notice.
    revise = store.prepare()["request"]
    assert revise["request_id"].endswith("-revise")
    store.commit(response(revise))
    final = store.prepare()["request"]
    assert final["request_id"].endswith("-final_review")
    store.commit(response(final, verdict))  # The critic still withholds after the correction.
    clock["at"] = edition.due_at
    store.flush()
    row = next(r for r in store.status(include_rendered=True)["editions"] if r["id"] == edition.id)
    assert row["state"] == "committed" and row["quality"]["fallback"] is None
    assert row["quality"]["review_withheld"] and row["quality"]["reduced"]
    assert "미국 증시는 반도체가 주도했으며" in row["rendered"][0] and "일부 확인 중" in row["rendered"][0]


def test_reduce_without_ids_and_failed_checks_ships_body_when_correction_cannot_fit(brief, monkeypatch):  # noqa: F811
    """2026-10-10 AM: reduce with no rejected IDs and failed validity checks nulled the body, and the
    correction was skipped because its context did not fit; only the deadline notice went out."""
    from quant_company.briefing import store as store_module

    store, clock = brief
    edition = seed(brief)
    store.commit(response(store.prepare()["request"]))
    verdict = review().model_dump()
    verdict.update(verdict="reduce", rejected_ids=[], concerns=["시점과 반대 근거 확인이 부족합니다."])
    for check in ("timing", "sources", "alternatives", "counterevidence"):
        verdict["checks"][check] = False
    original = store_module.prompt

    def limited(bundle, phase, *args, **kwargs):
        if phase == "revise":
            raise ValueError("brief_context_limit")
        return original(bundle, phase, *args, **kwargs)

    monkeypatch.setattr(store_module, "prompt", limited)
    store.commit(response(store.prepare()["request"], BriefReview.model_validate(verdict)))
    clock["at"] = edition.due_at
    store.flush()
    row = next(r for r in store.status(include_rendered=True)["editions"] if r["id"] == edition.id)
    assert row["state"] == "committed" and row["quality"]["fallback"] is None
    assert row["quality"]["review_withheld"] and row["quality"]["repair_skipped"] == "brief_context_limit"
    assert "미국 증시는 반도체가 주도했으며" in row["rendered"][0]
    # A body the critic did not pass is never the next edition's evidence.
    with store.db.transaction() as conn:
        conn.execute("UPDATE outbox SET status='delivered',sent_ts='1.0'")  # The AM main post reached Slack.
        saved = conn.execute("SELECT * FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()
        later = {**saved, "kind": "pm", "bundle": None, "market_data": None,
                 "due_at": saved["due_at"]+timedelta(hours=12)}
        assert store._freeze(conn, later)["morning_watchpoints"] == []
        assert store._freeze(conn, later)["previous_briefs_context_only"] == []
        conn.execute("""UPDATE brief_editions SET quality=quality||'{"review_withheld": false}' WHERE id=%s""",
                     (edition.id,))
        assert store._freeze(conn, later)["morning_watchpoints"] and store._freeze(conn, later)["previous_briefs_context_only"]


def test_thread_detail_overflow_is_truncated_instead_of_failing_the_edition():
    b = bundle()
    b["market_context"] = [{"source_id": "source-1", "text": f"비교 {i} " + "가"*1600} for i in range(10)]
    parts, quality = render(proposal(), b)
    assert len(parts) == 5 and all(len(part) <= 3500 for part in parts[1:])
    assert parts[-1].endswith("일부 상세 항목은 길이 제한으로 생략했습니다. 발간 기록에 보존합니다.")
    assert quality["details_truncated"]


def test_publishable_drops_trailing_issues_instead_of_failing_on_size(monkeypatch):
    from quant_company.briefing import editor

    real = editor.render

    def sized(value, b, **kwargs):
        if value and value.issues:
            raise ValueError("brief_message_size_limit")
        return real(value, b, **kwargs)

    monkeypatch.setattr(editor, "render", sized)
    parts, quality, shipped = editor.publishable(proposal(), bundle())
    assert shipped.issues == [] and quality["size_dropped_issues"] == ["fact"] and quality["reduced"]
    assert "미국 증시는 반도체가 주도했으며" in parts[0]
