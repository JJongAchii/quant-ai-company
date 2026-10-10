from datetime import UTC, date, datetime, timedelta

import pytest

from quant_company.briefing import schedule
from quant_company.briefing.contracts import BriefEdition

from .test_briefing import DAY, brief, bundle, response  # noqa: F401

# Days covering EDT/EST, both 2026/2027 US DST transitions, an NYSE holiday, an early close,
# Friday->Saturday KST and a Monday outlook.
DAYS = [date(2026, 9, 22), date(2026, 12, 2), date(2026, 10, 31), date(2026, 11, 2), date(2026, 11, 3),
        date(2027, 3, 13), date(2027, 3, 15), date(2027, 3, 16), date(2026, 11, 27), date(2026, 11, 28)]


def am(day, on=True):
    return next(e for e in schedule.editions(day, "C", "U", us_close_anchor=on) if e.kind == "am")


def local(value, zone):
    return value.astimezone(zone).strftime("%m-%d %H:%M")


def times(edition, zone):
    return [local(edition.starts_at, zone), local(edition.cutoff, zone), local(edition.due_at, zone)]


class Flag:
    def __init__(self, on):
        self.briefing_us_close_enabled = on


def test_schedule_versions_follow_the_switch():
    assert schedule.SCHEDULE_VERSION == 6 and schedule.US_CLOSE_SCHEDULE_VERSION == 7
    assert schedule.version(Flag(False)) == 6 and schedule.version(Flag(True)) == 7


@pytest.mark.parametrize("day", DAYS)
def test_switch_off_keeps_the_v6_0745_kst_schedule(day):
    e = am(day, on=False)
    assert times(e, schedule.KST) == [day.strftime("%m-%d ")+t for t in ("06:10", "06:30", "07:45")]
    assert e.model_dump() == next(x for x in schedule.editions(day, "C", "U") if x.kind == "am").model_dump()
    assert "cutoff_extended" not in e.model_dump(mode="json")
    for kind in ("am", "pm"):
        off = [x for x in schedule.editions(day, "C", "U") if x.kind == kind]
        on = [x for x in schedule.editions(day, "C", "U", us_close_anchor=True) if x.kind == kind]
        assert [x.id for x in off] == [x.id for x in on]
        if kind == "pm" or not off[0].us_session:
            assert off == on


def test_switch_on_normal_edt_close():
    e = am(date(2026, 9, 22))
    assert e.us_session == date(2026, 9, 21)
    assert times(e, schedule.NY) == ["09-21 16:20", "09-21 16:40", "09-21 17:55"]
    assert times(e, schedule.KST) == ["09-22 05:20", "09-22 05:40", "09-22 06:55"]
    assert e.expires_at == e.due_at+timedelta(hours=1)
    assert not e.cutoff_extended and e.cutoff_extension is None


def test_switch_on_normal_est_close():
    e = am(date(2026, 12, 2))
    assert e.us_session == date(2026, 12, 1)
    assert times(e, schedule.NY) == ["12-01 16:20", "12-01 16:40", "12-01 17:55"]
    assert times(e, schedule.KST) == ["12-02 06:20", "12-02 06:40", "12-02 07:55"]


@pytest.mark.parametrize(("day", "session", "kst_due"), [
    # US daylight time ends on 2026-11-01: Friday 10-30 is EDT, Monday 11-02 is EST.
    (date(2026, 10, 31), date(2026, 10, 30), "10-31 06:55"),
    (date(2026, 11, 3), date(2026, 11, 2), "11-03 07:55"),
    # US daylight time starts on 2027-03-14: Friday 03-12 is EST, Monday 03-15 is EDT.
    (date(2027, 3, 13), date(2027, 3, 12), "03-13 07:55"),
    (date(2027, 3, 16), date(2027, 3, 15), "03-16 06:55"),
])
def test_switch_on_dst_transitions_keep_the_new_york_anchor(day, session, kst_due):
    e = am(day)
    assert e.us_session == session
    assert local(e.due_at, schedule.KST) == kst_due
    assert local(e.due_at, schedule.NY)[-5:] == "17:55"


@pytest.mark.parametrize("day", [date(2026, 11, 2), date(2027, 3, 15)])
def test_switch_on_monday_outlook_without_us_session_keeps_0745_kst(day):
    e = am(day)
    assert e.us_session is None and e.weekly == "outlook"
    assert times(e, schedule.KST) == [day.strftime("%m-%d ")+t for t in ("06:10", "06:30", "07:45")]


def test_switch_on_nyse_holiday_uses_fallback_anchor():
    # Thanksgiving 2026-11-26: Friday KST has a Korean session but no new US result.
    e = am(date(2026, 11, 27))
    assert e.us_session is None and e.kr_session == date(2026, 11, 27)
    assert times(e, schedule.KST) == ["11-27 06:10", "11-27 06:30", "11-27 07:45"]


def test_switch_on_early_close_and_friday_session_belongs_to_saturday_kst():
    e = am(date(2026, 11, 28))
    assert e.day.weekday() == 5 and e.weekly == "review"
    assert e.us_session == date(2026, 11, 27) and e.previous_us_session == date(2026, 11, 25)
    assert times(e, schedule.NY) == ["11-27 13:20", "11-27 13:40", "11-27 14:55"]
    assert times(e, schedule.KST) == ["11-28 03:20", "11-28 03:40", "11-28 04:55"]
    # Friday KST still reports Thursday's US session, never Friday's.
    assert am(date(2026, 10, 30)).us_session == date(2026, 10, 29)
    assert am(date(2026, 10, 31)).us_session == date(2026, 10, 30)
    assert am(date(2026, 11, 28), on=False).us_session == date(2026, 11, 27)


def test_extension_is_applied_once():
    e = am(date(2026, 9, 22))
    extended = schedule.extend_cutoff(e.model_dump(mode="json"), "no_us_close_report_at_cutoff")
    assert extended.cutoff-e.cutoff == extended.due_at-e.due_at == extended.expires_at-e.expires_at == timedelta(minutes=30)
    assert extended.cutoff_extended and extended.cutoff_extension["original_cutoff"] == e.cutoff.isoformat()
    assert extended.id == e.id and extended.day == e.day
    with pytest.raises(ValueError, match="cutoff_already_extended"):
        schedule.extend_cutoff(extended.model_dump(mode="json"), "again")


def stored(store, edition_id):
    with store.db.transaction() as conn:
        return conn.execute("SELECT * FROM brief_editions WHERE id=%s", (edition_id,)).fetchone()


def close_report(edition, title="Synthetic S&P 500 closing report"):
    value = bundle(edition)
    value["documents"][0]["title"] = title
    return value


def seeded(brief, edition, documents):  # noqa: F811
    store, clock = brief
    clock["at"] = edition.cutoff-timedelta(minutes=1)
    store.register()
    claimed = store.claim_collection()
    assert str(claimed["id"]) == edition.id
    store.save_collection(claimed, documents)
    clock["at"] = edition.cutoff
    return edition


@pytest.fixture
def switched(brief):  # noqa: F811
    store, clock = brief
    store.company.settings.briefing_us_close_enabled = True
    return store, clock


def v7(kind="am"):
    return next(e for e in schedule.editions(DAY, "CQUANT", "UHUMAN", us_close_anchor=True) if e.kind == kind)


def v6(kind="am"):
    return next(e for e in schedule.editions(DAY, "CQUANT", "UHUMAN") if e.kind == kind)


def test_real_postgres_switch_off_registers_v6_and_never_extends(brief):  # noqa: F811
    store, clock = brief
    edition = seeded(brief, v6(), close_report(v6(), "Synthetic technology sector notes"))
    row = stored(store, edition.id)
    assert local(row["due_at"], schedule.KST)[-5:] == "07:45" and local(row["cutoff"], schedule.KST)[-5:] == "06:30"
    assert "cutoff_extended" not in row["definition"]
    request = store.prepare()
    assert request["state"] == "ready" and request["request"]["request_id"].endswith("-write")
    assert stored(store, edition.id)["cutoff"] == edition.cutoff


def test_real_postgres_switch_off_ready_am_waits_for_due(brief):  # noqa: F811
    store, clock = brief
    edition = seeded(brief, v6(), close_report(v6()))
    store.commit(response(store.prepare()["request"]))
    store.commit(response(store.prepare()["request"]))
    assert stored(store, edition.id)["state"] == "ready"
    clock["at"] = edition.due_at-timedelta(minutes=1)
    assert store.flush()["committed"] == 0
    clock["at"] = edition.due_at
    assert store.flush()["committed"] == 1


def test_real_postgres_switch_changes_the_policy_digest_only_when_on(brief):  # noqa: F811
    store, _ = brief
    off = store.policy()
    store.company.settings.briefing_us_close_enabled = True
    assert store.policy() != off
    store.company.settings.briefing_us_close_enabled = False
    assert store.policy() == off


def test_real_postgres_cutoff_extends_once_without_us_close_report(switched):
    store, clock = switched
    edition = v7()
    clock["at"] = edition.cutoff-timedelta(minutes=1)
    store.register()
    claimed = store.claim_collection()
    store.save_collection(claimed, close_report(edition, "Synthetic technology sector notes"))
    clock["at"] = edition.cutoff
    assert store.prepare() == {"state": "idle", "reason": "cutoff_extended"}
    row = stored(store, edition.id)
    shift = timedelta(minutes=30)
    assert (row["cutoff"], row["due_at"], row["expires_at"]) == (
        edition.cutoff+shift, edition.due_at+shift, edition.expires_at+shift)
    frozen = BriefEdition.model_validate(row["definition"])
    assert frozen.cutoff == row["cutoff"] and frozen.cutoff_extended
    assert frozen.cutoff_extension["reason"] == "no_us_close_report_at_cutoff"
    # Collection continues until the extended cutoff, then the edition freezes without another extension.
    clock["at"] = edition.cutoff+timedelta(minutes=5)
    assert store.claim_collection()["id"] == row["id"]
    clock["at"] = frozen.cutoff
    request = store.prepare()
    assert request["state"] == "ready" and request["request"]["request_id"].endswith("-write")
    row = stored(store, edition.id)
    assert row["cutoff"] == frozen.cutoff and row["bundle"]["edition"]["cutoff_extended"]
    with store.db.transaction() as conn:
        events = conn.execute("SELECT count(*) AS n FROM events WHERE kind='briefing_cutoff_extended'").fetchone()
    assert events["n"] == 1


def test_real_postgres_close_report_prevents_extension(switched):
    store, clock = switched
    edition = seeded(switched, v7(), close_report(v7()))
    assert store.prepare()["state"] == "ready"
    assert "cutoff_extended" not in stored(store, edition.id)["definition"]


def test_real_postgres_ready_am_is_sent_before_due_but_never_before_cutoff(switched):
    store, clock = switched
    edition = seeded(switched, v7(), close_report(v7()))
    store.commit(response(store.prepare()["request"]))
    store.commit(response(store.prepare()["request"]))
    assert stored(store, edition.id)["state"] == "ready"
    clock["at"] = edition.cutoff-timedelta(minutes=1)
    assert store.flush()["committed"] == 0
    clock["at"] = edition.cutoff+timedelta(minutes=3)
    assert clock["at"] < edition.due_at
    assert store.flush()["committed"] == 1
    row = stored(store, edition.id)
    assert row["state"] == "committed" and row["committed_at"] < edition.due_at


def test_real_postgres_switch_on_ready_pm_still_waits_for_due(switched):
    store, clock = switched
    store.company.settings.briefing_max_revisions = 0
    edition = seeded(switched, v7("pm"), close_report(v7("pm")))
    assert edition == v6("pm")
    store.commit(response(store.prepare()["request"]))
    store.commit(response(store.prepare()["request"]))
    assert stored(store, edition.id)["state"] == "ready"
    assert store.flush()["committed"] == 0
    clock["at"] = edition.due_at
    assert store.flush()["committed"] == 1


def test_real_postgres_switch_on_deadline_fallback_stays_at_due_plus_ten_minutes(switched):
    store, clock = switched
    edition = seeded(switched, v7(), close_report(v7()))
    clock["at"] = edition.due_at+timedelta(minutes=9)
    store.flush()
    assert stored(store, edition.id)["committed_at"] is None
    clock["at"] = edition.due_at+timedelta(minutes=10)
    assert store.flush()["committed"] == 1


def test_observation_timer_adds_new_york_entries_and_keeps_utc_entries():
    from pathlib import Path

    timer = (Path(__file__).parents[1]/"deploy/quant-company-analyst-monitor.timer").read_text()
    entries = [line.split("=", 1)[1] for line in timer.splitlines() if line.startswith("OnCalendar=")]
    assert entries == ["Mon..Fri 18:08:00 America/New_York", "Mon..Fri 18:38:00 America/New_York",
                       "*-*-* 22:58:00 UTC", "*-*-* 08:38:00 UTC", "*-*-* 08:58:00 UTC", "*-*-* 09:58:00 UTC"]
    # 18:08 ET is 13 minutes after the switched-on 17:55 ET due; 22:58 UTC follows 07:45 KST.
    due = am(date(2026, 9, 22)).due_at.astimezone(schedule.NY)
    observed = datetime.combine(due.date(), datetime.min.time().replace(hour=18, minute=8), schedule.NY)
    assert observed-due == timedelta(minutes=13)
    assert am(date(2026, 9, 22), on=False).due_at.astimezone(UTC).strftime("%H:%M") == "22:45"


def qualification_check(store, edition):
    from quant_company.briefing.qualification import qualify

    report = qualify(store.company, at=v6("pm").due_at+timedelta(minutes=15))
    return next(c for c in report["editions"] if c["id"] == edition.id)


def completed(brief, edition):  # noqa: F811
    store, clock = brief
    seeded(brief, edition, close_report(edition))
    store.commit(response(store.prepare()["request"]))
    store.commit(response(store.prepare()["request"]))
    clock["at"] = edition.due_at
    store.flush()
    return edition


@pytest.mark.parametrize("on", [False, True])
def test_real_postgres_late_brief_and_core_quotes_follow_the_due(brief, on):  # noqa: F811
    store, _ = brief
    store.company.settings.briefing_us_close_enabled = on
    edition = completed(brief, v7() if on else v6())
    check = qualification_check(store, edition)
    assert datetime.fromisoformat(check["observed"]["due_at"]) == edition.due_at
    assert local(edition.due_at, schedule.NY if on else schedule.KST)[-5:] == ("17:55" if on else "07:45")
    assert "late_brief" not in check["reasons"]
    assert set(check["observed"]["core_quote_assurance"]) == {"sp500", "nasdaq"}
    for minutes, late in ((10, False), (11, True)):
        with store.db.transaction() as conn:
            conn.execute("UPDATE brief_editions SET committed_at=%s WHERE id=%s",
                         (edition.due_at+timedelta(minutes=minutes), edition.id))
        assert ("late_brief" in qualification_check(store, edition)["reasons"]) is late


def test_real_postgres_late_brief_follows_a_recorded_extended_due(switched):
    from psycopg.types.json import Jsonb

    store, _ = switched
    edition = completed(switched, v7())
    extended = schedule.extend_cutoff(edition.model_dump(mode="json"), "no_us_close_report_at_cutoff")
    with store.db.transaction() as conn:
        conn.execute("UPDATE brief_editions SET definition=%s,due_at=%s,cutoff=%s,expires_at=%s,committed_at=%s WHERE id=%s",
                     (Jsonb(extended.model_dump(mode="json")), extended.due_at, extended.cutoff, extended.expires_at,
                      edition.due_at+timedelta(minutes=35), edition.id))
    check = qualification_check(store, edition)
    # 35 minutes after the original due is 5 minutes after the extended one.
    assert datetime.fromisoformat(check["observed"]["due_at"]) == extended.due_at
    assert check["observed"]["delay_seconds"] == 300 and "late_brief" not in check["reasons"]
    assert set(check["observed"]["core_quote_assurance"]) == {"sp500", "nasdaq"}
    with store.db.transaction() as conn:
        conn.execute("UPDATE brief_editions SET committed_at=%s WHERE id=%s",
                     (extended.due_at+timedelta(minutes=11), edition.id))
    assert "late_brief" in qualification_check(store, edition)["reasons"]


def load_monitor():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    spec = spec_from_file_location("analyst_monitor_v7", Path(__file__).parents[1]/"deploy/analyst_brief_monitor.py")
    monitor = module_from_spec(spec)
    spec.loader.exec_module(monitor)
    return monitor


def stamp(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def test_monitor_reports_an_extended_edition_in_progress_at_1808_and_judges_it_at_1838():
    monitor = load_monitor()
    e = am(date(2026, 9, 22))
    extended = schedule.extend_cutoff(e.model_dump(mode="json"), "no_us_close_report_at_cutoff")
    row = {"state": "writing", "publish": False, "policy_digest": "fixture", "calls": [],
           "definition": extended.model_dump(mode="json"), "due_at": extended.due_at.isoformat()}
    first = datetime.combine(e.due_at.astimezone(schedule.NY).date(), datetime.min.time(), schedule.NY)
    at_1808, at_1838 = first.replace(hour=18, minute=8), first.replace(hour=18, minute=38)
    early = monitor.assess(e.model_dump(mode="json"), row, at_1808)
    assert early["observation"] == "in_progress" and early["problems"] == [] and early["cutoff_extended"]
    assert stamp(early["due_at"]) == extended.due_at
    late = monitor.assess(e.model_dump(mode="json"), row, at_1838)
    assert late["observation"] == "final" and "edition_not_finalized" in late["problems"]
    row.update(state="previewed", committed_at=(extended.due_at+timedelta(minutes=5)).isoformat())
    done = monitor.assess(e.model_dump(mode="json"), row, at_1838)
    assert "late_brief" not in done["problems"] and done["delay_seconds"] == 300
    # An unextended edition is judged at 18:08 against its own due + 10 minutes.
    plain = {**row, "definition": e.model_dump(mode="json"), "due_at": e.due_at.isoformat(), "committed_at": None}
    assert "edition_not_finalized" in monitor.assess(e.model_dump(mode="json"), plain, at_1808)["problems"]


def test_monitor_with_the_switch_off_is_in_progress_until_the_0745_due_plus_ten():
    monitor = load_monitor()
    e = am(date(2026, 9, 22), on=False)
    row = {"state": "writing", "publish": False, "policy_digest": "fixture", "calls": [],
           "definition": e.model_dump(mode="json"), "due_at": e.due_at.isoformat()}
    # The 18:08/18:38 ET runs (07:08/07:38 KST) come before the 07:45 KST due: in progress, no problem.
    for minute in (8, 38):
        run = datetime.combine(date(2026, 9, 21), datetime.min.time(), schedule.NY).replace(hour=18, minute=minute)
        result = monitor.assess(e.model_dump(mode="json"), row, run)
        assert result["observation"] == "in_progress" and result["problems"] == []
    # The 22:58 UTC (07:58 KST) run judges it.
    final = monitor.assess(e.model_dump(mode="json"), row, e.due_at+timedelta(minutes=13))
    assert final["observation"] == "final" and "edition_not_finalized" in final["problems"]
