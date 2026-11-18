"""Pipeline freshness / staleness detection for the daily Flex pull.

The daily scheduler (`jobs/scheduler.py`) runs `run_full_pipeline` once a day.
If it silently stops succeeding — expired token, deactivated query, scheduler
process not running — the dashboard keeps serving stale data with no visible
signal (this is exactly what happened on 2026-06-08). This module computes how
old the last *successful* run is so both `GET /pipeline/status` and a periodic
watchdog job can surface it.

Staleness surfaces three ways: (a) a logged WARNING, (b) the `is_stale` flag on
`GET /pipeline/status` — consumed by the watchdog sidecar and any external
monitor, and (c) a deduped Telegram push per stale episode via
`notify.staleness.check_staleness_alert`, wired from `scheduler._staleness_watchdog`.
This module also stamps/reads the scheduler liveness heartbeat.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import FlexRun, NotificationState, PositionEod, _utcnow

# Liveness key stamped by the scheduler's heartbeat job (jobs/scheduler.py).
SCHEDULER_HEARTBEAT_KEY = "scheduler_heartbeat"
# Liveness key stamped at the START of every pull job (daily + TC) — proves the
# scheduler is still *dispatching* the pulls, not just that the main loop ticks.
# The heartbeat can stay fresh while the pull jobs' `max_instances` slot wedges
# and every fire is silently skipped (the 2026-08-22 incident): the heartbeat is
# a separate job, so it kept ticking while no pull ran for ~45h. This second
# stamp closes that blind spot. Stamped before any early-return/network call, so
# an upstream token/query/IBKR *failure* still refreshes it → the watchdog can
# tell a dispatch wedge (restart fixes) from an upstream error (restart won't).
SCHEDULER_PULL_ATTEMPT_KEY = "scheduler_pull_attempt"


def utc_iso(value: datetime | str | None) -> str | None:
    """Timezone-aware UTC ISO string (``...Z``) for a naive-UTC datetime (B57).

    ``db.models._utcnow`` stores naive UTC; emitted bare, browsers parse it as
    local time (a pull shown 2 h early in summer). ``None`` / unparsable → ``None``.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _stamp_age(session: Session, key: str, now: datetime) -> tuple[str | None, int | None]:
    """(iso, age_seconds) for a NotificationState liveness stamp; (None, None) if absent/unparsable."""
    row = session.get(NotificationState, key)
    if row is None or not row.value:
        return None, None
    try:
        stamped = datetime.fromisoformat(row.value)
    except (ValueError, TypeError):
        return None, None
    return utc_iso(stamped), int((now - stamped).total_seconds())


def scheduler_heartbeat(session: Session, *, now: datetime | None = None) -> dict:
    """Scheduler liveness: main-loop heartbeat AND last pull-job dispatch.

    ``heartbeat_age_seconds`` proves the scheduler *loop* is alive.
    ``last_pull_attempt_age_seconds`` proves it is still *dispatching the pull
    jobs* — a fresh heartbeat with a stale pull-attempt is the dispatch-wedge the
    watchdog restarts on. Either age is ``None`` when never stamped (e.g. just
    started). An external watchdog treats a large age as a wedge the HTTP
    healthcheck cannot see.
    """
    now = now or _utcnow()
    hb_at, hb_age = _stamp_age(session, SCHEDULER_HEARTBEAT_KEY, now)
    pa_at, pa_age = _stamp_age(session, SCHEDULER_PULL_ATTEMPT_KEY, now)
    return {
        "heartbeat_at": hb_at,
        "heartbeat_age_seconds": hb_age,
        "last_pull_attempt_at": pa_at,
        "last_pull_attempt_age_seconds": pa_age,
    }


def compute_staleness(
    session: Session,
    *,
    threshold_hours: float,
    now: datetime | None = None,
    query_id: str | None = None,
) -> dict:
    """Age of the last *successful* Flex run versus a staleness threshold.

    ``query_id`` restricts the check to one Flex query. Callers pass the
    Activity query id: the hourly Trade Confirmation pull succeeds every hour
    and would otherwise mask Activity failures for days (2026-06-16/17 never
    flagged). ``None`` keeps the unfiltered behaviour.

    Returns a dict with the last-success timestamp, its age in hours, and an
    ``is_stale`` flag — True when the last success is older than
    ``threshold_hours`` OR when there has never been a successful run. Times are
    naive UTC, matching ``_utcnow()`` and how ``FlexRun.fetched_at`` is stored.
    """
    now = now or _utcnow()
    stmt = select(FlexRun).where(FlexRun.status == "success")
    if query_id:
        stmt = stmt.where(FlexRun.query_id == query_id)
    latest = session.execute(
        stmt.order_by(FlexRun.fetched_at.desc()).limit(1)
    ).scalar_one_or_none()

    if latest is None or latest.fetched_at is None:
        return {
            "last_success_at": None,
            "age_hours": None,
            "is_stale": True,
            "threshold_hours": threshold_hours,
            "reason": "no successful run on record",
        }

    age_hours = (now - latest.fetched_at).total_seconds() / 3600.0
    return {
        "last_success_at": utc_iso(latest.fetched_at),
        "age_hours": round(age_hours, 1),
        "is_stale": age_hours > threshold_hours,
        "threshold_hours": threshold_hours,
        "reason": None,
    }


def latest_run_error(session: Session, *, query_id: str | None) -> dict | None:
    """Newest FlexRun of ``query_id`` when it ended in error, else ``None``.

    "Newest" means the newest run of any status: a later success clears the
    error. ``error`` is already redacted by the pipeline (`_safe_error`).
    """
    stmt = select(FlexRun)
    if query_id:
        stmt = stmt.where(FlexRun.query_id == query_id)
    newest = session.execute(
        stmt.order_by(FlexRun.fetched_at.desc()).limit(1)
    ).scalar_one_or_none()
    if newest is None or newest.status != "error":
        return None
    return {"fetched_at": newest.fetched_at, "error": newest.error}


def business_days_between(start: date, end: date) -> int:
    """Business days (Mon-Fri) in the half-open range ``(start, end]``; 0 if ``end <= start``."""
    if end <= start:
        return 0
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def compute_data_freshness(
    session: Session,
    *,
    threshold_business_days: int,
    today: date | None = None,
) -> dict:
    """Age of the newest ``positions_eod`` snapshot in business days.

    Complements ``compute_staleness`` (age of the last *successful pull*): a pull
    can keep succeeding while the statement IBKR returns no longer covers new
    days (2026-08-17 → 2026-09-12 incident: "Year to Date" Activity query
    delivered truncated). ``data_is_stale`` is True when the newest snapshot is
    older than ``threshold_business_days`` or when there is no snapshot at all.
    The key is deliberately named ``data_is_stale`` so the watchdog's flat JSON
    grep cannot confuse it with ``staleness.is_stale``.
    """
    today = today or _utcnow().date()
    last = session.execute(select(func.max(PositionEod.snapshot_date))).scalar()
    if last is None:
        return {
            "last_data_date": None,
            "age_business_days": None,
            "data_is_stale": True,
            "threshold_business_days": threshold_business_days,
            "reason": "no position snapshot on record",
        }
    age = business_days_between(last, today)
    return {
        "last_data_date": last.isoformat(),
        "age_business_days": age,
        "data_is_stale": age > threshold_business_days,
        "threshold_business_days": threshold_business_days,
        "reason": None,
    }


__all__ = [
    "SCHEDULER_HEARTBEAT_KEY",
    "SCHEDULER_PULL_ATTEMPT_KEY",
    "business_days_between",
    "compute_data_freshness",
    "compute_staleness",
    "latest_run_error",
    "scheduler_heartbeat",
    "utc_iso",
]
