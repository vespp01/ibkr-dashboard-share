"""Price alerts — poll IB Gateway quotes, ping Telegram on threshold crossings.

The user defines rules in ``price_alerts.yaml`` (symbol, ``>``/``<``, price).
A scheduled job polls live quotes via :mod:`ibkr_backend.ibgw.live` (needs
``ENABLE_LIVE_OVERLAY=true`` + IB Gateway running) and sends a Telegram alert
the first time a rule crosses. A ``PriceAlertState`` row re-arms the rule once
the price returns to the non-crossed side, so a standing threshold is not
re-notified on every poll.

This re-implements IBKR's own price alerts (which are NOT readable via any IBKR
API) as a dashboard-native feature: the thresholds live here, not in TWS.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import yaml

from ..config import Settings, get_settings
from ..db.models import PriceAlertState
from . import telegram

log = logging.getLogger(__name__)


@dataclass(slots=True)
class AlertRule:
    symbol: str
    op: str  # ">" or "<"
    price: float
    exchange: str = "SMART"
    currency: str = "USD"
    note: str | None = None

    @property
    def key(self) -> str:
        return f"{self.symbol}|{self.op}|{self.price:g}"


def load_rules(path) -> list[AlertRule]:
    """Parse `price_alerts.yaml` into rules. Invalid entries are skipped (logged)."""
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    rules: list[AlertRule] = []
    for raw in data.get("alerts", []) or []:
        try:
            op = str(raw["op"]).strip()
            if op not in (">", "<"):
                raise ValueError(f"op must be '>' or '<', got {op!r}")
            rules.append(
                AlertRule(
                    symbol=str(raw["symbol"]).strip().upper(),
                    op=op,
                    price=float(raw["price"]),
                    exchange=str(raw.get("exchange") or "SMART"),
                    currency=str(raw.get("currency") or "USD"),
                    note=raw.get("note"),
                )
            )
        except (KeyError, ValueError, TypeError) as e:
            log.warning("Skipping invalid price-alert rule %r: %s", raw, e)
    return rules


def crossed(rule: AlertRule, last: float | None) -> bool:
    """True when `last` satisfies the rule's threshold."""
    if last is None:
        return False
    return (rule.op == ">" and last > rule.price) or (
        rule.op == "<" and last < rule.price
    )


def _format(rule: AlertRule, last: float) -> str:
    verb = "ha superado" if rule.op == ">" else "ha caído por debajo de"
    note = f" ({rule.note})" if rule.note else ""
    return (
        f"🔔 <b>Alerta de precio — {rule.symbol}</b>{note}\n"
        f"{rule.symbol} {verb} {rule.price:.2f} {rule.currency} · "
        f"actual {last:.2f} {rule.currency}"
    )


def _live_quotes(rules: list[AlertRule]) -> dict[str, float | None]:
    """Snapshot last/close price per symbol via IB Gateway. {} if unavailable."""
    from ..ibgw.live import LiveClient, LiveUnavailable

    contracts = [(r.symbol, r.exchange, r.currency) for r in rules]
    try:
        with LiveClient() as live:
            quotes = live.snapshot_quotes(contracts)
    except LiveUnavailable as e:
        log.info("Price alerts skipped — live quotes unavailable: %s", e)
        return {}
    return {q.symbol: (q.last if q.last is not None else q.close) for q in quotes}


def check_price_alerts(
    session,
    *,
    quotes_fn=None,
    settings: Settings | None = None,
) -> dict:
    """Evaluate every rule against current prices; send Telegram on new crossings.

    `quotes_fn(rules) -> {symbol: last_price}` is injected in tests; in
    production it defaults to an IB Gateway snapshot. Best-effort: a Telegram
    or quote failure is logged, never raised. Returns a summary dict.
    """
    s = settings or get_settings()
    rules = load_rules(s.price_alerts_file)
    if not rules:
        return {"rules": 0, "fired": []}

    quotes = (quotes_fn or _live_quotes)(rules)
    fired: list[str] = []
    for rule in rules:
        last = quotes.get(rule.symbol)
        state = session.get(PriceAlertState, rule.key)
        is_crossed = crossed(rule, last)
        if state is None:
            state = PriceAlertState(alert_key=rule.key, triggered=False)
            session.add(state)
        state.last_price = last

        if is_crossed and not state.triggered:
            state.triggered = True
            if telegram.is_configured(s):
                try:
                    if telegram.send_message(_format(rule, last), settings=s):
                        fired.append(rule.key)
                except Exception as e:  # noqa: BLE001
                    log.warning("Price alert '%s' send failed: %s", rule.key, e)
        elif not is_crossed and state.triggered:
            state.triggered = False  # re-arm once price returns

    return {"rules": len(rules), "fired": fired, "configured": telegram.is_configured(s)}


__all__ = ["AlertRule", "check_price_alerts", "crossed", "load_rules"]
