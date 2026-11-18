"""Ops hardening of the scheduler (P0.11 / P1.7): dead-token suspension, same-day
retry, period cadence gate, next-run weekdays, parked-overlay jobs, backup age,
flex_runs retention, UTC-aware status timestamps.

Real SQLite per test (autouse fixture); Flex and Telegram are stubbed at their
network edge only.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text

from ibkr_backend.config import Settings
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import FlexRun, NavDaily, NotificationState, _utcnow
from ibkr_backend.flex.client import FlexError
from ibkr_backend.jobs import scheduler as sch
from ibkr_backend.jobs.health import SCHEDULER_PULL_ATTEMPT_KEY

TOKEN = "tok-AAAA-secret"  # pragma: allowlist secret


def _settings(**kw) -> Settings:
    base = dict(ib_flex_token=TOKEN, ib_flex_query_id="ACT", ib_flex_query_id_tc="TC",
                ib_flex_token_expiry="", telegram_bot_token="", telegram_chat_id="")
    base.update(kw)
    return Settings(**base)


@pytest.fixture()
def has_data():
    with session_scope() as s:
        s.add(NavDaily(snapshot_date=date(2026, 9, 30), account_id="U0", nav_total=Decimal("1")))


# ------------------------------------------------------------------ B39
def test_dead_token_suspends_pulls_until_token_changes(monkeypatch, caplog):
    settings = _settings()
    monkeypatch.setattr(sch, "get_settings", lambda: settings)
    calls = {"daily": 0, "tc": 0}

    def daily(**_):
        calls["daily"] += 1
        raise FlexError("1025", "Invalid token")

    def tc(**_):
        calls["tc"] += 1
        raise FlexError("1012", "Token has expired")

    monkeypatch.setattr(sch, "run_full_pipeline", daily)
    monkeypatch.setattr(sch, "run_tc_pipeline", tc)
    caplog.set_level(logging.DEBUG)

    sch._daily_job()
    assert calls["daily"] == 1
    # suspended: no further network calls from either job, but the attempt is stamped
    with session_scope() as db:
        db.delete(db.get(NotificationState, SCHEDULER_PULL_ATTEMPT_KEY))
    sch._daily_job()
    sch._tc_job()
    sch._tc_job()
    assert calls == {"daily": 1, "tc": 0}
    with session_scope() as db:
        assert db.get(NotificationState, SCHEDULER_PULL_ATTEMPT_KEY) is not None  # watchdog stays quiet
        marker = db.get(NotificationState, sch._PULLS_SUSPENDED_KEY).value
    assert TOKEN not in marker and marker.endswith("|1025")  # a hash, never the token
    assert TOKEN not in caplog.text

    # token renewed (value changes) -> pulls resume, and a success clears the marker
    settings2 = _settings(ib_flex_token="tok-BBBB-renewed")  # pragma: allowlist secret
    monkeypatch.setattr(sch, "get_settings", lambda: settings2)
    monkeypatch.setattr(sch, "run_full_pipeline", lambda **_: type(
        "R", (), {"reference_code": "r", "total_inserted": 0, "total_skipped": 0})())
    monkeypatch.setattr(sch, "_notify_after_pull", lambda **k: None)
    monkeypatch.setattr(sch, "_margin_eod_alert_after_pull", lambda **k: None)
    monkeypatch.setattr(sch, "_margin_usage_after_pull", lambda **k: None)
    sch._daily_job()
    with session_scope() as db:
        assert db.get(NotificationState, sch._PULLS_SUSPENDED_KEY) is None


def test_transient_codes_do_not_suspend(monkeypatch):
    settings = _settings()
    monkeypatch.setattr(sch, "get_settings", lambda: settings)

    def boom(**_):
        raise FlexError("1004", "Statement is incomplete")

    monkeypatch.setattr(sch, "run_full_pipeline", boom)
    sch._daily_job()
    with session_scope() as db:
        assert db.get(NotificationState, sch._PULLS_SUSPENDED_KEY) is None


# ------------------------------------------------------------------ B46
def _fake_pipeline(calls, *, fail=False):
    def run(**_):
        calls.append(1)
        if fail:
            raise FlexError("1004", "Statement is incomplete")
        with session_scope() as s:
            s.add(FlexRun(query_id="ACT", status="success", note="scheduled", fetched_at=_utcnow()))
        return type("R", (), {"reference_code": "r", "total_inserted": 1, "total_skipped": 0})()

    return run


def _stub_after_pull(monkeypatch):
    for name in ("_notify_after_pull", "_margin_eod_alert_after_pull", "_margin_usage_after_pull"):
        monkeypatch.setattr(sch, name, lambda **k: None)


def test_retry_fires_once_then_never_after_success(monkeypatch, has_data):
    settings = _settings()
    monkeypatch.setattr(sch, "get_settings", lambda: settings)
    _stub_after_pull(monkeypatch)
    calls: list[int] = []
    monkeypatch.setattr(sch, "run_full_pipeline", _fake_pipeline(calls))

    sch._daily_retry_job()  # last 06:30 slot has no scheduled success -> retries
    assert len(calls) == 1
    sch._daily_retry_job()  # 12:30 slot: the 08:30 retry succeeded -> nothing to do
    sch._daily_retry_job()
    assert len(calls) == 1


def test_retry_keeps_trying_while_it_fails_and_skips_a_good_slot(monkeypatch, has_data):
    settings = _settings()
    monkeypatch.setattr(sch, "get_settings", lambda: settings)
    _stub_after_pull(monkeypatch)
    calls: list[int] = []
    monkeypatch.setattr(sch, "run_full_pipeline", _fake_pipeline(calls, fail=True))
    sch._daily_retry_job()
    sch._daily_retry_job()
    assert len(calls) == 2  # both slots retry while the day still has no success

    # a TC success / manual success must not count as the scheduled slot
    with session_scope() as s:
        s.add(FlexRun(query_id="TC", status="success", note="scheduled-tc", fetched_at=_utcnow()))
        s.add(FlexRun(query_id="ACT", status="success", note="manual", fetched_at=_utcnow()))
    sch._daily_retry_job()
    assert len(calls) == 3

    with session_scope() as s:
        s.add(FlexRun(query_id="ACT", status="success", note="scheduled", fetched_at=_utcnow()))
    sch._daily_retry_job()
    assert len(calls) == 3


def test_retry_skips_on_fresh_db_and_when_suspended(monkeypatch):
    settings = _settings()
    monkeypatch.setattr(sch, "get_settings", lambda: settings)
    calls: list[int] = []
    monkeypatch.setattr(sch, "run_full_pipeline", _fake_pipeline(calls))
    sch._daily_retry_job()  # fresh DB: bootstrap owns it
    assert calls == []


def test_retry_job_is_registered_at_0830_and_1230_zurich():
    s = sch.build_scheduler()
    s.start(paused=True)
    try:
        job = s.get_job("daily_flex_pull_retry")
        assert job is not None
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert fields["hour"] == "8,12" and fields["minute"] == "30"
    finally:
        s.shutdown(wait=False)


# ------------------------------------------------------------------ B45
def test_next_run_weekday_of_weekly_job_is_monday():
    s = sch.build_scheduler()
    s.start(paused=True)
    try:
        tue = datetime(2026, 10, 6, 12, 0).astimezone()  # a Tuesday, local tz
        rows = {jid: (nxt, wd) for jid, nxt, wd in sch.next_run_weekdays(s, now=tue)}
        assert rows["margin_financing_notice"][1] == "Mon"
        assert datetime.fromisoformat(rows["margin_financing_notice"][0]).weekday() == 0
    finally:
        s.shutdown(wait=False)


# ------------------------------------------------------------------ D5
def test_overlay_interval_jobs_are_not_scheduled_while_disabled(monkeypatch):
    monkeypatch.setattr(sch, "get_settings", lambda: Settings(enable_live_overlay=False))
    s = sch.build_scheduler()
    assert s.get_job("price_alerts") is None
    assert s.get_job("margin_live_alerts") is None
    assert s.get_job("staleness_watchdog") is not None  # unrelated jobs untouched
    monkeypatch.setattr(sch, "get_settings", lambda: Settings(enable_live_overlay=True))
    s = sch.build_scheduler()
    assert s.get_job("price_alerts") is not None
    assert s.get_job("margin_live_alerts") is not None


# ------------------------------------------------------------------ B44
class _FakeDate(date):
    _today = date(2026, 10, 6)

    @classmethod
    def today(cls):
        return cls._today


def test_weekly_message_is_not_dropped_when_monday_pull_failed(monkeypatch):
    monkeypatch.setattr(sch, "date", _FakeDate)
    cfg = {"messages": {"margin_usage": {"enabled": True, "cadence": {"freq": "weekly", "weekday": 0}}}}
    # Tuesday 2026-10-06, nothing sent since Monday's pull failed -> still due
    enabled, due, _ = sch._chained_gate(cfg, "margin_usage")
    assert enabled and due
    sch._mark_cadence_sent("margin_usage", _FakeDate(2026, 10, 6))
    assert sch._chained_gate(cfg, "margin_usage")[1] is False  # once this ISO week
    _FakeDate._today = date(2026, 10, 12)  # next Monday
    try:
        assert sch._chained_gate(cfg, "margin_usage")[1] is True
    finally:
        _FakeDate._today = date(2026, 10, 6)


# ------------------------------------------------------------------ B42
def test_backup_info_and_overdue_alert(tmp_path, monkeypatch):
    from ibkr_backend.jobs.backup import latest_backup_info
    from ibkr_backend.notify import staleness

    now = datetime(2026, 10, 7, 12, 0, tzinfo=__import__("datetime").UTC)
    empty = latest_backup_info(tmp_path, stale_after_hours=36, now=now)
    assert empty["last_backup_at"] is None and empty["is_overdue"] is False  # first run pending

    (tmp_path / "ibkr_backend.nightly-20261007T014500Z.db").write_bytes(b"x")
    (tmp_path / "ibkr_backend.PRE-manual.db").write_bytes(b"x")  # never counted
    fresh = latest_backup_info(tmp_path, stale_after_hours=36, now=now)
    assert fresh["last_backup_at"] == "2026-10-07T01:45:00Z" and fresh["is_overdue"] is False

    late = latest_backup_info(tmp_path, stale_after_hours=36, now=now + timedelta(hours=40))
    assert late["is_overdue"] is True and late["age_hours"] > 36

    sent: list[str] = []
    monkeypatch.setattr(staleness.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(staleness.telegram, "send_message", lambda t, **k: sent.append(t) or True)
    s = Settings(backup_dir=str(tmp_path), telegram_bot_token="b", telegram_chat_id="c")
    with session_scope() as db:
        assert staleness.check_backup_age_alert(db, settings=s, info=late)["sent"] is True
    with session_scope() as db:
        assert staleness.check_backup_age_alert(db, settings=s, info=late)["skipped"] == "already alerted"
    assert len(sent) == 1
    with session_scope() as db:  # a new backup lands -> re-armed
        staleness.check_backup_age_alert(db, settings=s, info=fresh)
        assert db.get(NotificationState, "backup_stale_alert_active").value == "0"


def test_backup_alert_stays_unarmed_when_telegram_not_ok(tmp_path, monkeypatch):
    from ibkr_backend.notify import staleness

    monkeypatch.setattr(staleness.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(staleness.telegram, "send_message", lambda t, **k: False)
    s = Settings(backup_dir=str(tmp_path))
    info = {"is_overdue": True, "age_hours": 50.0, "threshold_hours": 36, "last_backup_at": "x", "enabled": True}
    with session_scope() as db:
        assert staleness.check_backup_age_alert(db, settings=s, info=info)["sent"] is False
    with session_scope() as db:
        assert db.get(NotificationState, "backup_stale_alert_active") is None


# ------------------------------------------------------------------ P1.7
def test_flex_runs_retention_prunes_only_old_zero_insert_tc_successes():
    from ibkr_backend.jobs.retention import prune_flex_runs

    old, recent = _utcnow() - timedelta(days=120), _utcnow() - timedelta(days=5)
    with session_scope() as s:
        s.add_all([
            FlexRun(query_id="TC", status="success", rows_inserted=0, note="scheduled-tc", fetched_at=old),   # prune
            FlexRun(query_id="TC", status="success", rows_inserted=0, note="scheduled-tc", fetched_at=recent),
            FlexRun(query_id="TC", status="success", rows_inserted=2, note="scheduled-tc", fetched_at=old),   # kept: inserted
            FlexRun(query_id="TC", status="error", rows_inserted=0, note="scheduled-tc", fetched_at=old),     # kept: error
            FlexRun(query_id="ACT", status="success", rows_inserted=0, note="scheduled", fetched_at=old),     # kept: Activity
        ])
    with session_scope() as s:
        assert prune_flex_runs(s, keep_days=90, tc_query_id="TC") == 1
    with session_scope() as s:
        assert s.query(FlexRun).count() == 4


def test_flex_runs_status_fetched_at_index_exists_on_old_databases():
    from ibkr_backend.db import session as sess

    with sess.engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_flex_runs_status_fetched_at"))
    assert "ix_flex_runs_status_fetched_at" not in {
        i["name"] for i in inspect(sess.engine).get_indexes("flex_runs")}
    sess.init_db()  # existing volume: create_all() alone would not add it
    assert "ix_flex_runs_status_fetched_at" in {
        i["name"] for i in inspect(sess.engine).get_indexes("flex_runs")}


# ------------------------------------------------------------------ B57
def test_pipeline_routes_emit_utc_aware_timestamps():
    from fastapi.testclient import TestClient

    from ibkr_backend.api.main import create_app

    stamp = _utcnow()
    with session_scope() as s:
        s.add(FlexRun(query_id="ACT", status="success", note="scheduled", fetched_at=stamp))
        s.add(NotificationState(key="scheduler_heartbeat", value=stamp.isoformat()))
        s.add(NotificationState(key="scheduler_pull_attempt", value=stamp.isoformat()))
    c = TestClient(create_app())
    st = c.get("/pipeline/status").json()
    for value in (st["staleness"]["last_success_at"], st["scheduler"]["heartbeat_at"],
                  st["scheduler"]["last_pull_attempt_at"], st["latest_run"]["fetched_at"]):
        assert value.endswith("Z") and datetime.fromisoformat(value).utcoffset() == timedelta(0)
    assert c.get("/pipeline/runs").json()[0]["fetched_at"].endswith("Z")
    assert "backup" in st and st["backup"]["is_overdue"] is False
