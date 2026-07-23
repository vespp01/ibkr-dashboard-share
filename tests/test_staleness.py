"""Test compute_staleness — the pipeline freshness signal behind the staleness
watchdog and GET /pipeline/status. Real SQLite per test (no DB mocking)."""

from __future__ import annotations

from datetime import timedelta

from ibkr_backend.db import session_scope
from ibkr_backend.db.models import FlexRun, _utcnow
from ibkr_backend.jobs.health import compute_staleness


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
