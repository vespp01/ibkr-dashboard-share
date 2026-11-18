"""Scheduler liveness heartbeat + misfire-grace hardening.

Guards the resilience fixes for the 2026-08-15/17 incident: the APScheduler
went silent for ~31h while the HTTP server stayed up, so nothing restarted it.
The heartbeat job makes a wedge observable; the misfire grace keeps a briefly
delayed job from being silently dropped. Real SQLite per test (no DB mocking).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger
from fastapi.testclient import TestClient

from ibkr_backend.api.main import create_app
from ibkr_backend.config import get_settings
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import FlexRun, NavDaily, NotificationState, _utcnow
from ibkr_backend.jobs import scheduler as sch
from ibkr_backend.jobs.health import SCHEDULER_HEARTBEAT_KEY, SCHEDULER_PULL_ATTEMPT_KEY


def test_stamp_heartbeat_writes_key():
    with session_scope() as s:
        assert s.get(NotificationState, SCHEDULER_HEARTBEAT_KEY) is None
    sch._stamp_heartbeat()
    with session_scope() as s:
        row = s.get(NotificationState, SCHEDULER_HEARTBEAT_KEY)
        assert row is not None and row.value


def test_stamp_heartbeat_updates_in_place():
    sch._stamp_heartbeat()
    with session_scope() as s:
        first = s.get(NotificationState, SCHEDULER_HEARTBEAT_KEY).value
    sch._stamp_heartbeat()
    with session_scope() as s:
        rows = s.query(NotificationState).filter_by(key=SCHEDULER_HEARTBEAT_KEY).all()
    assert len(rows) == 1          # updated in place, not duplicated
    assert rows[0].value >= first  # ISO strings sort chronologically


def test_build_scheduler_registers_heartbeat_job():
    s = sch.build_scheduler()
    s.start(paused=True)
    try:
        hb = s.get_job("scheduler_heartbeat")
        assert hb is not None
        assert hb.misfire_grace_time == sch._HEARTBEAT_MISFIRE_GRACE
    finally:
        s.shutdown(wait=False)


def test_configurable_jobs_get_misfire_grace():
    """The env-cron notification jobs carry the hardened grace (default was 1s)."""
    s = sch.build_scheduler()
    s.start(paused=True)
    try:
        job = s.get_job("staleness_watchdog")
        assert job is not None
        assert job.misfire_grace_time == sch._CONFIGURABLE_JOB_MISFIRE_GRACE
    finally:
        s.shutdown(wait=False)


def test_crash_diagnostics_enables_faulthandler_and_sigusr1():
    """`_enable_crash_diagnostics` arms the on-demand thread-dump the watchdog
    triggers (kill -USR1) to diagnose a wedged scheduler. Idempotent, never raises."""
    import faulthandler
    import signal

    from ibkr_backend.api import main

    main._enable_crash_diagnostics()
    assert faulthandler.is_enabled()
    if hasattr(signal, "SIGUSR1"):
        assert faulthandler.unregister(signal.SIGUSR1) is True  # was registered
    main._enable_crash_diagnostics()  # second call must not raise


def test_status_endpoint_exposes_scheduler_heartbeat():
    """GET /pipeline/status carries the `scheduler` block the watchdog reads.

    Pins the exact JSON contract deploy/watchdog/watchdog.sh greps:
    ``scheduler.heartbeat_age_seconds`` must be an int when a stamp exists.
    """
    stamp = _utcnow().isoformat()
    with session_scope() as s:
        s.add(NotificationState(key=SCHEDULER_HEARTBEAT_KEY, value=stamp))

    client = TestClient(create_app())  # no `with` → lifespan/scheduler not started
    body = client.get("/pipeline/status").json()

    assert "scheduler" in body
    assert body["scheduler"]["heartbeat_at"] == stamp + "Z"  # B57: aware UTC
    assert isinstance(body["scheduler"]["heartbeat_age_seconds"], int)


# --- cold start (backend down over the 06:30 slot → false "wedge" restart
# and the daily pull never ran) ------------------------------------------------


def test_start_scheduler_stamps_pull_attempt():
    """A fresh scheduler is not wedged: the pull-attempt stamp is refreshed at boot,
    so the watchdog does not read pre-downtime age as a dispatch wedge."""
    stale = (_utcnow() - timedelta(hours=6)).isoformat()
    with session_scope() as s:
        s.add(NotificationState(key=SCHEDULER_PULL_ATTEMPT_KEY, value=stale))
    try:
        sch.start_scheduler()
        with session_scope() as s:
            value = s.get(NotificationState, SCHEDULER_PULL_ATTEMPT_KEY).value
    finally:
        sch.stop_scheduler()
    age = (_utcnow() - datetime.fromisoformat(value)).total_seconds()
    assert 0 <= age < 30


def test_previous_fire_time_matches_the_2026_08_31_boot():
    zurich = ZoneInfo("Europe/Zurich")
    trigger = CronTrigger.from_crontab("30 6 * * *", timezone=zurich)
    boot = datetime(2026, 8, 31, 8, 26, 52, tzinfo=zurich)  # 06:26:52Z, after the 06:30 local slot
    assert sch._previous_fire_time(trigger, boot) == datetime(2026, 8, 31, 6, 30, tzinfo=zurich)
    on_slot = datetime(2026, 8, 31, 6, 30, tzinfo=zurich)
    assert sch._previous_fire_time(trigger, on_slot) == on_slot
    just_before = datetime(2026, 8, 31, 6, 29, 59, tzinfo=zurich)
    assert sch._previous_fire_time(trigger, just_before) == datetime(2026, 8, 30, 6, 30, tzinfo=zurich)


@pytest.fixture()
def paused_scheduler():
    sched = sch.build_scheduler()
    sched.start(paused=True)
    yield sched
    sched.shutdown(wait=False)


def _previous_daily_fire_utc(now: datetime) -> datetime:
    trigger = CronTrigger.from_crontab(get_settings().pipeline_cron)
    previous = sch._previous_fire_time(trigger, now)
    return previous.astimezone(UTC).replace(tzinfo=None)


def _quiet_now(sched) -> datetime:
    """A 'now' two hours before the job's real next fire: far from the slot, so
    the result does not depend on the time of day the suite runs."""
    return sched.get_job("daily_flex_pull").next_run_time.astimezone(UTC) - timedelta(hours=2)


def _add_run(fetched_at: datetime, *, status: str = "success", note: str = "scheduled") -> None:
    with session_scope() as s:
        s.add(FlexRun(query_id="1", status=status, note=note, fetched_at=fetched_at))


@pytest.fixture()
def has_data():
    """One real NAV row, so the DB is not 'fresh' (dashboard_has_data)."""
    with session_scope() as s:
        s.add(NavDaily(snapshot_date=date(2026, 9, 30), account_id="U0", nav_total=Decimal("1")))


def test_startup_catchup_moves_daily_pull_when_slot_missed(paused_scheduler, has_data):
    now = _quiet_now(paused_scheduler)
    previous = _previous_daily_fire_utc(now)
    _add_run(previous - timedelta(days=1))                            # last good run: the slot before
    _add_run(previous + timedelta(minutes=1), status="error")          # a failed run does not count
    _add_run(previous + timedelta(minutes=2), note="tc")               # nor does a TC pull

    assert sch._schedule_startup_catchup(paused_scheduler, now=now) is True
    job = paused_scheduler.get_job("daily_flex_pull")
    delay = (job.next_run_time - now).total_seconds()
    assert delay == pytest.approx(sch._STARTUP_CATCHUP_DELAY_SECONDS, abs=1)
    assert len(paused_scheduler.get_jobs()) == len(sch.build_scheduler().get_jobs())  # no extra job


def test_startup_catchup_skipped_when_daily_slot_ran(paused_scheduler, has_data):
    now = _quiet_now(paused_scheduler)
    _add_run(_previous_daily_fire_utc(now) + timedelta(minutes=1))
    before = paused_scheduler.get_job("daily_flex_pull").next_run_time

    assert sch._schedule_startup_catchup(paused_scheduler, now=now) is False
    assert paused_scheduler.get_job("daily_flex_pull").next_run_time == before


def test_startup_catchup_ignores_tc_and_manual_successes(paused_scheduler, has_data):
    # Review of #17: the hourly TC job logs note="scheduled-tc". Only a scheduled
    # daily run counts, because only it sends the digest and margin alerts.
    now = _quiet_now(paused_scheduler)
    previous = _previous_daily_fire_utc(now)
    _add_run(previous - timedelta(days=1))
    _add_run(previous + timedelta(minutes=45), note="scheduled-tc")
    _add_run(previous + timedelta(minutes=50), note="manual")

    assert sch._schedule_startup_catchup(paused_scheduler, now=now) is True


def test_startup_catchup_skipped_on_a_fresh_db(paused_scheduler):
    # No data at all (first deploy): `bootstrap` loads history, not a catch-up.
    assert sch._schedule_startup_catchup(paused_scheduler, now=_quiet_now(paused_scheduler)) is False


def test_startup_catchup_runs_on_plan_b_data_without_flex_runs(paused_scheduler, has_data):
    # load-xml writes data but no FlexRun row.
    assert sch._schedule_startup_catchup(paused_scheduler, now=_quiet_now(paused_scheduler)) is True


@pytest.mark.parametrize("lead", [timedelta(seconds=60), timedelta(minutes=30)])
def test_startup_catchup_does_not_absorb_the_next_cron_fire(paused_scheduler, has_data, lead):
    # A catch-up still running at the slot would make max_instances=1 drop it.
    natural = paused_scheduler.get_job("daily_flex_pull").next_run_time
    now = natural - lead

    assert sch._schedule_startup_catchup(paused_scheduler, now=now) is False
    assert paused_scheduler.get_job("daily_flex_pull").next_run_time == natural
