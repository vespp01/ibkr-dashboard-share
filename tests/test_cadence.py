"""Cadence pure-function tests — cron compile, send-filter, biweekly parity."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from ibkr_backend.notify import cadence as cad


def test_cadence_to_cron_variants():
    assert cad.cadence_to_cron({"freq": "off"}) is None
    assert cad.cadence_to_cron({"freq": "daily", "time": "06:30"}) == "30 6 * * *"
    assert cad.cadence_to_cron({"freq": "weekly", "weekday": 2, "time": "08:00"}) == "0 8 * * wed"
    assert cad.cadence_to_cron({"freq": "biweekly", "weekday": 0, "time": "09:15"}) == "15 9 * * mon"
    assert cad.cadence_to_cron({"freq": "monthly", "day_of_month": 15, "time": "07:00"}) == "0 7 15 * *"


def test_cadence_to_cron_defaults_time_to_08():
    assert cad.cadence_to_cron({"freq": "weekly", "weekday": 1}) == "0 8 * * tue"


def test_cadence_allows_daily_weekly():
    monday = date(2026, 8, 10)  # Monday → weekday 0
    assert cad.cadence_allows({"freq": "daily"}, monday) is True
    assert cad.cadence_allows({"freq": "off"}, monday) is False
    assert cad.cadence_allows({"freq": "weekly", "weekday": 0}, monday) is True
    assert cad.cadence_allows({"freq": "weekly", "weekday": 1}, monday) is False


def test_biweekly_exactly_one_of_two_consecutive_weeks():
    monday = date(2026, 8, 10)
    next_monday = date(2026, 8, 17)
    a = cad.cadence_allows({"freq": "biweekly", "weekday": 0}, monday)
    b = cad.cadence_allows({"freq": "biweekly", "weekday": 0}, next_monday)
    assert a != b


def test_is_active_iso_week_alternates():
    assert cad.is_active_iso_week(date(2026, 8, 10)) != cad.is_active_iso_week(date(2026, 8, 17))


def test_monthly_allows_on_target_day_only():
    assert cad.cadence_allows({"freq": "monthly", "day_of_month": 10}, date(2026, 8, 10)) is True
    assert cad.cadence_allows({"freq": "monthly", "day_of_month": 11}, date(2026, 8, 10)) is False


def test_cron_to_cadence_weekly_roundtrip():
    c = cad.cron_to_cadence("0 8 * * 1")
    assert c["freq"] == "weekly"
    assert c["weekday"] == 1
    assert c["time"] == "08:00"


def test_cron_to_cadence_interval_falls_back_daily():
    assert cad.cron_to_cadence("*/15 * * * 1-5")["freq"] == "daily"
    assert cad.cron_to_cadence("0 */6 * * *")["freq"] == "daily"


def test_normalize_clamps_out_of_range():
    c = cad.normalize_cadence(
        {"freq": "weekly", "weekday": 99, "day_of_month": 99, "time": "bad"}
    )
    assert c["weekday"] == 6
    assert c["time"] is None
    assert cad.normalize_cadence({"freq": "monthly", "day_of_month": 40})["day_of_month"] == 28


def test_from_crontab_weekday_convention_matches_cadence():
    """Pin the empirically-verified contract: APScheduler from_crontab treats the
    day-of-week field as 0=Monday..6=Sunday — the same convention cadence emits."""
    pytest.importorskip("apscheduler")
    from apscheduler.triggers.cron import CronTrigger

    base = datetime(2026, 8, 9)  # Sunday
    cron = cad.cadence_to_cron({"freq": "weekly", "weekday": 0, "time": "08:00"})  # Monday
    nxt = CronTrigger.from_crontab(cron).get_next_fire_time(None, base)
    assert nxt.weekday() == 0  # Monday


def test_cron_to_cadence_reads_day_names():
    c = cad.cron_to_cadence("0 8 * * mon")
    assert (c["freq"], c["weekday"], c["time"]) == ("weekly", 0, "08:00")
    assert cad.cron_to_cadence("30 7 * * sun")["weekday"] == 6
    assert cad.cron_to_cadence("*/15 * * * mon-fri")["freq"] == "daily"  # interval-style


def test_default_crons_fire_on_the_intended_weekdays():
    """B45: APScheduler day-of-week is 0=Mon; the env defaults must use names."""
    from datetime import timedelta

    from apscheduler.triggers.cron import CronTrigger

    from ibkr_backend.config import Settings

    s = Settings()
    fx = CronTrigger.from_crontab(s.margin_fx_cron)
    nxt = fx.get_next_fire_time(None, datetime(2026, 10, 6))  # Tuesday
    assert nxt.weekday() == 0  # next Monday, not Tuesday
    # price/margin alert crons: weekdays only, Monday included, no Saturday
    trig = CronTrigger.from_crontab(s.price_alert_cron)
    seen = set()
    cur = datetime(2026, 10, 5)  # Monday 00:00
    for _ in range(7):
        n = trig.get_next_fire_time(None, cur)
        seen.add(n.weekday())
        cur = datetime(n.year, n.month, n.day) + timedelta(days=1)
    assert seen == {0, 1, 2, 3, 4}


def test_cadence_due_weekly_survives_a_failed_cadence_day():
    wk = {"freq": "weekly", "weekday": 0}
    monday, tuesday = date(2026, 10, 5), date(2026, 10, 6)
    assert cad.cadence_due(wk, monday, None) is True
    # Monday's pull failed, nothing sent: Tuesday still delivers it (B44).
    assert cad.cadence_due(wk, tuesday, None) is True
    # Already sent this ISO week: not again.
    assert cad.cadence_due(wk, tuesday, monday) is False
    # Next ISO week it is due again.
    assert cad.cadence_due(wk, date(2026, 10, 12), monday) is True
    # Slot not arrived yet (Wednesday cadence, Monday today).
    assert cad.cadence_due({"freq": "weekly", "weekday": 2}, monday, None) is False
    assert cad.cadence_due({"freq": "weekly", "weekday": 2}, date(2026, 10, 8), None) is True


def test_cadence_due_biweekly_and_monthly_and_daily_off():
    bi = {"freq": "biweekly", "weekday": 0}
    active = next(d for d in (date(2026, 8, 10), date(2026, 8, 17)) if cad.is_active_iso_week(d))
    inactive = next(d for d in (date(2026, 8, 10), date(2026, 8, 17)) if not cad.is_active_iso_week(d))
    assert cad.cadence_due(bi, active, None) is True
    assert cad.cadence_due(bi, inactive, None) is False
    mo = {"freq": "monthly", "day_of_month": 5}
    assert cad.cadence_due(mo, date(2026, 10, 4), None) is False
    assert cad.cadence_due(mo, date(2026, 10, 7), None) is True  # 5th missed → still due
    assert cad.cadence_due(mo, date(2026, 10, 7), date(2026, 10, 5)) is False
    assert cad.cadence_due(mo, date(2026, 11, 5), date(2026, 10, 5)) is True
    assert cad.cadence_due({"freq": "daily"}, date(2026, 10, 7), date(2026, 10, 7)) is True
    assert cad.cadence_due({"freq": "off"}, date(2026, 10, 7), None) is False
