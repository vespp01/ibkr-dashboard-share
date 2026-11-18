"""Test compute_staleness — the pipeline freshness signal behind the staleness
watchdog and GET /pipeline/status. Real SQLite per test (no DB mocking)."""

from __future__ import annotations

from datetime import timedelta

from ibkr_backend.db import session_scope
from ibkr_backend.db.models import FlexRun, NotificationState, _utcnow
from ibkr_backend.jobs.health import (
    SCHEDULER_HEARTBEAT_KEY,
    compute_staleness,
    scheduler_heartbeat,
)


def _seed_run(session, *, status: str, age_hours: float) -> None:
    session.add(
        FlexRun(
            query_id="Q1",
            reference_code="REF",
            fetched_at=_utcnow() - timedelta(hours=age_hours),
            status=status,
            rows_inserted=1,
        )
    )


def test_no_runs_is_stale():
    """An empty flex_runs table is treated as stale (nothing has ever succeeded)."""
    with session_scope() as s:
        result = compute_staleness(s, threshold_hours=30)

    assert result["is_stale"] is True
    assert result["last_success_at"] is None
    assert result["age_hours"] is None
    assert result["reason"] == "no successful run on record"


def test_recent_success_is_fresh():
    """A success 2h ago is well within a 30h threshold."""
    with session_scope() as s:
        _seed_run(s, status="success", age_hours=2)

    with session_scope() as s:
        result = compute_staleness(s, threshold_hours=30)

    assert result["is_stale"] is False
    assert result["age_hours"] < 30
    assert result["last_success_at"] is not None


def test_old_success_is_stale():
    """A success 40h ago exceeds a 30h threshold."""
    with session_scope() as s:
        _seed_run(s, status="success", age_hours=40)

    with session_scope() as s:
        result = compute_staleness(s, threshold_hours=30)

    assert result["is_stale"] is True
    assert result["age_hours"] > 30


def test_recent_error_does_not_count_as_success():
    """A recent *failed* run must not mask staleness — only successes count.

    A run that errored 1h ago with the last success 40h ago is still stale.
    """
    with session_scope() as s:
        _seed_run(s, status="success", age_hours=40)
        _seed_run(s, status="error", age_hours=1)

    with session_scope() as s:
        result = compute_staleness(s, threshold_hours=30)

    assert result["is_stale"] is True
    # The age reported is the last SUCCESS (~40h), not the recent error.
    assert result["age_hours"] > 30


def test_threshold_is_respected():
    """Same data, different threshold flips the verdict."""
    with session_scope() as s:
        _seed_run(s, status="success", age_hours=20)

    with session_scope() as s:
        assert compute_staleness(s, threshold_hours=30)["is_stale"] is False
        assert compute_staleness(s, threshold_hours=10)["is_stale"] is True


# ------------------------------------------------- scheduler liveness heartbeat
def test_heartbeat_none_when_never_stamped():
    """No stamp yet → all ages None (the watchdog must not treat this as wedged)."""
    with session_scope() as s:
        assert scheduler_heartbeat(s) == {
            "heartbeat_at": None,
            "heartbeat_age_seconds": None,
            "last_pull_attempt_at": None,
            "last_pull_attempt_age_seconds": None,
        }


def test_heartbeat_age_from_stamp():
    """A stamp N seconds ago reports ~N seconds of age."""
    stamp = (_utcnow() - timedelta(seconds=90)).isoformat()
    with session_scope() as s:
        s.add(NotificationState(key=SCHEDULER_HEARTBEAT_KEY, value=stamp))
    with session_scope() as s:
        hb = scheduler_heartbeat(s)
    assert hb["heartbeat_at"] == stamp + "Z"  # B57: aware UTC, not naive
    assert 85 <= hb["heartbeat_age_seconds"] <= 95


def test_heartbeat_ignores_garbage_value():
    """A non-ISO value is treated as absent, not a crash."""
    with session_scope() as s:
        s.add(NotificationState(key=SCHEDULER_HEARTBEAT_KEY, value="not-a-date"))
    with session_scope() as s:
        assert scheduler_heartbeat(s)["heartbeat_age_seconds"] is None


# ── Data-age rule (2026-09-12): pulls OK but positions frozen ─────────────────

from datetime import date  # noqa: E402
from decimal import Decimal  # noqa: E402

from ibkr_backend.db.models import PositionEod  # noqa: E402
from ibkr_backend.jobs.health import business_days_between, compute_data_freshness  # noqa: E402


def _seed_position(session, snapshot: date) -> None:
    session.add(PositionEod(snapshot_date=snapshot, account_id="U1", conid=1,
                            symbol="AAA", quantity=Decimal("1")))


def test_business_days_between_skips_weekends():
    fri, mon, wed = date(2026, 9, 11), date(2026, 9, 14), date(2026, 9, 16)
    assert business_days_between(fri, fri) == 0
    assert business_days_between(fri, mon) == 1
    assert business_days_between(fri, wed) == 3
    assert business_days_between(mon, fri) == 0        # end <= start


def test_data_freshness_no_snapshot_is_stale():
    with session_scope() as s:
        r = compute_data_freshness(s, threshold_business_days=3, today=date(2026, 9, 12))
    assert r["data_is_stale"] is True and r["last_data_date"] is None
    assert r["reason"] == "no position snapshot on record"


def test_data_freshness_friday_snapshot_is_fresh_on_monday_and_stale_after_threshold():
    with session_scope() as s:
        _seed_position(s, date(2026, 9, 11))          # viernes
    with session_scope() as s:
        mon = compute_data_freshness(s, threshold_business_days=3, today=date(2026, 9, 14))
        thu = compute_data_freshness(s, threshold_business_days=3, today=date(2026, 9, 17))
    assert mon["data_is_stale"] is False and mon["age_business_days"] == 1
    assert thu["data_is_stale"] is True and thu["age_business_days"] == 4
    assert thu["last_data_date"] == "2026-09-11" and thu["threshold_business_days"] == 3


def test_data_freshness_uses_newest_snapshot():
    with session_scope() as s:
        _seed_position(s, date(2026, 8, 17))
        _seed_position(s, date(2026, 9, 11))
    with session_scope() as s:
        r = compute_data_freshness(s, threshold_business_days=3, today=date(2026, 9, 14))
    assert r["last_data_date"] == "2026-09-11" and r["data_is_stale"] is False


def test_fresh_tc_does_not_mask_old_activity_failure():
    """B40: hourly TC successes must not reset the Activity staleness clock."""
    with session_scope() as s:
        s.add(FlexRun(query_id="ACT", status="success", fetched_at=_utcnow() - timedelta(hours=60),
                      note="scheduled"))
        s.add(FlexRun(query_id="ACT", status="error", fetched_at=_utcnow() - timedelta(hours=12),
                      note="scheduled", error="FlexError: Flex 1012: Token has expired"))
        s.add(FlexRun(query_id="TC", status="success", fetched_at=_utcnow() - timedelta(minutes=10),
                      note="scheduled-tc"))
    with session_scope() as s:
        unfiltered = compute_staleness(s, threshold_hours=30)
        activity = compute_staleness(s, threshold_hours=30, query_id="ACT")
    assert unfiltered["is_stale"] is False  # the masking bug
    assert activity["is_stale"] is True
    assert activity["age_hours"] > 59
