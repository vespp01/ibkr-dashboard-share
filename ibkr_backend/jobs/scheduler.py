"""APScheduler-based daily Flex pull.

Started by the FastAPI lifespan; can also run standalone:

    python -m ibkr_backend.jobs.scheduler
"""

from __future__ import annotations

import hashlib
import logging
import signal
import threading
import time
from datetime import UTC, date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import get_settings
from ..loaders.pipeline import run_full_pipeline, run_tc_pipeline

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


_CATCHUP_BY_FREQ = {"weekly": 8, "biweekly": 15, "monthly": 32}

# Scheduler liveness heartbeat. A fixed job stamps a timestamp every few minutes
# (and once at startup); an external watchdog reads it via GET /pipeline/status
# (`scheduler.heartbeat_age_seconds`) to detect a wedged scheduler within
# minutes — far sooner than waiting for data to cross the 30h staleness
# threshold. See docs: the 2026-08-15/17 incident (scheduler silent 31h while
# the HTTP server stayed up, so the healthcheck never noticed).
_HEARTBEAT_CRON = "*/5 * * * *"
_HEARTBEAT_MISFIRE_GRACE = 120
# Grace so a brief scheduler stall doesn't silently drop a configurable job's
# fire (APScheduler's default misfire_grace_time is 1 second).
_CONFIGURABLE_JOB_MISFIRE_GRACE = 900
# The jobstore is in memory, so a daily cron slot that passed while the backend
# was down is never run (misfire grace only helps a *running* scheduler). At
# startup the daily job is moved this far ahead when that slot was missed
# (e.g. the backend was down over the 06:30 slot, so that pull never ran).
_STARTUP_CATCHUP_DELAY_SECONDS = 120
# No catch-up when the cron fires this soon anyway: a catch-up still running at
# the slot would make max_instances=1 drop the real fire.
_STARTUP_CATCHUP_MIN_CRON_LEAD = timedelta(hours=1)


def _stamp_liveness(key: str) -> None:
    """Write ``now`` to ``NotificationState[key]``.

    Best-effort — never raises, so a transient DB hiccup can't kill the caller.
    """
    try:
        from ..db import session_scope
        from ..db.models import NotificationState, _utcnow

        now_iso = _utcnow().isoformat()
        with session_scope() as s:
            row = s.get(NotificationState, key)
            if row is None:
                s.add(NotificationState(key=key, value=now_iso))
            else:
                row.value = now_iso
    except Exception as e:  # noqa: BLE001
        log.warning("Scheduler liveness stamp (%s) failed: %s", key, e)


def _stamp_heartbeat() -> None:
    """Prove the scheduler main loop is alive (fixed 5-min job)."""
    from .health import SCHEDULER_HEARTBEAT_KEY

    _stamp_liveness(SCHEDULER_HEARTBEAT_KEY)


def _stamp_pull_attempt() -> None:
    """Prove the scheduler is still *dispatching* the pull jobs.

    Called at the very start of `_daily_job` / `_tc_job`, before any config gate
    or network call — so an upstream token/query/IBKR *failure* still refreshes
    it. The watchdog reads a fresh heartbeat + a stale pull-attempt as a dispatch
    wedge (the 2026-08-22 incident: pull jobs' `max_instances` slot stuck while
    the heartbeat kept ticking). Because upstream errors still stamp an attempt,
    this never false-fires on a token/query problem a restart can't fix.
    """
    from .health import SCHEDULER_PULL_ATTEMPT_KEY

    _stamp_liveness(SCHEDULER_PULL_ATTEMPT_KEY)


def _resolve_config_safe(settings=None):
    """Resolve the notification config; ``None`` on any error (callers stay permissive)."""
    try:
        from ..notify.config_store import resolve_notification_config

        return resolve_notification_config(settings=settings or get_settings())
    except Exception as e:  # noqa: BLE001
        log.warning("Notification config unavailable; sending with defaults: %s", e)
        return None


_CADENCE_SENT_PREFIX = "cadence_sent:"


def _cadence_last_sent(message_id: str) -> date | None:
    """Day this chained message was last delivered (``None`` if never / unreadable)."""
    try:
        from ..db import session_scope
        from ..db.models import NotificationState

        with session_scope() as s:
            row = s.get(NotificationState, _CADENCE_SENT_PREFIX + message_id)
            return date.fromisoformat(row.value) if row is not None and row.value else None
    except Exception as e:  # noqa: BLE001
        log.warning("Cadence marker read (%s) failed: %s", message_id, e)
        return None


def _mark_cadence_sent(message_id: str, day: date | None = None) -> None:
    """Record a delivery so the period gate does not send again this week/month."""
    try:
        from ..db import session_scope
        from ..db.models import NotificationState

        value = (day or date.today()).isoformat()
        with session_scope() as s:
            key = _CADENCE_SENT_PREFIX + message_id
            row = s.get(NotificationState, key)
            if row is None:
                s.add(NotificationState(key=key, value=value))
            else:
                row.value = value
    except Exception as e:  # noqa: BLE001
        log.warning("Cadence marker write (%s) failed: %s", message_id, e)


def _chained_gate(config, message_id):
    """(enabled, due_today, message_cfg) for a chained message.

    ``due_today`` is the period gate (``cadence_due``): for weekly / biweekly /
    monthly it means "this period's slot has arrived and the message was not yet
    sent this ISO week / month", so a pull that failed on the cadence day does
    not drop the message (B44). Permissive ``(True, True, {})`` when config is
    missing — preserves the pre-config behaviour of always sending after a pull.
    """
    from ..notify.cadence import cadence_due, normalize_cadence

    if config is None:
        return True, True, {}
    msg = config.get("messages", {}).get(message_id)
    if not msg:
        return True, True, {}
    cadence = msg.get("cadence", {})
    last_sent = None
    if normalize_cadence(cadence)["freq"] in ("weekly", "biweekly", "monthly"):
        last_sent = _cadence_last_sent(message_id)
    allowed = cadence_due(cadence, date.today(), last_sent)
    return bool(msg.get("enabled", True)), allowed, msg


def _catchup_for(cadence) -> int:
    """Scale the digest catch-up window to the cadence so a weekly/monthly gap
    is not silently truncated by the default 14-day cap."""
    return _CATCHUP_BY_FREQ.get((cadence or {}).get("freq"), 14)


# Pull suspension after a dead-token code (B39). IBKR answered 1012/1025 to every
# hourly pull for 4.8 days (57x + 64x) until the token was renewed. A retry cannot
# succeed until IB_FLEX_TOKEN changes, so the jobs skip the network call while the
# configured token still has the same fingerprint. Only a hash prefix is stored.
_PULLS_SUSPENDED_KEY = "flex_pulls_suspended"
# One daily-pull body at a time: the 06:30 slot, its same-day retries and the
# startup catch-up all run `_daily_job` from different APScheduler jobs.
_DAILY_PULL_LOCK = threading.Lock()


def _token_fingerprint(token: str) -> str:
    """Short irreversible fingerprint of the token (never log or store the token)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _pulls_suspended(settings) -> bool:
    """True while the configured token is the one that got a dead-token code."""
    try:
        from ..db import session_scope
        from ..db.models import NotificationState

        with session_scope() as s:
            row = s.get(NotificationState, _PULLS_SUSPENDED_KEY)
            value = row.value if row is not None else ""
    except Exception as e:  # noqa: BLE001
        log.warning("Pull-suspension check failed (pulling anyway): %s", e)
        return False
    if not value or not settings.ib_flex_token:
        return False
    return value.partition("|")[0] == _token_fingerprint(settings.ib_flex_token)


def _set_pull_suspension(settings, code: str | None) -> None:
    """Suspend (``code`` set) or resume (``code`` None) pulls for this token."""
    try:
        from ..db import session_scope
        from ..db.models import NotificationState

        with session_scope() as s:
            row = s.get(NotificationState, _PULLS_SUSPENDED_KEY)
            if code is None:
                if row is not None:
                    s.delete(row)
                return
            value = f"{_token_fingerprint(settings.ib_flex_token)}|{code}"
            if row is None:
                s.add(NotificationState(key=_PULLS_SUSPENDED_KEY, value=value))
            else:
                row.value = value
    except Exception as e:  # noqa: BLE001
        log.warning("Pull-suspension write failed: %s", e)


def _note_pull_failure(exc: Exception, settings) -> None:
    """Suspend the pulls when Flex reported the token itself as dead."""
    from ..flex.errors import TOKEN_DEAD_CODES

    code = getattr(exc, "code", None)
    if code in TOKEN_DEAD_CODES:
        _set_pull_suspension(settings, code)
        log.error(
            "Flex token rejected (code %s): scheduled pulls suspended until "
            "IB_FLEX_TOKEN changes. Renew it (docs/setup/flex-queries.md §3) and "
            "recreate the backend container.", code,
        )


def _note_pull_success(settings) -> None:
    """A pull went through: drop any suspension marker (also one left by an old token)."""
    _set_pull_suspension(settings, None)


def _daily_job() -> None:
    _stamp_pull_attempt()  # proves dispatch even if the pull below fails/skips
    settings = get_settings()
    config = _resolve_config_safe(settings)
    _notify_token_expiry(settings, config=config)  # fire even if the pull below fails
    if not settings.ib_flex_token or not settings.ib_flex_query_id:
        log.warning("Daily job skipped: Flex token / query id not configured.")
        return
    if _pulls_suspended(settings):
        log.warning("Daily job skipped: Flex pulls suspended (dead token) until IB_FLEX_TOKEN changes.")
        return
    if not _DAILY_PULL_LOCK.acquire(blocking=False):
        log.info("Daily job skipped: another daily pull is still running.")
        return
    try:
        result = run_full_pipeline(note="scheduled")  # uses query's configured period
        log.info(
            "Daily Flex run ok: ref=%s inserted=%d skipped=%d",
            result.reference_code, result.total_inserted, result.total_skipped,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("Daily Flex run failed: %s", e)
        _note_pull_failure(e, settings)
        return
    finally:
        _DAILY_PULL_LOCK.release()
    _note_pull_success(settings)
    _notify_after_pull(config=config)
    _margin_eod_alert_after_pull(config=config)
    _margin_usage_after_pull(config=config)


def _notify_after_pull(config=None) -> None:
    """Send the daily Telegram digest + performance pulse.

    Strictly best-effort: any failure is logged and swallowed so a Telegram
    outage can never affect the Flex pipeline. No-op unless Telegram is
    configured (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`). Each message honours
    its dashboard config (enabled + cadence + digest sections). The digest
    (transactional) and the pulse (portfolio performance state) run in separate
    transactions so one failing can't suppress the other.
    """
    from ..db import session_scope
    from ..notify import is_configured
    from ..notify.reports import daily_notify, send_performance_pulse

    if not is_configured():
        return
    digest_enabled, digest_allowed, digest_cfg = _chained_gate(config, "daily_digest")
    if digest_enabled and digest_allowed:
        try:
            with session_scope() as s:
                summary = daily_notify(
                    s,
                    sections=digest_cfg.get("sections"),
                    max_catchup_days=_catchup_for(digest_cfg.get("cadence")),
                )
            log.info("Telegram digest: %s", summary)
            if summary.get("sent") and not summary.get("outage"):
                _mark_cadence_sent("daily_digest")
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram digest failed: %s", e)
    pulse_enabled, pulse_allowed, _ = _chained_gate(config, "performance_pulse")
    if pulse_enabled and pulse_allowed:
        try:
            with session_scope() as s:
                pulse = send_performance_pulse(s)
            log.info("Performance pulse: %s", pulse)
            if pulse.get("sent"):
                _mark_cadence_sent("performance_pulse")
        except Exception as e:  # noqa: BLE001
            log.warning("Performance pulse failed: %s", e)


_TOKEN_EXPIRY_KEY = "token_expiry_last_sig"


def _token_expiry_bucket(days: int, warn_days: int) -> str | None:
    """Milestone bucket for a token that expires in `days` days.

    Returns ``"expired"`` once past, the reached milestone (``warn_days``/3/1/0)
    while inside the warning window, or ``None`` when still further out. The
    bucket only changes as a milestone is crossed, so the caller sends one push
    per milestone instead of one every single day of the window.
    """
    if days < 0:
        return "expired"
    milestones = sorted({warn_days, 3, 1, 0} & set(range(0, warn_days + 1)))
    for m in milestones:  # smallest milestone we've reached (days <= m)
        if days <= m:
            return str(m)
    return None


def _notify_token_expiry(settings=None, config=None) -> None:
    """Telegram-warn when the Flex token is near (or past) its expiry date.

    Called at the top of the daily job so it fires even when the pull itself
    fails — an expired token is exactly when the pull breaks. No-op unless
    `IB_FLEX_TOKEN_EXPIRY` is set and Telegram is configured. Fires once per
    milestone (deduped via `NotificationState`), not once per day, so a 7-day
    window is at most ~4 pushes. Best-effort: any failure is logged and swallowed.
    """
    from datetime import date

    from ..db import session_scope
    from ..db.models import NotificationState
    from ..notify import is_configured, send_message
    from ..notify.reports import token_expiry_message

    s = settings or get_settings()
    if not s.ib_flex_token_expiry or not is_configured(s):
        return
    enabled, allowed, _ = _chained_gate(config, "token_expiry")
    if not (enabled and allowed):
        return
    try:
        expiry = date.fromisoformat(s.ib_flex_token_expiry)
    except ValueError:
        log.warning("IB_FLEX_TOKEN_EXPIRY not a valid YYYY-MM-DD: %r", s.ib_flex_token_expiry)
        return
    today = date.today()
    bucket = _token_expiry_bucket((expiry - today).days, s.token_expiry_warn_days)
    if bucket is None:  # still outside the warning window
        return
    sig = f"{expiry.isoformat()}|{bucket}"
    with session_scope() as sess:
        row = sess.get(NotificationState, _TOKEN_EXPIRY_KEY)
        if row is not None and row.value == sig:
            return  # this milestone already pushed
    msg = token_expiry_message(expiry, today, s.token_expiry_warn_days)
    if not msg:
        return
    try:
        delivered = send_message(msg, settings=s)
    except Exception as e:  # noqa: BLE001
        log.warning("Token-expiry warning failed: %s", e)
        return  # don't advance the marker → retry next run (at-least-once)
    if not delivered:
        return  # Telegram not-ok (HTTP 200 {ok:false}) → don't advance, retry
    with session_scope() as sess:  # advance only after a successful send
        row = sess.get(NotificationState, _TOKEN_EXPIRY_KEY)
        if row is None:
            sess.add(NotificationState(key=_TOKEN_EXPIRY_KEY, value=sig))
        else:
            row.value = sig
    log.info("Token-expiry warning sent (expiry=%s, milestone=%s).", expiry, bucket)


def _tc_job() -> None:
    """Hourly Trade Confirmation pull — captures intraday fills.

    Skipped silently if the TC query id isn't configured; the broader
    `pipeline_cron` daily job remains the source of truth for everything else.
    """
    _stamp_pull_attempt()  # hourly dispatch heartbeat the watchdog watches
    settings = get_settings()
    if not settings.ib_flex_token or not settings.ib_flex_query_id_tc:
        log.debug("TC job skipped: token / IB_FLEX_QUERY_ID_TC not configured.")
        return
    if _pulls_suspended(settings):
        log.debug("TC job skipped: Flex pulls suspended (dead token).")
        return
    try:
        result = run_tc_pipeline(note="scheduled-tc")
        log.info(
            "TC run ok: ref=%s inserted=%d skipped=%d",
            result.reference_code, result.total_inserted, result.total_skipped,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("TC run failed: %s", e)
        _note_pull_failure(e, settings)


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


def _margin_live_alert_job() -> None:
    """Check IBKR live margin values and notify only on a risk transition."""
    settings = get_settings()
    if not settings.margin_alerts_enabled or not settings.enable_live_overlay:
        return
    from ..db import session_scope
    from ..notify import is_configured
    from ..notify.margin_alerts import check_margin_alert

    if not is_configured(settings):
        return
    try:
        with session_scope() as s:
            summary = check_margin_alert(s, settings=settings)
        if summary.get("sent"):
            log.info("Live margin alert sent: level=%s", summary.get("level"))
    except Exception as e:  # noqa: BLE001
        log.warning("Live margin alert job failed: %s", e)


def _margin_eod_alert_after_pull(config=None) -> None:
    """Evaluate the latest Flex snapshot as a labelled EOD fallback."""
    settings = get_settings()
    enabled, allowed, _ = _chained_gate(config, "margin_eod")
    if not (enabled and allowed):
        return
    from ..analytics.margin import margin_summary
    from ..db import session_scope
    from ..notify import is_configured
    from ..notify.margin_alerts import build_eod_snapshot, check_margin_alert

    if not is_configured(settings):
        return
    try:
        with session_scope() as s:
            snapshot = build_eod_snapshot(margin_summary(s))
            summary = check_margin_alert(
                s,
                snapshot_fn=lambda: snapshot,
                settings=settings,
            )
        if summary.get("sent"):
            log.info("EOD margin estimate alert sent: level=%s", summary.get("level"))
            _mark_cadence_sent("margin_eod")
    except Exception as e:  # noqa: BLE001
        log.warning("EOD margin estimate alert failed: %s", e)


def _margin_usage_after_pull(config=None) -> None:
    """Periodic margin-usage status (+ threshold escalation), EOD estimate today.

    The `enabled` gate turns the whole message off. The cadence gate only limits
    the recurring *status* — a worsening band still escalates immediately (the
    alert path is independent of the periodic cadence).
    """
    settings = get_settings()
    enabled, allowed, msg_cfg = _chained_gate(config, "margin_usage")
    if not enabled:
        return
    from ..db import session_scope
    from ..notify import is_configured
    from ..notify.margin_usage import check_margin_usage

    if not is_configured(settings):
        return
    alert_bands = bool(msg_cfg.get("alert_bands", True)) if msg_cfg else True
    try:
        with session_scope() as s:
            summary = check_margin_usage(
                s,
                settings=settings,
                periodic_allowed=allowed,
                alert_bands=alert_bands,
            )
        if summary.get("sent"):
            log.info("Margin usage message sent: level=%s", summary.get("level"))
            if summary.get("periodic_due"):
                _mark_cadence_sent("margin_usage")
    except Exception as e:  # noqa: BLE001
        log.warning("Margin usage message failed: %s", e)


def _margin_fx_job() -> None:
    """Send the source-traceable margin funding-currency review at its cadence."""
    from datetime import date

    settings = get_settings()
    if not settings.margin_fx_enabled:
        return
    from ..db import session_scope
    from ..notify import is_configured
    from ..notify.cadence import is_active_iso_week
    from ..notify.config_store import resolve_notification_config
    from ..notify.margin_financing import check_margin_financing_notice

    if not is_configured(settings):
        return
    # Honor the dashboard cadence: the enabled toggle and — for a biweekly
    # cadence, which sync compiles to a plain weekly cron — the every-other-week
    # parity gate (APScheduler can't express "every 2 weeks" in cron).
    fx = resolve_notification_config(settings=settings)["messages"]["margin_fx"]
    if not fx["enabled"]:
        return
    if fx["cadence"].get("freq") == "biweekly" and not is_active_iso_week(date.today()):
        log.debug("Margin-fx skipped: biweekly parity (inactive ISO week).")
        return
    try:
        with session_scope() as s:
            summary = check_margin_financing_notice(s, settings=settings)
        if summary.get("sent"):
            log.info(
                "Margin financing notice sent: candidate=%s",
                summary.get("robust_candidate"),
            )
    except Exception as e:  # noqa: BLE001
        log.warning("Margin financing notice job failed: %s", e)


def _backup_age_check(session, settings) -> None:
    """Best-effort backup-age alert (B42), part of the 6-hourly watchdog tick."""
    if not settings.backup_dir:
        return
    from ..notify.staleness import check_backup_age_alert
    from .backup import latest_backup_info

    info = latest_backup_info(
        settings.backup_dir, stale_after_hours=settings.backup_stale_after_hours
    )
    if info["is_overdue"]:
        log.warning(
            "Nightly DB backup OVERDUE: newest file %s (age=%sh > %sh).",
            info["last_backup_at"], info["age_hours"], info["threshold_hours"],
        )
    try:
        check_backup_age_alert(session, settings=settings, info=info)
    except Exception as e:  # noqa: BLE001
        log.warning("Backup-age alert failed: %s", e)


def _staleness_watchdog() -> None:
    """Periodic check: warn if the last successful Flex pull is too old.

    The daily job logs its own failures, but a *silently not-running* scheduler
    (or a long string of failures) leaves no signal — this watchdog is the
    catch-all. It logs a WARNING and, when Telegram is configured, pushes one
    deduped alert per stale episode; `GET /pipeline/status` (`staleness.is_stale`)
    still exposes the flag for any external monitor.
    """
    settings = get_settings()
    if settings.backup_dir:  # independent of Flex: the DB backup can fail on its own
        from ..db import session_scope as _backup_scope

        with _backup_scope() as bs:
            _backup_age_check(bs, settings)
    if not settings.ib_flex_token or not settings.ib_flex_query_id:
        return
    from ..db import session_scope
    from ..notify.staleness import check_data_age_alert, check_staleness_alert
    from .health import compute_data_freshness, compute_staleness

    with session_scope() as s:
        st = compute_staleness(
            s, threshold_hours=settings.pipeline_stale_after_hours,
            query_id=settings.ib_flex_query_id or None,
        )
        if st["is_stale"]:
            log.warning(
                "Pipeline STALE: last successful Flex run %s (age=%sh > %sh threshold). "
                "Check the Flex token/query id and that the scheduler is running.",
                st["last_success_at"], st["age_hours"], st["threshold_hours"],
            )
        else:
            log.debug("Pipeline fresh: last success age=%sh", st["age_hours"])
        # Best-effort deduped Telegram push (no-op unless configured).
        try:
            check_staleness_alert(s, settings=settings, staleness=st)
        except Exception as e:  # noqa: BLE001
            log.warning("Staleness alert failed: %s", e)
        # Data-age rule (2026-09-12): pulls in `success` but positions frozen.
        # Only evaluated while the pull itself is fresh — a stale pull already
        # fired the alert above and the data is trivially old then.
        if not st["is_stale"]:
            fr = compute_data_freshness(
                s, threshold_business_days=settings.data_stale_after_business_days
            )
            if fr["data_is_stale"]:
                log.warning(
                    "Data STALE despite successful pulls: newest positions snapshot %s "
                    "(%s business days > %s). Check the Activity query period in the Client Portal.",
                    fr["last_data_date"], fr["age_business_days"], fr["threshold_business_days"],
                )
            try:
                check_data_age_alert(s, settings=settings, freshness=fr)
            except Exception as e:  # noqa: BLE001
                log.warning("Data-age alert failed: %s", e)


# Notification jobs whose APScheduler cron is driven by the dashboard config
# (`GET/POST /config/notifications`). The daily pull and TC pull are NOT
# notifications and are never reconfigured here. `kind` matches the config
# schema: `interval` jobs keep their env cron and only toggle on/off; `cron`
# jobs compile their cadence to a fresh crontab and are rescheduled live.
_CONFIGURABLE_JOBS: tuple[dict, ...] = (
    {"job_id": "staleness_watchdog", "message_id": "staleness",
     "func": _staleness_watchdog, "cron_attr": "staleness_watchdog_cron", "kind": "interval"},
    {"job_id": "price_alerts", "message_id": "price_alerts",
     "func": _price_alert_job, "cron_attr": "price_alert_cron", "kind": "interval"},
    {"job_id": "margin_live_alerts", "message_id": "margin_live",
     "func": _margin_live_alert_job, "cron_attr": "margin_alert_cron", "kind": "interval"},
    {"job_id": "margin_financing_notice", "message_id": "margin_fx",
     "func": _margin_fx_job, "cron_attr": "margin_fx_cron", "kind": "cron"},
)


# Interval jobs that only do work with the IB Gateway live overlay on. While
# ENABLE_LIVE_OVERLAY is false they are not scheduled at all (they fired ~576
# no-op times a day: */3 margin + */15 price).
_OVERLAY_JOB_IDS = frozenset({"price_alerts", "margin_live_alerts"})


def _effective_cron_for(job: dict, config: dict | None, settings) -> str | None:
    """Resolve the crontab for a configurable job, or ``None`` when disabled.

    Falls back to the env cron when config is unavailable (never disables on a
    resolver error). `cron`-kind jobs compile their cadence; `off` cadence →
    ``None`` (removed). Live-overlay jobs are ``None`` while the overlay is off.
    """
    if job["job_id"] in _OVERLAY_JOB_IDS and not settings.enable_live_overlay:
        return None
    env_cron = getattr(settings, job["cron_attr"])
    if config is None:
        return env_cron
    msg = config.get("messages", {}).get(job["message_id"], {})
    if not msg.get("enabled", True):
        return None
    if job["kind"] == "interval":
        return env_cron
    from ..notify.cadence import cadence_to_cron

    return cadence_to_cron(msg.get("cadence", {}), fallback_cron=env_cron)


def _backup_job() -> None:
    """Nightly SQLite backup into ``BACKUP_DIR`` (bind mount on the host)."""
    from pathlib import Path

    from .backup import backup_sqlite_database

    settings = get_settings()
    try:
        path = backup_sqlite_database(
            settings.database_url, Path(settings.backup_dir), keep=settings.backup_keep
        )
        log.info("Nightly DB backup written: %s", path.name)
    except Exception as e:  # noqa: BLE001
        log.exception("Nightly DB backup failed: %s", e)
        _alert_backup_failure(e)


def _alert_backup_failure(error: Exception) -> None:
    """One Telegram line per failed night, so the only DB copy cannot fail
    silently. Best-effort, and a no-op without Telegram."""
    from html import escape

    from ..notify import is_configured, send_message

    if not is_configured():
        return
    try:
        sent = send_message(
            "🟠 <b>IBKR · backup nocturno de la base falló</b>\n"
            f"{escape(type(error).__name__)}: {escape(str(error)[:300])}\n"
            "Revisa: docker compose logs backend (busca Nightly DB backup) · "
            "ls -l ~/.ibkr-backups"
        )
        if not sent:
            log.warning("Backup failure alert was not accepted by Telegram")
    except Exception as e:  # noqa: BLE001
        log.warning("Backup failure alert send failed: %s", e)


def build_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    sched = BackgroundScheduler()
    # Fixed, non-notification jobs — always scheduled from env crons.
    sched.add_job(
        _daily_job,
        trigger=CronTrigger.from_crontab(settings.pipeline_cron),
        id="daily_flex_pull",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        # Wide grace so a sleep-prone host (OrbStack on a Mac that slept past
        # 06:30) still fires ONE coalesced make-up pull on wake — the pull is
        # idempotent (IBKR-supplied ids), so a late wake refreshes same-day with
        # no duplicate/burst. 1h was too short for a laptop deployment.
        misfire_grace_time=12 * 60 * 60,
    )
    try:  # a bad PIPELINE_RETRY_CRON must not take the scheduler down either
        sched.add_job(
            _daily_retry_job,
            trigger=CronTrigger.from_crontab(settings.pipeline_retry_cron),
            id="daily_flex_pull_retry",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=15 * 60,
        )
    except ValueError as e:
        log.error("Daily pull retry disabled: invalid PIPELINE_RETRY_CRON %r (%s)",
                  settings.pipeline_retry_cron, e)
    sched.add_job(
        _tc_job,
        trigger=CronTrigger.from_crontab(settings.tc_cron),
        id="tc_flex_pull",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30 * 60,
    )
    if settings.backup_dir:
        try:  # a bad BACKUP_CRON must not take the scheduler (and the API) down
            sched.add_job(
                _backup_job,
                trigger=CronTrigger.from_crontab(settings.backup_cron),
                id="nightly_db_backup",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=12 * 60 * 60,  # a host asleep at 03:45 backs up on wake
            )
        except ValueError as e:
            log.error("Nightly DB backup disabled: invalid BACKUP_CRON %r (%s)", settings.backup_cron, e)
    try:
        from .retention import flex_runs_retention_job

        sched.add_job(
            flex_runs_retention_job,
            trigger=CronTrigger.from_crontab(settings.flex_runs_retention_cron),
            id="flex_runs_retention",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=12 * 60 * 60,
        )
    except ValueError as e:
        log.error("flex_runs retention disabled: invalid FLEX_RUNS_RETENTION_CRON %r (%s)",
                  settings.flex_runs_retention_cron, e)
    # Liveness heartbeat — always on, independent of any config.
    sched.add_job(
        _stamp_heartbeat,
        trigger=CronTrigger.from_crontab(_HEARTBEAT_CRON),
        id="scheduler_heartbeat",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=_HEARTBEAT_MISFIRE_GRACE,
    )
    # Configurable notification jobs — respect the persisted dashboard config at
    # startup (a disabled job stays disabled after a restart). Fall back to env
    # crons if the config can't be resolved yet.
    try:
        from ..notify.config_store import resolve_notification_config

        config = resolve_notification_config(settings=settings)
    except Exception as e:  # noqa: BLE001
        log.warning("Notification config unavailable at build; using env crons: %s", e)
        config = None
    for job in _CONFIGURABLE_JOBS:
        cron = _effective_cron_for(job, config, settings)
        if cron is None:
            continue
        try:
            sched.add_job(
                job["func"],
                trigger=CronTrigger.from_crontab(cron),
                id=job["job_id"],
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=_CONFIGURABLE_JOB_MISFIRE_GRACE,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Could not schedule %s (%s): %s", job["job_id"], cron, e)
    return sched


def get_scheduler() -> BackgroundScheduler | None:
    """The running scheduler singleton, or ``None`` when it was never started."""
    return _scheduler


def sync_scheduler_jobs(config: dict | None = None) -> dict:
    """Apply a notification config to the live scheduler (reschedule/add/remove).

    Called after a config save. No-op with a clear reason when the scheduler is
    not running (Flex not configured → the lifespan never started it). Each job
    op is isolated so one failure can't abort the rest.
    """
    sched = get_scheduler()
    if sched is None:
        return {"applied": False, "reason": "scheduler_not_running"}
    settings = get_settings()
    if config is None:
        from ..notify.config_store import resolve_notification_config

        config = resolve_notification_config(settings=settings)
    results: dict[str, str] = {}
    for job in _CONFIGURABLE_JOBS:
        job_id = job["job_id"]
        cron = _effective_cron_for(job, config, settings)
        existing = sched.get_job(job_id)
        try:
            if cron is None:
                if existing is not None:
                    sched.remove_job(job_id)
                    results[job_id] = "removed"
                else:
                    results[job_id] = "absent"
            else:
                trigger = CronTrigger.from_crontab(cron)
                if existing is not None:
                    sched.reschedule_job(job_id, trigger=trigger)
                    results[job_id] = f"rescheduled:{cron}"
                else:
                    sched.add_job(
                        job["func"],
                        trigger=trigger,
                        id=job_id,
                        replace_existing=True,
                        max_instances=1,
                        coalesce=True,
                        misfire_grace_time=_CONFIGURABLE_JOB_MISFIRE_GRACE,
                    )
                    results[job_id] = f"added:{cron}"
        except Exception as e:  # noqa: BLE001
            results[job_id] = f"error:{e}"
            log.warning("sync_scheduler_jobs: %s failed: %s", job_id, e)
    return {"applied": True, "jobs": results}


def _previous_fire_time(trigger: CronTrigger, now: datetime) -> datetime | None:
    """Most recent fire time of ``trigger`` at or before ``now`` (last 8 days)."""
    fire = trigger.get_next_fire_time(None, now - timedelta(days=8))
    previous = None
    while fire is not None and fire <= now:
        previous = fire
        fire = trigger.get_next_fire_time(fire, fire + timedelta(microseconds=1))
    return previous


def _last_scheduled_success_utc() -> datetime | None:
    """``fetched_at`` (naive UTC) of the newest successful scheduled daily pull.

    Only ``note == "scheduled"`` counts: a manual or TC pull does not run the
    digest and margin alerts, which the catch-up must still send.
    """
    from sqlalchemy import func

    from ..db import session_scope
    from ..db.models import FlexRun

    with session_scope() as s:
        return (
            s.query(func.max(FlexRun.fetched_at))
            .filter(FlexRun.note == "scheduled", FlexRun.status == "success")
            .scalar()
        )


def _db_has_data() -> bool:
    """False on a fresh DB, where `bootstrap` (not a catch-up) loads history.
    Plan B data (load-xml) counts as data."""
    from ..db import session_scope
    from ..loaders.pipeline import dashboard_has_data

    with session_scope() as s:
        return dashboard_has_data(s)


def _missed_daily_slot(now: datetime | None = None):
    """``(previous_slot, last_ok_utc, now)`` when the last daily cron slot has no
    scheduled success since it, else ``None``. Shared by the startup catch-up and
    the same-day retry so neither can run a slot that already succeeded.
    """
    trigger = CronTrigger.from_crontab(get_settings().pipeline_cron)
    now = now or datetime.now(trigger.timezone)
    previous = _previous_fire_time(trigger, now)
    if previous is None:
        return None
    previous_utc = previous.astimezone(UTC).replace(tzinfo=None)
    if not _db_has_data():
        return None  # fresh DB: the first deploy runs `bootstrap`, then the cron
    last_ok = _last_scheduled_success_utc()
    if last_ok is not None and last_ok >= previous_utc:
        return None
    return previous, last_ok, now


def _daily_retry_job() -> None:
    """Same-day retry of the daily pull after a transient failure (B46).

    Fires at the ``PIPELINE_RETRY_CRON`` slots (08:30 and 12:30 Zürich). It runs
    ``_daily_job`` only while the last ``PIPELINE_CRON`` slot has no successful
    scheduled pull (the startup catch-up predicate), so a good pull is never
    repeated and the digest/alerts are sent exactly once, by whichever attempt
    succeeds. 13 of 116 daily pulls failed on transient codes before this.
    """
    settings = get_settings()
    if not settings.ib_flex_token or not settings.ib_flex_query_id:
        return
    try:
        missed = _missed_daily_slot()
    except Exception as e:  # noqa: BLE001
        log.warning("Daily retry check failed: %s", e)
        return
    if missed is None:
        log.debug("Daily retry slot: today's pull already succeeded; nothing to do.")
        return
    previous, last_ok, _ = missed
    log.warning(
        "Daily pull retry: slot %s has no successful scheduled pull (last: %s); retrying now.",
        previous.isoformat(), last_ok,
    )
    _daily_job()


def _schedule_startup_catchup(
    sched: BackgroundScheduler, *, now: datetime | None = None
) -> bool:
    """Run the daily pull soon after boot when its last cron slot was missed.

    Moves the existing ``daily_flex_pull`` job to ``now + 120 s`` instead of
    adding a second job, so ``max_instances=1`` still applies and the next fire
    after the catch-up is the normal cron slot. Returns True when it moved the
    job. Best-effort — never raises.
    """
    try:
        missed = _missed_daily_slot(now)
        if missed is None:
            return False
        previous, last_ok, now = missed
        run_at = now + timedelta(seconds=_STARTUP_CATCHUP_DELAY_SECONDS)
        job = sched.get_job("daily_flex_pull")
        if job is None or (
            job.next_run_time is not None
            and job.next_run_time <= now + _STARTUP_CATCHUP_MIN_CRON_LEAD
        ):
            return False  # the cron fires soon; a catch-up would absorb that slot
        sched.modify_job("daily_flex_pull", next_run_time=run_at)
        log.warning(
            "Daily pull slot %s was missed (last scheduled success: %s); "
            "catch-up pull at %s",
            previous.isoformat(), last_ok, run_at.isoformat(),
        )
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Startup catch-up check failed: %s", e)
        return False


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = build_scheduler()
    _scheduler.start()
    _stamp_heartbeat()  # fresh heartbeat immediately, not only after the first tick
    # A fresh scheduler has free max_instances slots, so dispatch is not wedged.
    # Without this stamp, the watchdog read the pre-downtime stamp after every
    # cold start longer than 3 h as a dispatch wedge (false restart 2026-08-31).
    _stamp_pull_attempt()
    _schedule_startup_catchup(_scheduler)
    for job_id, nxt, weekday in next_run_weekdays(_scheduler):
        log.info("Job %-26s next run %s (%s)", job_id, nxt, weekday)
    s = get_settings()
    log.info(
        "Scheduler started (daily=%s, retry=%s, tc=%s tc_id_set=%s, margin=%s "
        "enabled=%s, margin_fx=%s, overlay=%s)",
        s.pipeline_cron,
        s.pipeline_retry_cron,
        s.tc_cron,
        bool(s.ib_flex_query_id_tc),
        s.margin_alert_cron,
        s.margin_alerts_enabled,
        s.margin_fx_cron,
        s.enable_live_overlay,
    )
    return _scheduler


def next_run_weekdays(sched: BackgroundScheduler, *, now: datetime | None = None):
    """``[(job_id, next_run_iso, weekday_name)]`` for every job, sorted by id.

    Computed from each trigger, so it works before and after ``start()``. Logged
    at startup (B45): a day-of-week off-by-one (``0 8 * * 1`` fired on Tuesday for
    weeks) is then visible in the first log lines instead of in the calendar.
    """
    out = []
    for job in sorted(sched.get_jobs(), key=lambda j: j.id):
        trig = job.trigger
        tz = getattr(trig, "timezone", None) or UTC
        ref = now or datetime.now(tz)
        nxt = trig.get_next_fire_time(None, ref)
        if nxt is not None:
            out.append((job.id, nxt.isoformat(), nxt.strftime("%a")))
    return out


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
