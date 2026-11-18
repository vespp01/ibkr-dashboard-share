"""Retention for ``flex_runs`` (P1.7).

The hourly Trade Confirmation pull writes one ``flex_runs`` row per hour, ~98 %
of them successes that inserted nothing. They carry no information after a few
weeks, so a daily job prunes TC successes with zero inserts older than
``flex_runs_retention_days``. Activity runs, errors and any run that inserted
rows are always kept (they are the audit trail behind staleness and `/pipeline/runs`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from ..db.models import FlexRun, _utcnow

log = logging.getLogger(__name__)


def prune_flex_runs(
    session: Session,
    *,
    keep_days: int,
    tc_query_id: str | None,
    now: datetime | None = None,
) -> int:
    """Delete zero-insert TC success rows older than ``keep_days``. Returns the count."""
    cutoff = (now or _utcnow()) - timedelta(days=keep_days)
    is_tc = FlexRun.note == "scheduled-tc"
    if tc_query_id:
        is_tc = or_(is_tc, FlexRun.query_id == tc_query_id)
    result = session.execute(
        delete(FlexRun).where(
            FlexRun.status == "success",
            FlexRun.rows_inserted == 0,
            FlexRun.fetched_at < cutoff,
            is_tc,
        )
    )
    return result.rowcount or 0


def flex_runs_retention_job() -> None:
    """Scheduled daily step. Never raises."""
    from ..config import get_settings
    from ..db import session_scope

    settings = get_settings()
    try:
        with session_scope() as s:
            n = prune_flex_runs(
                s,
                keep_days=settings.flex_runs_retention_days,
                tc_query_id=settings.ib_flex_query_id_tc or None,
            )
        log.info("flex_runs retention: pruned %d zero-insert TC rows older than %d d", n,
                 settings.flex_runs_retention_days)
    except Exception as e:  # noqa: BLE001
        log.warning("flex_runs retention failed: %s", e)
