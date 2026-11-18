"""Telegram push when the Flex pipeline stops updating.

The staleness watchdog used to only log a WARNING — if the daily pull silently
stopped (scheduler down, token expired, query deactivated) no message reached
the user. This adds a deduped Telegram alert: one message per stale episode,
re-armed once the pipeline recovers, mirroring the margin-alert re-arm pattern
so a transient send failure retries instead of being lost.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from html import escape

from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db.models import NotificationState
from . import telegram

log = logging.getLogger(__name__)

_ALERT_KEY = "stale_alert_active"
_DATA_ALERT_KEY = "data_stale_alert_active"
_BACKUP_ALERT_KEY = "backup_stale_alert_active"


def _fmt_age(hours: float | None) -> str:
    if hours is None:
        return "un tiempo desconocido"
    if hours < 48:
        return f"{hours:.0f} h"
    return f"{hours / 24:.1f} días"


def _token_line(s: Settings, today: date) -> str | None:
    """Cross-check the configured Flex token expiry against `today`.

    Points at the most probable cause (expired token) or rules it out. None when the expiry is unset or unparseable (same guard as the
    scheduler's token-expiry warning)."""
    raw = s.ib_flex_token_expiry
    if not raw:
        return None
    try:
        expiry = date.fromisoformat(raw)
    except ValueError:
        return None
    if expiry < today:
        return (
            f"Causa probable: el token Flex caducó el {expiry.strftime('%d/%m/%Y')}. "
            "Regenéralo en el Client Portal."
        )
    return (
        f"Token Flex válido hasta el {expiry.strftime('%d/%m/%Y')} → "
        "revisa el scheduler o la query de Flex."
    )


def _last_error_line(session: Session, s: Settings) -> str | None:
    """Quote the newest Activity FlexRun error (B47), or None when it did not fail.

    The text is the redacted ``flex_runs.error`` (no token / query id), clipped
    and HTML-escaped for ``parse_mode=HTML``.
    """
    from ..jobs.health import latest_run_error  # lazy: avoid import cycle

    last = latest_run_error(session, query_id=s.ib_flex_query_id or None)
    if last is None or not last["error"]:
        return None
    when = last["fetched_at"].strftime("%d/%m %H:%M UTC") if last["fetched_at"] else "?"
    return f"Último error de Flex ({when}): <code>{escape(last['error'][:240])}</code>"


def check_staleness_alert(
    session: Session,
    *,
    settings: Settings | None = None,
    staleness: dict | None = None,
    now=None,
) -> dict:
    """Send at most one Telegram alert per stale episode; re-arm on recovery.

    `staleness` may be a precomputed `compute_staleness(...)` dict to avoid a
    second query; otherwise it is computed here. No-op (never raises) if
    Telegram is not configured.
    """
    s = settings or get_settings()
    if staleness is None:
        from ..jobs.health import compute_staleness  # lazy: avoid import cycle

        staleness = compute_staleness(
            session, threshold_hours=s.pipeline_stale_after_hours, now=now,
            query_id=s.ib_flex_query_id or None,
        )

    row = session.get(NotificationState, _ALERT_KEY)
    active = bool(row and row.value == "1")

    if not staleness["is_stale"]:
        if active:
            row.value = "0"  # recovered → re-arm for the next episode
        return {"stale": False, "sent": False}

    if active:
        return {"stale": True, "sent": False, "skipped": "already alerted"}

    if not telegram.is_configured(s):
        # Leave un-armed so the alert fires once Telegram is configured.
        return {"stale": True, "sent": False, "skipped": "telegram not configured"}

    today = now.date() if isinstance(now, datetime) else date.today()
    lines = [
        "🔴 <b>Pipeline sin actualizar</b>",
        (
            f"Último pull de Flex OK: hace <b>{_fmt_age(staleness['age_hours'])}</b> "
            f"(umbral {staleness['threshold_hours']:.0f} h)."
        ),
    ]
    token_line = _token_line(s, today)
    if token_line:
        lines.append(token_line)
    error_line = _last_error_line(session, s)
    if error_line:
        lines.append(error_line)
    lines.append(
        "Siguiente paso: docker compose logs backend (busca «Daily Flex run failed») · "
        "docker compose ps."
    )
    lines.append(
        "Plan B: docker exec ibkr-backend python -m ibkr_backend.cli load-xml "
        "/app/Extractos/&lt;export&gt;.xml"
    )
    message = "\n".join(lines)
    try:
        delivered = telegram.send_message(message, settings=s)
    except Exception as e:  # noqa: BLE001
        log.warning("Staleness alert send failed: %s", e)
        return {"stale": True, "sent": False, "error": str(e)}
    if not delivered:  # Telegram answered HTTP 200 {ok:false} → stay un-armed, retry next tick
        log.warning("Staleness alert not accepted by Telegram; will retry.")
        return {"stale": True, "sent": False, "error": "telegram not ok"}

    # Arm only after a successful send, so a failed send retries next tick.
    if row is None:
        session.add(NotificationState(key=_ALERT_KEY, value="1"))
    else:
        row.value = "1"
    log.info("Staleness alert sent (age=%sh).", staleness["age_hours"])
    return {"stale": True, "sent": True}


def check_data_age_alert(
    session: Session,
    *,
    settings: Settings | None = None,
    freshness: dict | None = None,
    today: date | None = None,
) -> dict:
    """Deduped Telegram alert when the DATA stops advancing although pulls succeed.

    Same one-per-episode / re-arm-on-recovery contract as ``check_staleness_alert``
    (key ``data_stale_alert_active``). ``freshness`` may be a precomputed
    ``compute_data_freshness(...)`` dict. Never raises.
    """
    s = settings or get_settings()
    if freshness is None:
        from ..jobs.health import compute_data_freshness  # lazy: avoid import cycle

        freshness = compute_data_freshness(
            session, threshold_business_days=s.data_stale_after_business_days, today=today
        )

    row = session.get(NotificationState, _DATA_ALERT_KEY)
    active = bool(row and row.value == "1")

    if not freshness["data_is_stale"]:
        if active:
            row.value = "0"
        return {"stale": False, "sent": False}

    if active:
        return {"stale": True, "sent": False, "skipped": "already alerted"}

    if not telegram.is_configured(s):
        return {"stale": True, "sent": False, "skipped": "telegram not configured"}

    last = freshness["last_data_date"]
    last_txt = date.fromisoformat(last).strftime("%d/%m/%Y") if last else "nunca"
    age = freshness["age_business_days"]
    lines = [
        "🟠 <b>Datos sin avanzar</b>",
        (
            f"Último snapshot de posiciones: <b>{last_txt}</b> "
            f"({age if age is not None else '?'} días hábiles, umbral "
            f"{freshness['threshold_business_days']}) — el pull de Flex sigue en éxito, "
            "pero el statement no trae días nuevos."
        ),
        (
            f"Causa probable: el periodo de la query Activity {s.ib_flex_query_id or ''} "
            "en el Client Portal ya no cubre los últimos días (2026-09-12: «Year to Date» "
            "llegaba truncado → «Last 30 Calendar Days»)."
        ),
        "Comprueba: GET /data/coverage (days_stale) · GET /pipeline/status → data_freshness.",
    ]
    try:
        delivered = telegram.send_message("\n".join(lines), settings=s)
    except Exception as e:  # noqa: BLE001
        log.warning("Data-age alert send failed: %s", e)
        return {"stale": True, "sent": False, "error": str(e)}
    if not delivered:
        log.warning("Data-age alert not accepted by Telegram; will retry.")
        return {"stale": True, "sent": False, "error": "telegram not ok"}

    if row is None:
        session.add(NotificationState(key=_DATA_ALERT_KEY, value="1"))
    else:
        row.value = "1"
    log.info("Data-age alert sent (last=%s, age=%s bd).", last, age)
    return {"stale": True, "sent": True}


def check_backup_age_alert(
    session: Session,
    *,
    settings: Settings | None = None,
    info: dict | None = None,
) -> dict:
    """Deduped Telegram alert when the newest nightly DB backup is overdue (B42).

    Same one-per-episode / re-arm-on-recovery contract as the other watchdog
    alerts (key ``backup_stale_alert_active``, armed only after an ok send).
    ``info`` may be a precomputed ``latest_backup_info(...)`` dict. The nightly
    job already alerts on a failed run; this catches the silent cases (job never
    fired, volume not mounted, files not landing). Never raises.
    """
    s = settings or get_settings()
    if not s.backup_dir:
        return {"overdue": False, "sent": False, "skipped": "backup disabled"}
    if info is None:
        from ..jobs.backup import latest_backup_info  # lazy: avoid import cycle

        info = latest_backup_info(s.backup_dir, stale_after_hours=s.backup_stale_after_hours)

    row = session.get(NotificationState, _BACKUP_ALERT_KEY)
    active = bool(row and row.value == "1")

    if not info["is_overdue"]:
        if active:
            row.value = "0"
        return {"overdue": False, "sent": False}
    if active:
        return {"overdue": True, "sent": False, "skipped": "already alerted"}
    if not telegram.is_configured(s):
        return {"overdue": True, "sent": False, "skipped": "telegram not configured"}

    message = "\n".join(
        [
            "🟠 <b>IBKR · backup nocturno atrasado</b>",
            (
                f"El último backup de la base tiene <b>{_fmt_age(info['age_hours'])}</b> "
                f"(umbral {info['threshold_hours']:.0f} h)."
            ),
            "Revisa: docker compose logs backend (busca Nightly DB backup) · "
            "ls -l ~/.ibkr-backups",
        ]
    )
    try:
        delivered = telegram.send_message(message, settings=s)
    except Exception as e:  # noqa: BLE001
        log.warning("Backup-age alert send failed: %s", e)
        return {"overdue": True, "sent": False, "error": str(e)}
    if not delivered:
        log.warning("Backup-age alert not accepted by Telegram; will retry.")
        return {"overdue": True, "sent": False, "error": "telegram not ok"}
    if row is None:
        session.add(NotificationState(key=_BACKUP_ALERT_KEY, value="1"))
    else:
        row.value = "1"
    log.info("Backup-age alert sent (age=%sh).", info["age_hours"])
    return {"overdue": True, "sent": True}


__all__ = ["check_backup_age_alert", "check_data_age_alert", "check_staleness_alert"]
