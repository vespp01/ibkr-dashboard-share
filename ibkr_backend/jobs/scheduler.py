"""APScheduler-based daily Flex pull.

Started by the FastAPI lifespan; can also run standalone:

    python -m ibkr_backend.jobs.scheduler
"""

from __future__ import annotations

import logging
import signal
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import get_settings
from ..loaders.pipeline import run_full_pipeline, run_tc_pipeline

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def _daily_job() -> None:
    settings = get_settings()
    _notify_token_expiry(settings)  # fire even if the pull below fails
    if not settings.ib_flex_token or not settings.ib_flex_query_id:
        log.warning("Daily job skipped: Flex token / query id not configured.")
        return
    try:
        result = run_full_pipeline(note="scheduled")  # uses query's configured period
        log.info(
            "Daily Flex run ok: ref=%s inserted=%d skipped=%d",
            result.reference_code, result.total_inserted, result.total_skipped,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("Daily Flex run failed: %s", e)
        return
    _notify_after_pull()


def _notify_after_pull() -> None:
    """Send the daily Telegram digest (dividends / operations / commissions).

    Strictly best-effort: any failure is logged and swallowed so a Telegram
    outage can never affect the Flex pipeline. No-op unless Telegram is
    configured (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`).
    """
    from ..db import session_scope
    from ..notify import is_configured
    from ..notify.reports import daily_notify

    if not is_configured():
        return
    try:
        with session_scope() as s:
            summary = daily_notify(s)
        log.info("Telegram digest: %s", summary)
    except Exception as e:  # noqa: BLE001
        log.warning("Telegram digest failed: %s", e)


def _notify_token_expiry(settings=None) -> None:
    """Telegram-warn when the Flex token is near (or past) its expiry date.

    Called at the top of the daily job so it fires even when the pull itself
    fails — an expired token is exactly when the pull breaks. No-op unless
    `IB_FLEX_TOKEN_EXPIRY` is set and Telegram is configured. Best-effort: any
    failure is logged and swallowed.
    """
    from datetime import date

    from ..notify import is_configured, send_message
    from ..notify.reports import token_expiry_message

    s = settings or get_settings()
    if not s.ib_flex_token_expiry or not is_configured(s):
        return
    try:
        expiry = date.fromisoformat(s.ib_flex_token_expiry)
    except ValueError:
        log.warning("IB_FLEX_TOKEN_EXPIRY not a valid YYYY-MM-DD: %r", s.ib_flex_token_expiry)
        return
    msg = token_expiry_message(expiry, date.today(), s.token_expiry_warn_days)
    if not msg:
        return
    try:
        send_message(msg, settings=s)
        log.info("Token-expiry warning sent (expiry=%s).", expiry)
    except Exception as e:  # noqa: BLE001
        log.warning("Token-expiry warning failed: %s", e)


def _tc_job() -> None:
    """Hourly Trade Confirmation pull — captures intraday fills.

    Skipped silently if the TC query id isn't configured; the broader
    `pipeline_cron` daily job remains the source of truth for everything else.
    """
    settings = get_settings()
    if not settings.ib_flex_token or not settings.ib_flex_query_id_tc:
        log.debug("TC job skipped: token / IB_FLEX_QUERY_ID_TC not configured.")
        return
    try:
        result = run_tc_pipeline(note="scheduled-tc")
        log.info(
            "TC run ok: ref=%s inserted=%d skipped=%d",
            result.reference_code, result.total_inserted, result.total_skipped,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("TC run failed: %s", e)


def _price_alert_job() -> None:
    """Poll IB Gateway quotes and Telegram-alert on price-threshold crossings.

    No-op unless the live overlay is on AND Telegram is configured. Strictly
    best-effort — a quote or send failure is logged and swallowed.
    """
    settings = get_settings()
    if not settings.enable_live_overlay:
        return
    from ..db import session_scope
    from ..notify import is_configured
    from ..notify.price_alerts import check_price_alerts

    if not is_configured(settings):
        return
    try:
        with session_scope() as s:
            summary = check_price_alerts(s, settings=settings)
        if summary.get("fired"):
            log.info("Price alerts fired: %s", summary["fired"])
    except Exception as e:  # noqa: BLE001
        log.warning("Price alert job failed: %s", e)


def _staleness_watchdog() -> None:
    """Periodic check: warn if the last successful Flex pull is too old.

    The daily job logs its own failures, but a *silently not-running* scheduler
    (or a long string of failures) leaves no signal — this watchdog is the
    catch-all. There's no push channel, so the WARNING log is the alert; an
    external monitor or `GET /pipeline/status` (`staleness.is_stale`) can act.
    """
    settings = get_settings()
    if not settings.ib_flex_token or not settings.ib_flex_query_id:
        return
    from ..db import session_scope
    from .health import compute_staleness

    with session_scope() as s:
        st = compute_staleness(
            s, threshold_hours=settings.pipeline_stale_after_hours
        )
    if st["is_stale"]:
        log.warning(
            "Pipeline STALE: last successful Flex run %s (age=%sh > %sh threshold). "
            "Check the Flex token/query id and that the scheduler is running.",
            st["last_success_at"], st["age_hours"], st["threshold_hours"],
        )
    else:
        log.debug("Pipeline fresh: last success age=%sh", st["age_hours"])


def build_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    sched = BackgroundScheduler()
    sched.add_job(
        _daily_job,
        trigger=CronTrigger.from_crontab(settings.pipeline_cron),
        id="daily_flex_pull",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60 * 60,
    )
    sched.add_job(
        _staleness_watchdog,
        trigger=CronTrigger.from_crontab(settings.staleness_watchdog_cron),
        id="staleness_watchdog",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        _tc_job,
        trigger=CronTrigger.from_crontab(settings.tc_cron),
        id="tc_flex_pull",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30 * 60,
    )
    sched.add_job(
        _price_alert_job,
        trigger=CronTrigger.from_crontab(settings.price_alert_cron),
        id="price_alerts",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return sched


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = build_scheduler()
    _scheduler.start()
    s = get_settings()
    log.info(
        "Scheduler started (daily=%s, tc=%s tc_id_set=%s)",
        s.pipeline_cron, s.tc_cron, bool(s.ib_flex_query_id_tc),
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def _run_blocking() -> None:
    sched = start_scheduler()
    log.info("APScheduler standalone runner. Press Ctrl+C to stop.")
    stop = False

    def _handler(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handler)
    try:
        signal.signal(signal.SIGTERM, _handler)
    except Exception:
        pass
    while not stop:
        time.sleep(1)
    sched.shutdown(wait=False)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _run_blocking()
