"""Telegram alerts for live IBKR margin risk.

Live values come from IBKR account summary. Flex-derived Reg-T values can be
used as an end-of-day fallback, but they stay explicitly labelled estimates.
The state machine alerts only when risk worsens and once when it recovers.
"""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime
from typing import Any, Callable

from ..config import Settings, get_settings
from ..db.models import NotificationState
from . import telegram

log = logging.getLogger(__name__)

_LEVEL_RANK = {
    "unavailable": -1,
    "safe": 0,
    "warning": 1,
    "high": 2,
    "critical": 3,
    "breach": 4,
}
_LEVEL_LABEL = {
    "warning": "VIGILANCIA",
    "high": "ALTO",
    "critical": "CRÍTICO",
    "breach": "INCUMPLIMIENTO",
}

# IBKR official-warning thresholds (were inline magic numbers).
_IBKR_MAINTENANCE_WARNING_MULTIPLE = 1.10  # equity within 110% of maintenance
_IBKR_OFFICIAL_WARNING_CUSHION_PCT = 10.0  # cushion ≤ 10% == official IBKR warning


def _number(raw: Any) -> float | None:
    """Return a finite float from an IBKR tag or a plain value."""
    if isinstance(raw, dict):
        raw = raw.get("value")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value or abs(value) > 1e18:
        return None
    return value


def _currency(summary: dict[str, Any]) -> str | None:
    for tag in ("NetLiquidation", "ExcessLiquidity", "MaintMarginReq"):
        raw = summary.get(tag)
        if isinstance(raw, dict) and raw.get("currency"):
            return str(raw["currency"])
    return None


def _utc_iso(value: datetime | None = None) -> str:
    value = value or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_live_snapshot(
    summary: dict[str, Any], *, captured_at: datetime | None = None
) -> dict[str, Any]:
    """Normalise IBKR account-summary tags without exposing the account id."""
    nav = _number(summary.get("NetLiquidation"))
    equity = _number(summary.get("EquityWithLoanValue"))
    maintenance = _number(summary.get("MaintMarginReq"))
    excess = _number(summary.get("ExcessLiquidity"))
    lookahead_excess = _number(summary.get("LookAheadExcessLiquidity"))
    lookahead_maintenance = _number(summary.get("LookAheadMaintMarginReq"))
    cushion_ratio = _number(summary.get("Cushion"))

    cushion_pct = round(cushion_ratio * 100, 8) if cushion_ratio is not None else None
    if cushion_pct is None and nav not in (None, 0) and excess is not None:
        cushion_pct = excess / nav * 100
    lookahead_cushion_pct = None
    if nav not in (None, 0) and lookahead_excess is not None:
        lookahead_cushion_pct = round(lookahead_excess / nav * 100, 8)

    required = (nav, maintenance, excess, cushion_pct)
    status = "ready" if all(value is not None for value in required) else "unavailable"
    return {
        "status": status,
        "source": "ibkr_live",
        "captured_at": _utc_iso(captured_at),
        "currency": _currency(summary),
        "net_liquidation": nav,
        "equity_with_loan_value": equity,
        "maintenance_margin": maintenance,
        "excess_liquidity": excess,
        "cushion_pct": cushion_pct,
        "lookahead_excess_liquidity": lookahead_excess,
        "lookahead_maintenance_margin": lookahead_maintenance,
        "lookahead_cushion_pct": lookahead_cushion_pct,
        "sma": _number(summary.get("SMA")),
        "leverage": _number(summary.get("Leverage")),
    }


def build_eod_snapshot(summary: dict[str, Any]) -> dict[str, Any]:
    """Build a clearly labelled EOD snapshot from conservative Reg-T estimates."""
    estimate = summary.get("reg_t_estimate") or {}
    nav = _number(summary.get("nav_total"))
    maintenance = _number(estimate.get("maint_margin_estimate"))
    excess = _number(estimate.get("excess_liq_estimate"))
    cushion = _number(estimate.get("cushion_pct_estimate"))
    return {
        "status": "estimated" if cushion is not None else "unavailable",
        "source": "flex_reg_t_estimate",
        "captured_at": summary.get("snapshot_date"),
        "currency": summary.get("base_currency"),
        "net_liquidation": nav,
        "equity_with_loan_value": None,
        "maintenance_margin": maintenance,
        "excess_liquidity": excess,
        "cushion_pct": cushion,
        "lookahead_excess_liquidity": None,
        "lookahead_maintenance_margin": None,
        "lookahead_cushion_pct": None,
        "sma": None,
        "leverage": _number(summary.get("leverage_x")),
    }


def _band(cushion_pct: float, settings: Settings) -> str:
    if cushion_pct <= settings.margin_cushion_critical_pct:
        return "critical"
    if cushion_pct <= settings.margin_cushion_high_pct:
        return "high"
    if cushion_pct <= settings.margin_cushion_warning_pct:
        return "warning"
    return "safe"


def evaluate_margin(snapshot: dict[str, Any], *, settings: Settings | None = None) -> dict[str, Any]:
    """Classify current and look-ahead margin values into the worst risk band."""
    s = settings or get_settings()
    if snapshot.get("status") == "unavailable" or snapshot.get("cushion_pct") is None:
        return {
            **snapshot,
            "level": "unavailable",
            "official_warning": False,
            "reasons": ["MARGIN_DATA_UNAVAILABLE"],
        }

    reasons: list[str] = []
    level = _band(float(snapshot["cushion_pct"]), s)
    current_excess = snapshot.get("excess_liquidity")
    lookahead_excess = snapshot.get("lookahead_excess_liquidity")
    lookahead_cushion = snapshot.get("lookahead_cushion_pct")

    if current_excess is not None and float(current_excess) <= 0:
        level = "breach"
        reasons.append("EXCESS_LIQUIDITY_NON_POSITIVE")
    if lookahead_excess is not None and float(lookahead_excess) <= 0:
        level = "breach"
        reasons.append("LOOKAHEAD_EXCESS_LIQUIDITY_NON_POSITIVE")

    if lookahead_cushion is not None:
        lookahead_level = _band(float(lookahead_cushion), s)
        if _LEVEL_RANK[lookahead_level] > _LEVEL_RANK[level]:
            level = lookahead_level
        if float(lookahead_cushion) <= s.margin_cushion_critical_pct:
            reasons.append("LOOKAHEAD_CUSHION_CRITICAL")

    equity = snapshot.get("equity_with_loan_value")
    maintenance = snapshot.get("maintenance_margin")
    equity_warning = (
        equity is not None
        and maintenance is not None
        and float(equity) <= float(maintenance) * _IBKR_MAINTENANCE_WARNING_MULTIPLE
    )
    official_warning = bool(
        equity_warning
        or float(snapshot["cushion_pct"]) <= _IBKR_OFFICIAL_WARNING_CUSHION_PCT
        or (
            lookahead_cushion is not None
            and float(lookahead_cushion) <= _IBKR_OFFICIAL_WARNING_CUSHION_PCT
        )
        or level == "breach"
    )
    if official_warning and _LEVEL_RANK[level] < _LEVEL_RANK["critical"]:
        level = "critical"
        reasons.append("IBKR_OFFICIAL_10_PCT_WARNING")
    if equity_warning:
        reasons.append("EQUITY_WITH_LOAN_VALUE_AT_IBKR_WARNING")

    if not reasons and level != "safe":
        reasons.append(f"CUSHION_{level.upper()}")
    return {
        **snapshot,
        "level": level,
        "official_warning": official_warning,
        "reasons": reasons,
        "policy": {
            "warning_pct": s.margin_cushion_warning_pct,
            "high_pct": s.margin_cushion_high_pct,
            "critical_pct": s.margin_cushion_critical_pct,
        },
    }


def _format_message(result: dict[str, Any], *, recovered_from: str | None = None) -> str:
    source = (
        "IBKR en directo"
        if result.get("source") == "ibkr_live"
        else "estimación Flex EOD (no es margen IBKR en directo)"
    )
    cushion = result.get("cushion_pct")
    lookahead = result.get("lookahead_cushion_pct")
    excess = result.get("excess_liquidity")
    currency = html.escape(str(result.get("currency") or ""))
    if recovered_from:
        title = "✅ <b>Margen — RECUPERADO</b>"
        detail = f"Estado anterior: {html.escape(recovered_from.upper())}."
    else:
        level = str(result["level"])
        icon = "🚨" if level in {"critical", "breach"} else "⚠️"
        title = f"{icon} <b>Margen — {_LEVEL_LABEL[level]}</b>"
        detail = "Reduce o revisa exposición y órdenes abiertas en TWS."
    lines = [title, f"Fuente: {source}."]
    if cushion is not None:
        lines.append(f"Cushion actual: <b>{float(cushion):.1f}%</b>.")
    if lookahead is not None:
        lines.append(f"Cushion look-ahead: <b>{float(lookahead):.1f}%</b>.")
    if excess is not None:
        lines.append(f"Exceso de liquidez: {float(excess):,.0f} {currency}.".strip())
    if result.get("official_warning"):
        lines.append("Banda oficial IBKR de advertencia alcanzada o proyectada.")
    lines.append(detail)
    lines.append("Aviso informativo. No ejecuta órdenes.")
    return "\n".join(lines)


def _live_snapshot() -> dict[str, Any]:
    from ..ibgw.live import LiveClient

    with LiveClient(purpose="margin_alerts") as live:
        return build_live_snapshot(live.account_summary())


def check_margin_alert(
    session,
    *,
    snapshot_fn: Callable[[], dict[str, Any]] | None = None,
    settings: Settings | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Evaluate once and send only a worsening transition or full recovery."""
    s = settings or get_settings()
    if not s.margin_alerts_enabled:
        return {"enabled": False, "sent": False, "would_send": False}

    try:
        snapshot = (snapshot_fn or _live_snapshot)()
    except Exception as exc:  # noqa: BLE001
        log.info("Margin alert skipped — live values unavailable: %s", exc)
        return {
            "enabled": True,
            "sent": False,
            "would_send": False,
            "level": "unavailable",
            "error": str(exc),
        }

    result = evaluate_margin(snapshot, settings=s)
    level = result["level"]
    if level == "unavailable":
        return {**result, "sent": False, "would_send": False}

    state_key = f"margin_alert_level:{result['source']}"
    state = session.get(NotificationState, state_key)
    previous = state.value if state and state.value in _LEVEL_RANK else None
    worsening = level != "safe" and (
        previous is None or _LEVEL_RANK[level] > _LEVEL_RANK[previous]
    )
    recovered = level == "safe" and previous not in (None, "safe")
    # Recovery-only hysteresis: only announce RECUPERADO once the cushion clears
    # the warning band by a buffer. Between warning_pct and warning_pct+hysteresis
    # we hold the previous risk level and stay silent, so band-edge NAV noise on
    # the */3 live poll can't flap WARNING↔RECUPERADO. Escalations are untouched.
    in_recovery_deadband = False
    if recovered:
        cushion = result.get("cushion_pct")
        clear_at = s.margin_cushion_warning_pct + s.margin_cushion_recovery_hysteresis_pct
        if cushion is not None and float(cushion) < clear_at:
            recovered = False
            in_recovery_deadband = True
    would_send = worsening or recovered
    message = (
        _format_message(result, recovered_from=previous if recovered else None)
        if would_send
        else None
    )

    response = {
        **result,
        "previous_level": previous,
        "sent": False,
        "would_send": would_send,
        "message": message,
    }
    if dry_run:
        return response

    # Do not consume an alert transition if Telegram is absent or the send
    # fails. This makes the next scheduled run retry the same transition.
    if would_send:
        if not telegram.is_configured(s):
            return {**response, "configured": False}
        try:
            if not telegram.send_message(message or "", settings=s):
                return {**response, "configured": True}
        except Exception as exc:  # noqa: BLE001
            log.warning("Margin alert send failed: %s", exc)
            return {**response, "configured": True, "error": str(exc)}
        response["sent"] = True

    # Advance the tracked level — EXCEPT while holding inside the recovery
    # dead-band, where we deliberately keep the previous risk level.
    if not in_recovery_deadband:
        if state is None:
            session.add(NotificationState(key=state_key, value=level))
        else:
            state.value = level
    return response


__all__ = [
    "build_eod_snapshot",
    "build_live_snapshot",
    "check_margin_alert",
    "evaluate_margin",
]
