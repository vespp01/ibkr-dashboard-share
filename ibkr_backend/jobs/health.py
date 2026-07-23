"""Pipeline freshness / staleness detection for the daily Flex pull.

The daily scheduler (`jobs/scheduler.py`) runs `run_full_pipeline` once a day.
If it silently stops succeeding — expired token, deactivated query, scheduler
process not running — the dashboard keeps serving stale data with no visible
signal (this is exactly what happened on 2026-06-08). This module computes how
old the last *successful* run is so both `GET /pipeline/status` and a periodic
watchdog job can surface it.

There is no push channel configured in this project (Telegram/email were
removed), so the "alert" is a logged WARNING plus an `is_stale` flag on the
status endpoint. An external monitor (launchd, a healthcheck probe, the
frontend) can act on either.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import FlexRun, _utcnow


def compute_staleness(
    session: Session,
    *,
    threshold_hours: float,
    now: datetime | None = None,
) -> dict:
    """Age of the last *successful* Flex run versus a staleness threshold.

    Returns a dict with the last-success timestamp, its age in hours, and an
    ``is_stale`` flag — True when the last success is older than
    ``threshold_hours`` OR when there has never been a successful run. Times are
    naive UTC, matching ``_utcnow()`` and how ``FlexRun.fetched_at`` is stored.
    """
    now = now or _utcnow()
    latest = session.execute(
        select(FlexRun)
        .where(FlexRun.status == "success")
        .order_by(FlexRun.fetched_at.desc())
        .limit(1)
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
        "last_success_at": latest.fetched_at.isoformat(),
        "age_hours": round(age_hours, 1),
        "is_stale": age_hours > threshold_hours,
        "threshold_hours": threshold_hours,
        "reason": None,
    }


__all__ = ["compute_staleness"]
