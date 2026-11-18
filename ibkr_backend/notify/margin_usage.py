"""Periodic margin-usage message — "how much margin am I using and how close am
I to the recommended limits", plus threshold escalation.

Reuses the margin-alert machinery: :func:`build_eod_snapshot` /
:func:`build_live_snapshot` for the snapshot and :func:`evaluate_margin` for the
risk bands. Works TODAY from the Flex EOD Reg-T estimate (clearly labelled) and
auto-upgrades to real IBKR figures when ``ENABLE_LIVE_OVERLAY`` is on and the
gateway responds.

Two send reasons, deduped independently:
* periodic — a recurring status at the message cadence (once per calendar day,
  gated by the scheduler's ``cadence_allows``).
* alert — an immediate push when the risk band worsens (state-machine
  transition), independent of the periodic cadence, when ``alert_bands`` is on.
"""

from __future__ import annotations

import html
import logging
from datetime import date

from ..config import Settings, get_settings
from ..db.models import NotificationState
from . import telegram
from .margin_alerts import (
    _LEVEL_LABEL,
    _LEVEL_RANK,
    build_eod_snapshot,
    build_live_snapshot,
    evaluate_margin,
)

log = logging.getLogger(__name__)

_PERIOD_KEY = "margin_usage_last_sent"          # once-per-day dedup for the status
_ALERT_PREFIX = "margin_usage_alert_level"      # transition state, keyed by source


def build_margin_usage_result(session, *, settings: Settings | None = None) -> dict:
    """Evaluate margin usage. Prefers live IBKR values, falls back to Flex EOD.

    Liquidity (cash / NLV) always comes from the Flex EOD snapshot.
    """
    from ..analytics.margin import margin_summary

    s = settings or get_settings()
    eod = margin_summary(session)
    snapshot = None
    if s.enable_live_overlay:
        try:
            from ..ibgw.live import LiveClient

            with LiveClient(purpose="margin_usage") as live:
                snapshot = build_live_snapshot(live.account_summary())
        except Exception as exc:  # noqa: BLE001
            log.info("Margin usage: live unavailable, using EOD estimate: %s", exc)
            snapshot = None
    if snapshot is None or snapshot.get("status") == "unavailable":
        snapshot = build_eod_snapshot(eod)

    result = evaluate_margin(snapshot, settings=s)
    result["cash_pct_nlv"] = eod.get("cash_pct_nlv")
    result["cash_base"] = (eod.get("nav_breakdown") or {}).get("cash")
    result["cash_currency"] = eod.get("base_currency")
    result["cash_snapshot_date"] = eod.get("snapshot_date")
    cushion = result.get("cushion_pct")
    if cushion is not None:
        result["usage_pct"] = round(100 - float(cushion), 2)  # maint / NAV, i.e. margin used
        result["distance_to_recommended_pct"] = round(
            float(cushion) - s.margin_cushion_warning_pct, 2
        )
    return result


def format_margin_usage_message(result: dict) -> str:
    source = (
        "IBKR en directo"
        if result.get("source") == "ibkr_live"
        else "estimación Flex EOD (no es margen IBKR en directo)"
    )
    level = str(result.get("level", "unavailable"))
    cushion = result.get("cushion_pct")
    usage = result.get("usage_pct")
    distance = result.get("distance_to_recommended_pct")
    excess = result.get("excess_liquidity")
    currency = html.escape(str(result.get("currency") or ""))
    warn = (result.get("policy") or {}).get("warning_pct")

    if level in {"critical", "breach"}:
        icon = "🚨"
    elif level in {"warning", "high"}:
        icon = "⚠️"
    else:
        icon = "📊"
    band = _LEVEL_LABEL.get(level, "SEGURO" if level == "safe" else "SIN DATOS")

    lines = [f"{icon} <b>Uso de margen — {band}</b>"]
    if usage is not None and cushion is not None:
        lines.append(
            f"Usado: <b>{usage:.1f}%</b> del patrimonio · colchón <b>{float(cushion):.1f}%</b>"
        )
    elif usage is not None:
        lines.append(f"Usado: <b>{usage:.1f}%</b> del patrimonio")
    elif cushion is not None:
        lines.append(f"Colchón: <b>{float(cushion):.1f}%</b>")
    if distance is not None and warn is not None:
        if distance >= 0:
            lines.append(
                f"A <b>{distance:.1f} pp</b> del límite recomendado "
                f"(colchón mín. {warn:.0f}%)"
            )
        else:
            lines.append(
                f"<b>{abs(distance):.1f} pp por debajo</b> del límite recomendado "
                f"(colchón mín. {warn:.0f}%)"
            )
    if excess is not None:
        lines.append(f"Exceso de liquidez: {float(excess):,.0f} {currency}".strip())
    cash_pct = result.get("cash_pct_nlv")
    if cash_pct is not None:
        cash = result.get("cash_base")
        cash_ccy = html.escape(str(result.get("cash_currency") or ""))
        line = f"Liquidez: <b>{float(cash_pct):.1f}%</b> del patrimonio en caja"
        if cash is not None:
            line += f" ({float(cash):,.0f} {cash_ccy} netos)"
        if result.get("source") == "ibkr_live":
            line += f" · Flex EOD {result.get('cash_snapshot_date')}"
        lines.append(line)
    lines.append(f"<i>Fuente: {source}. Aviso informativo; no ejecuta órdenes.</i>")
    return "\n".join(lines)


def _set_state(session, existing, key: str, value: str) -> None:
    if existing is None:
        session.add(NotificationState(key=key, value=value))
    else:
        existing.value = value


def check_margin_usage(
    session,
    *,
    settings: Settings | None = None,
    periodic_allowed: bool = True,
    alert_bands: bool = True,
    today: date | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Send the margin-usage status and/or a threshold-worsening alert.

    ``periodic_allowed`` is the cadence gate for the recurring status (the
    scheduler computes it from the message cadence). ``alert_bands`` enables the
    immediate escalation on a worsening band. Markers only advance after a
    successful send (at-least-once).
    """
    s = settings or get_settings()
    today = today or date.today()

    result = build_margin_usage_result(session, settings=s)
    level = str(result.get("level", "unavailable"))
    summary = {
        "level": level,
        "source": result.get("source"),
        "usage_pct": result.get("usage_pct"),
        "cushion_pct": result.get("cushion_pct"),
    }
    if level == "unavailable":
        return {**summary, "sent": False, "would_send": False, "skipped": "unavailable"}

    source = result.get("source")
    alert_key = f"{_ALERT_PREFIX}:{source}"
    astate = session.get(NotificationState, alert_key)
    prev = astate.value if astate and astate.value in _LEVEL_RANK else None
    worsening = level != "safe" and (prev is None or _LEVEL_RANK[level] > _LEVEL_RANK[prev])
    alert_due = bool(alert_bands and worsening)

    period_sig = today.isoformat()
    pstate = session.get(NotificationState, _PERIOD_KEY)
    periodic_due = force or (periodic_allowed and (pstate is None or pstate.value != period_sig))

    would_send = alert_due or periodic_due
    message = format_margin_usage_message(result) if would_send else None
    response = {
        **summary,
        "previous_level": prev,
        "alert_due": alert_due,
        "periodic_due": periodic_due,
        "would_send": would_send,
        "sent": False,
        "message": message,
    }
    if dry_run:
        return response

    sent_ok = False
    if would_send:
        if not telegram.is_configured(s):
            return {**response, "configured": False}
        try:
            sent_ok = telegram.send_message(message or "", settings=s)
        except Exception as exc:  # noqa: BLE001
            log.warning("Margin usage send failed: %s", exc)
            return {**response, "configured": True, "error": str(exc)}
        if not sent_ok:
            return {**response, "configured": True}
        response["sent"] = True

    # Advance markers. On a pending alert that failed to send we returned early
    # above, so the transition retries next run. When nothing was due, still
    # track the current band as the baseline for the next worsening check.
    if sent_ok or not alert_due:
        _set_state(session, astate, alert_key, level)
    if sent_ok and periodic_due:
        _set_state(session, pstate, _PERIOD_KEY, period_sig)
    return response


__all__ = [
    "build_margin_usage_result",
    "check_margin_usage",
    "format_margin_usage_message",
]
