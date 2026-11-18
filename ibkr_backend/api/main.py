"""FastAPI entry point.

Run with:
    uvicorn ibkr_backend.api.main:app --reload --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from ..config import get_settings
from ..db import init_db
from ..db.models import CashTransaction, NavDaily, PositionEod, Trade
from .deps import SessionDep
from .routes import (
    commissions_router,
    config_router,
    dividends_router,
    integrations_router,
    options_router,
    performance_router,
    pipeline_router,
    portfolio_router,
    tax_router,
    trades_router,
)

log = logging.getLogger(__name__)


def _configure_logging(settings) -> None:
    """Surface ibkr_backend.* INFO logs in `docker logs`.

    uvicorn configures only its own loggers, leaving ours on the root
    last-resort handler (WARNING+ only) — so the daily digest / token-expiry
    success lines never appeared. Attach our own handler at `log_level`.
    """
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logger = logging.getLogger("ibkr_backend")
    logger.setLevel(level)
    if not any(getattr(h, "_ibkr_dashboard", False) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        handler._ibkr_dashboard = True  # marker → idempotent across reloads
        logger.addHandler(handler)
    logger.propagate = False


def _enable_crash_diagnostics() -> None:
    """Make a wedged/crashed process diagnosable on demand.

    ``faulthandler.enable()`` dumps all thread tracebacks to stderr on a fatal
    signal. Registering ``SIGUSR1`` lets the external watchdog trigger a full
    all-thread dump (`kill -USR1 1`) even when the scheduler is wedged — the
    C-level handler doesn't need the GIL. Paired with the watchdog's ``py-spy``
    capture (deploy/watchdog/watchdog.sh) to root-cause the 2026-08-15/17 silent
    31h scheduler stall, which left no logs. Best-effort; never raises.
    """
    import faulthandler
    import signal

    try:
        faulthandler.enable()
        if hasattr(signal, "SIGUSR1"):
            faulthandler.register(signal.SIGUSR1, all_threads=True, chain=False)
    except (ValueError, OSError, RuntimeError) as e:  # pragma: no cover
        log.warning("Crash diagnostics not enabled: %s", e)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings)
    _enable_crash_diagnostics()
    init_db()
    scheduler = None
    if settings.ib_flex_token and settings.ib_flex_query_id:
        from ..jobs import start_scheduler

        scheduler = start_scheduler()
    else:
        log.info(
            "Scheduler not started: IB_FLEX_TOKEN / IB_FLEX_QUERY_ID not set. "
            "API still serves whatever data is in the DB."
        )
    yield
    if scheduler is not None:
        from ..jobs import stop_scheduler

        stop_scheduler()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="IBKR Dashboard Backend",
        description=(
            "Read-only API powering an IBKR personal portfolio dashboard. "
            "Sources: Flex Web Service (EOD truth) + ib_async (live overlay)."
        ),
        version="0.1.0",
        lifespan=_lifespan,
    )
    # An empty CORS_ORIGINS must NOT fall back to "*": wildcard origins
    # combined with allow_credentials=True make Starlette reflect ANY Origin
    # with credentials. Fall back to the safe localhost defaults instead.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list
        or ["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(portfolio_router, prefix="/portfolio", tags=["portfolio"])
    app.include_router(dividends_router, prefix="/dividends", tags=["dividends"])
    app.include_router(commissions_router, prefix="/commissions", tags=["commissions"])
    app.include_router(performance_router, prefix="/performance", tags=["performance"])
    app.include_router(pipeline_router, prefix="/pipeline", tags=["pipeline"])
    app.include_router(trades_router, prefix="/trades", tags=["trades"])
    app.include_router(options_router, prefix="/options", tags=["options"])
    app.include_router(tax_router, prefix="/tax", tags=["tax"])
    app.include_router(integrations_router, prefix="/integrations", tags=["integrations"])
    app.include_router(config_router, prefix="/config", tags=["config"])

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    @app.get("/data/coverage", tags=["meta"])
    def data_coverage(session: SessionDep):
        t = session.execute(
            select(func.min(Trade.trade_date), func.max(Trade.trade_date), func.count(Trade.trade_id))
        ).one()
        ct = session.execute(
            select(
                func.min(CashTransaction.settle_date),
                func.max(CashTransaction.settle_date),
                func.count(CashTransaction.transaction_id),
            )
        ).one()
        pos_snap = session.execute(select(func.max(PositionEod.snapshot_date))).scalar()
        nav_snap = session.execute(select(func.max(NavDaily.snapshot_date))).scalar()

        def _s(d) -> str | None:
            return d.isoformat() if d else None

        candidates = [d for d in [_s(t[1]), _s(ct[1]), _s(nav_snap)] if d]
        last_date = max(candidates) if candidates else None
        firsts = [d for d in [_s(t[0]), _s(ct[0])] if d]
        first_date = min(firsts) if firsts else None
        days_stale = (date.today() - date.fromisoformat(last_date)).days if last_date else None

        return {
            "first_date": first_date,
            "last_date": last_date,
            "days_stale": days_stale,
            "sources": {
                "trades": {"first": _s(t[0]), "last": _s(t[1]), "count": t[2]},
                "cash_transactions": {"first": _s(ct[0]), "last": _s(ct[1]), "count": ct[2]},
                "positions": {"last_snapshot": _s(pos_snap)},
            },
        }

    return app


app = create_app()
