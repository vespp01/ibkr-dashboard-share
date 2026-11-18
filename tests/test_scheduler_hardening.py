"""Scheduler hardening: token-expiry milestone firing + daily-pull misfire grace.

Real SQLite per test (autouse fixture). No network — send_message is stubbed.
"""

from __future__ import annotations

from datetime import date, timedelta

from ibkr_backend.config import Settings
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import NotificationState
from ibkr_backend.jobs import scheduler as sch


def test_token_expiry_bucket_milestones():
    b = sch._token_expiry_bucket
    assert b(-1, 7) == "expired"
    assert b(0, 7) == "0"
    assert b(1, 7) == "1"
    assert b(2, 7) == "3"   # reached the 3-day milestone
    assert b(3, 7) == "3"
    assert b(5, 7) == "7"
    assert b(7, 7) == "7"
    assert b(8, 7) is None   # still outside the warning window


def test_daily_pull_has_wide_misfire_grace():
    """A sleep-prone host that wakes hours after 06:30 must still get its pull."""
    s = sch.build_scheduler()
    s.start(paused=True)
    try:
        job = s.get_job("daily_flex_pull")
        assert job is not None
        assert job.misfire_grace_time >= 6 * 60 * 60  # generous, not the old 1h
    finally:
        s.shutdown(wait=False)


def test_notify_token_expiry_fires_once_per_milestone(monkeypatch):
    import ibkr_backend.notify as notify

    sent: list[str] = []
    settings = Settings(
        telegram_bot_token="t",
        telegram_chat_id="c",
        ib_flex_token_expiry=(date.today() + timedelta(days=1)).isoformat(),
        token_expiry_warn_days=7,
    )
    monkeypatch.setattr(sch, "get_settings", lambda: settings)
    monkeypatch.setattr(notify, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(notify, "send_message", lambda msg, **k: sent.append(msg) or True)

    sch._notify_token_expiry(config=None)
    sch._notify_token_expiry(config=None)  # same "1-day" milestone → deduped

    assert len(sent) == 1
    with session_scope() as db:
        row = db.get(NotificationState, sch._TOKEN_EXPIRY_KEY)
        assert row is not None and row.value.endswith("|1")


def test_notify_token_expiry_send_failure_does_not_advance_marker(monkeypatch):
    import ibkr_backend.notify as notify

    settings = Settings(
        telegram_bot_token="t",
        telegram_chat_id="c",
        ib_flex_token_expiry=date.today().isoformat(),  # expires today → bucket "0"
        token_expiry_warn_days=7,
    )
    monkeypatch.setattr(sch, "get_settings", lambda: settings)
    monkeypatch.setattr(notify, "is_configured", lambda *a, **k: True)

    def boom(msg, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(notify, "send_message", boom)
    sch._notify_token_expiry(config=None)  # send fails → must not persist the marker
    with session_scope() as db:
        assert db.get(NotificationState, sch._TOKEN_EXPIRY_KEY) is None


def test_notify_token_expiry_not_ok_does_not_advance_marker(monkeypatch):
    """Telegram HTTP 200 {ok:false} → send_message returns False; the milestone
    marker must NOT advance, else the undelivered warning is lost until the next."""
    import ibkr_backend.notify as notify

    settings = Settings(
        telegram_bot_token="t",
        telegram_chat_id="c",
        ib_flex_token_expiry=date.today().isoformat(),
        token_expiry_warn_days=7,
    )
    monkeypatch.setattr(sch, "get_settings", lambda: settings)
    monkeypatch.setattr(notify, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(notify, "send_message", lambda msg, **k: False)  # not-ok
    sch._notify_token_expiry(config=None)
    with session_scope() as db:
        assert db.get(NotificationState, sch._TOKEN_EXPIRY_KEY) is None


# --------------------------------------------------------------------------- #
# Pull-attempt liveness: proves the scheduler is still DISPATCHING the pulls,
# not just that its main loop ticks. Closes the 2026-08-22 blind spot where the
# heartbeat stayed fresh while the pull jobs' max_instances slot wedged.
# --------------------------------------------------------------------------- #
from ibkr_backend.jobs.health import (  # noqa: E402
    SCHEDULER_PULL_ATTEMPT_KEY,
    scheduler_heartbeat,
)


def test_daily_job_stamps_pull_attempt(monkeypatch):
    """Even when the pull is skipped (Flex not configured), the attempt is stamped
    first — so a fresh stamp always means 'the scheduler dispatched a pull'."""
    monkeypatch.setattr(sch, "get_settings", lambda: Settings())  # no token → early return
    sch._daily_job()
    with session_scope() as db:
        row = db.get(NotificationState, SCHEDULER_PULL_ATTEMPT_KEY)
        assert row is not None and row.value


def test_tc_job_stamps_pull_attempt(monkeypatch):
    monkeypatch.setattr(sch, "get_settings", lambda: Settings())  # no TC query → early return
    sch._tc_job()
    with session_scope() as db:
        assert db.get(NotificationState, SCHEDULER_PULL_ATTEMPT_KEY) is not None


def test_pull_attempt_stamped_even_when_pull_raises(monkeypatch):
    """The stamp precedes the network call, so an UPSTREAM failure still refreshes
    it. That is what keeps the watchdog from restart-looping on a token/query
    error a restart cannot fix (it fires only on a true dispatch wedge)."""
    settings = Settings(ib_flex_token="tok", ib_flex_query_id="q1")
    monkeypatch.setattr(sch, "get_settings", lambda: settings)

    def _boom(**_):
        raise RuntimeError("HTTP 604")

    monkeypatch.setattr(sch, "run_full_pipeline", _boom)
    sch._daily_job()  # _daily_job swallows the failure, but must have stamped first
    with session_scope() as db:
        assert db.get(NotificationState, SCHEDULER_PULL_ATTEMPT_KEY) is not None


def test_status_exposes_pull_attempt_age():
    from ibkr_backend.db.models import _utcnow

    with session_scope() as db:
        db.add(NotificationState(
            key=SCHEDULER_PULL_ATTEMPT_KEY, value=_utcnow().isoformat()
        ))
    with session_scope() as db:
        out = scheduler_heartbeat(db)
    assert out["last_pull_attempt_age_seconds"] is not None
    assert out["last_pull_attempt_age_seconds"] >= 0
    # heartbeat never stamped in this test → present but None (fail-safe for the
    # watchdog: a missing stamp reads as 'unknown', never as a spurious wedge).
    assert "heartbeat_age_seconds" in out
    assert out["heartbeat_age_seconds"] is None
