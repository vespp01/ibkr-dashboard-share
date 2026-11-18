"""Pipeline control endpoints — manual Flex pull, run history."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from sqlalchemy import select

from ...config import get_settings
from ...db.models import FlexRun
from ...jobs.backup import latest_backup_info
from ...jobs.health import (
    compute_data_freshness,
    compute_staleness,
    scheduler_heartbeat,
    utc_iso,
)
from ...loaders.pipeline import run_full_pipeline
from ..deps import SessionDep

router = APIRouter()


@router.post(
    "/run",
    summary="Trigger a Flex Web Service pull (sync by default; ?async=true to queue)",
    responses={
        202: {"description": "Run queued in the background (`?async=true`). "
                             "Poll GET /pipeline/runs for the outcome."},
    },
)
def run(
    background: BackgroundTasks,
    response: Response,
    period: str | None = Query(
        None,
        description="Flex period override (e.g. LastBusinessDay). Default empty = "
        "use the query's own configured period, matching the scheduled pull. Most "
        "IBKR queries have a fixed period and REJECT an override with HTTP 604, so "
        "only set this if yours accepts one.",
    ),
    from_date: str | None = Query(None, pattern="^[0-9]{8}$"),
    to_date: str | None = Query(None, pattern="^[0-9]{8}$"),
    asynchronous: bool = Query(False, alias="async"),
    note: str | None = Query("manual"),
):
    """Trigger a Flex pull and load it into the DB.

    **Synchronous by default**: the request blocks while IBKR generates and
    serves the statement — up to `flex_max_wait_seconds` (default **300 s**).
    The nginx proxy in front of the frontend (`frontend/nginx.conf`) uses the
    same 300 s `proxy_read_timeout`, so a slow statement generation called via
    `:5173/api/pipeline/run` can surface as a **504 while the run still
    completes server-side**. The pipeline is idempotent, so retrying is safe;
    check `GET /pipeline/runs` for the actual outcome. Calls direct to the
    backend (`:8001`) or via the CLI don't go through nginx.

    **`?async=true`**: enqueue the pull as a background task instead — returns
    `202 {"status": "queued"}` immediately; poll `GET /pipeline/runs`.
    """
    settings = get_settings()
    if not settings.ib_flex_token or not settings.ib_flex_query_id:
        raise HTTPException(
            status_code=400,
            detail="IB_FLEX_TOKEN / IB_FLEX_QUERY_ID not configured (.env).",
        )
    if asynchronous:
        background.add_task(
            run_full_pipeline,
            from_date=from_date, to_date=to_date, period=period, note=note,
        )
        response.status_code = 202
        return {"status": "queued", "note": note}
    result = run_full_pipeline(
        from_date=from_date, to_date=to_date, period=period, note=note,
    )
    return {
        "status": "ok",
        "reference_code": result.reference_code,
        "from_date": result.from_date.isoformat() if result.from_date else None,
        "to_date": result.to_date.isoformat() if result.to_date else None,
        "statements": result.statements,
        "inserted": result.total_inserted,
        "skipped": result.total_skipped,
        "sections": [
            {"section": s.section, "inserted": s.inserted,
             "skipped": s.skipped, "replaced": s.replaced}
            for s in result.sections
        ],
    }


@router.get("/runs", summary="List recent Flex pipeline runs")
def runs(session: SessionDep, limit: int = Query(50, ge=1, le=500)):
    rows = session.execute(
        select(FlexRun).order_by(FlexRun.fetched_at.desc()).limit(limit)
    ).scalars().all()
    return [
        {
            "run_id": r.run_id,
            "fetched_at": utc_iso(r.fetched_at),
            "from_date": r.from_date.isoformat() if r.from_date else None,
            "to_date": r.to_date.isoformat() if r.to_date else None,
            "rows_inserted": r.rows_inserted,
            "rows_skipped_dup": r.rows_skipped_dup,
            "status": r.status,
            "error": r.error,
            "note": r.note,
        }
        for r in rows
    ]


@router.get("/status", summary="Non-secret Flex pipeline status")
def status(session: SessionDep):
    settings = get_settings()
    latest = session.execute(
        select(FlexRun).order_by(FlexRun.fetched_at.desc()).limit(1)
    ).scalar_one_or_none()
    return {
        "configured": bool(settings.ib_flex_token and settings.ib_flex_query_id),
        "activity_configured": bool(settings.ib_flex_token and settings.ib_flex_query_id),
        "tc_configured": bool(settings.ib_flex_token and settings.ib_flex_query_id_tc),
        "scheduler_enabled": bool(settings.ib_flex_token and settings.ib_flex_query_id),
        "base_currency": settings.base_currency,
        "token_expiry": settings.ib_flex_token_expiry or None,
        "live_overlay_enabled": settings.enable_live_overlay,
        "staleness": compute_staleness(
            session, threshold_hours=settings.pipeline_stale_after_hours,
            query_id=settings.ib_flex_query_id or None,
        ),
        "data_freshness": compute_data_freshness(
            session, threshold_business_days=settings.data_stale_after_business_days
        ),
        "scheduler": scheduler_heartbeat(session),
        "backup": latest_backup_info(
            settings.backup_dir, stale_after_hours=settings.backup_stale_after_hours
        ),
        "latest_run": (
            {
                "fetched_at": utc_iso(latest.fetched_at) if latest else None,
                "from_date": latest.from_date.isoformat() if latest and latest.from_date else None,
                "to_date": latest.to_date.isoformat() if latest and latest.to_date else None,
                "status": latest.status if latest else None,
                "rows_inserted": latest.rows_inserted if latest else 0,
                "rows_skipped_dup": latest.rows_skipped_dup if latest else 0,
                "has_error": bool(latest and latest.error),
            }
            if latest
            else None
        ),
    }
