"""Live-apply of the notification config to the APScheduler singleton."""

from __future__ import annotations

from ibkr_backend.jobs import scheduler as sch
from ibkr_backend.notify import config_store as store


def _start_paused(monkeypatch):
    """A built scheduler wired as the singleton, started paused (no job fires)."""
    s = sch.build_scheduler()
    s.start(paused=True)
    monkeypatch.setattr(sch, "_scheduler", s)
    return s


def test_sync_reschedules_cron_job(monkeypatch):
    s = _start_paused(monkeypatch)
    try:
        # margin_fx defaults to weekly (0 8 * * mon); move it to monthly day 5 07:00.
        cfg = store.save_notification_config(
            {"messages": {"margin_fx": {"enabled": True,
                                        "cadence": {"freq": "monthly", "day_of_month": 5, "time": "07:00"}}}}
        )
        res = sch.sync_scheduler_jobs(cfg)
        assert res["applied"] is True
        trigger = str(s.get_job("margin_financing_notice").trigger)
        assert "day='5'" in trigger
        assert "hour='7'" in trigger
    finally:
        s.shutdown(wait=False)


def test_sync_removes_disabled_job(monkeypatch):
    s = _start_paused(monkeypatch)
    try:
        assert s.get_job("margin_financing_notice") is not None  # enabled by default
        cfg = store.save_notification_config({"messages": {"margin_fx": {"enabled": False}}})
        res = sch.sync_scheduler_jobs(cfg)
        assert res["jobs"]["margin_financing_notice"] == "removed"
        assert s.get_job("margin_financing_notice") is None
    finally:
        s.shutdown(wait=False)


def test_sync_readds_when_reenabled(monkeypatch):
    s = _start_paused(monkeypatch)
    try:
        sch.sync_scheduler_jobs(store.save_notification_config({"messages": {"margin_fx": {"enabled": False}}}))
        assert s.get_job("margin_financing_notice") is None
        res = sch.sync_scheduler_jobs(store.save_notification_config({"messages": {"margin_fx": {"enabled": True}}}))
        assert res["jobs"]["margin_financing_notice"].startswith("added:")
        assert s.get_job("margin_financing_notice") is not None
    finally:
        s.shutdown(wait=False)


def test_build_scheduler_respects_disabled_config(monkeypatch):
    # Persist a disabled margin_fx BEFORE building — a restart must keep it off.
    store.save_notification_config({"messages": {"margin_fx": {"enabled": False}}})
    s = sch.build_scheduler()
    assert s.get_job("margin_financing_notice") is None
    # a non-configurable job is always present
    assert s.get_job("daily_flex_pull") is not None


def test_sync_noop_when_scheduler_not_running(monkeypatch):
    monkeypatch.setattr(sch, "_scheduler", None)
    res = sch.sync_scheduler_jobs()
    assert res["applied"] is False
    assert res["reason"] == "scheduler_not_running"
