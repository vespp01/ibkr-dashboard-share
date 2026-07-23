"""Daily Telegram reports — dividends, operations, month-to-date commissions.

Re-implements, against the dashboard's own DB, the three notifications a
legacy spreadsheet workflow used to send, in the same per-item block style
("Compra DD/MM/YYYY", "Importe:", "Tipo cambio:") — but each item unified into
ONE block showing native + base currency, instead of two separate per-currency
blocks:

  1. Dividends collected on a given day (gross / withholding / net, shares
     and per-share rate parsed from the cash-transaction description, since
     IBKR ships dividend rows with `symbol`/`conid` often NULL).
  2. Buy/sell operations on stocks and options (forex excluded; option
     exercise/assignment book trades excluded — they show as their own
     "Ejercicio" blocks), plus deposits/withdrawals.
  3. Commissions accumulated month-to-date.

All figures trace to Flex fields already stored — no invented metrics. A
`NotificationState` watermark prevents re-sending the same day.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analytics.dividends_calc import DIVIDEND_TYPES, WITHHOLDING_TYPES
from ..analytics.filters import resolve_period
from ..config import Settings, get_settings
from ..db.models import (
    CashTransaction,
    Instrument,
    NotificationState,
    OptionExerciseAssignment,
    Trade,
)
from . import telegram

log = logging.getLogger(__name__)

_WATERMARK_KEY = "last_notified_date"

# `TICKER(ISIN) Cash Dividend USD 1.67 per Share (...)` — ticker is the
# leading token, ISIN sits in the parens, the per-share rate + currency
# follow "Cash Dividend". Case varies (some rows are upper-cased).
_TICKER_RE = re.compile(r"^\s*([A-Za-z0-9.]+)\s*\(")
_ISIN_RE = re.compile(r"\(([A-Z]{2}[A-Z0-9]{9}\d)\)")
_RATE_RE = re.compile(r"(?i)\b([A-Z]{3})\s+([\d.]+)\s+per\s+share")


# ------------------------------------------------------------- small helpers
def _fmt(x: float | None) -> str:
    return f"{(x or 0):.2f}"


def _fmt_fx(x: float | None) -> str:
    return f"{x:.6f}" if x else "1.000000"


def _fmt_rate(x: float | None) -> str:
    if not x:
        return "0"
    return f"{x:.4f}".rstrip("0").rstrip(".")


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _amounts(native: float, fx: float | None, base_ccy: str, ccy: str) -> str:
    """`-1201.05 USD (-940.53 CHF)` — base in parens unless same currency."""
    if not ccy or ccy == base_ccy:
        return f"{_fmt(native)} {base_ccy}"
    base = native * float(fx or 1)
    return f"{_fmt(native)} {ccy} ({_fmt(base)} {base_ccy})"


def _parse_ticker(desc: str | None) -> str | None:
    if not desc:
        return None
    m = _TICKER_RE.match(desc)
    return m.group(1).upper() if m else None


def _parse_isin(desc: str | None) -> str | None:
    if not desc:
        return None
    m = _ISIN_RE.search(desc)
    return m.group(1) if m else None


def _parse_rate(desc: str | None) -> float | None:
    if not desc:
        return None
    m = _RATE_RE.search(desc)
    return float(m.group(2)) if m else None


def _instrument_name(
    session: Session, *, conid: int | None = None,
    isin: str | None = None, fallback: str | None = None,
) -> str:
    """Long instrument name (e.g. 'WASTE CONNECTIONS INC'); ticker fallback."""
    if conid:
        inst = session.get(Instrument, conid)
        if inst and inst.description:
            return inst.description
    if isin:
        inst = session.execute(
            select(Instrument).where(Instrument.isin == isin)
        ).scalars().first()
        if inst and inst.description:
            return inst.description
    return fallback or "?"


# ------------------------------------------------------------------ reports
def dividends_report(
    session: Session, target: date, base_ccy: str
) -> str | None:
    """Dividends collected on `target` (by settle_date). None if there are none."""
    rows = (
        session.execute(
            select(CashTransaction)
            .where(CashTransaction.type.in_(DIVIDEND_TYPES + WITHHOLDING_TYPES))
            .where(CashTransaction.settle_date == target)
        )
        .scalars()
        .all()
    )
    if not rows:
        return None

    groups: dict[str, dict] = {}
    for r in rows:
        ticker = _parse_ticker(r.description) or (r.symbol or "?")
        g = groups.setdefault(
            ticker,
            {"ticker": ticker, "conid": None, "isin": None, "currency": r.currency,
             "gross": 0.0, "wht": 0.0, "rate": None, "fx": None},
        )
        amt = float(r.amount or 0)
        g["fx"] = g["fx"] or (float(r.fx_rate_to_base) if r.fx_rate_to_base else None)
        g["conid"] = g["conid"] or r.conid
        g["isin"] = g["isin"] or _parse_isin(r.description)
        if r.type in DIVIDEND_TYPES:
            g["gross"] += amt
            g["currency"] = r.currency
            g["rate"] = g["rate"] or _parse_rate(r.description)
        else:
            g["wht"] += amt

    blocks: list[str] = []
    for g in sorted(groups.values(), key=lambda x: -abs(x["gross"])):
        name = _instrument_name(
            session, conid=g["conid"], isin=g["isin"], fallback=g["ticker"]
        )
        ccy = g["currency"] or ""
        fx = g["fx"]
        net = g["gross"] + g["wht"]
        shares = (g["gross"] / g["rate"]) if g["rate"] else None
        rate_txt = f"({_fmt_rate(g['rate'])} {ccy} por acción) " if g["rate"] else ""
        acc_txt = f"de {shares:.0f} acc: " if shares is not None else ""
        lines = [
            f"<b>Dividendo {_ddmmyyyy(target)}</b>",
            f"{name}:",
            f"Importe bruto {rate_txt}{acc_txt}{_amounts(g['gross'], fx, base_ccy, ccy)}",
        ]
        if g["wht"]:
            pct = (-g["wht"] / g["gross"] * 100) if g["gross"] else 0
            lines.append(f"Importe retenido: {_amounts(g['wht'], fx, base_ccy, ccy)}")
            lines.append(
                f"Importe neto (retenido {pct:.2f}%): {_amounts(net, fx, base_ccy, ccy)}"
            )
        else:
            lines.append(f"Importe neto: {_amounts(net, fx, base_ccy, ccy)}")
        if ccy and ccy != base_ccy:
            lines.append(f"Tipo cambio: {_fmt_fx(fx)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def operations_report(session: Session, target: date) -> str | None:
    """Operations on `target`: stock/option buys/sells (forex and option
    exercise/assignment book trades excluded), option exercise/assignment, and
    cash deposits/withdrawals. None if there were none."""
    trades = (
        session.execute(
            select(Trade)
            .where(Trade.trade_date == target)
            .where(Trade.asset_class.in_(("STK", "OPT")))
            .order_by(Trade.trade_datetime)
        )
        .scalars()
        .all()
    )
    # Exclude exercise/assignment book trades — they are the *result* of an
    # option E&A (shown in their own block), not a discretionary buy/sell.
    trades = [t for t in trades if (t.transaction_type or "") != "BookTrade"]

    eae = (
        session.execute(
            select(OptionExerciseAssignment)
            .where(OptionExerciseAssignment.event_date == target)
            .where(OptionExerciseAssignment.transaction_type.notin_(("Buy", "Sell")))
            .order_by(OptionExerciseAssignment.symbol)
        )
        .scalars()
        .all()
    )
    cash = (
        session.execute(
            select(CashTransaction)
            .where(CashTransaction.type == "Deposits/Withdrawals")
            .where(CashTransaction.settle_date == target)
            .order_by(CashTransaction.event_datetime)
        )
        .scalars()
        .all()
    )
    if not (trades or eae or cash):
        return None

    base_ccy = get_settings().base_currency
    blocks: list[str] = []

    for t in trades:
        is_buy = (t.buy_sell or "").upper() == "BUY"
        action = "Compra" if is_buy else "Venta"
        verb = "Buy" if is_buy else "Sell"
        name = _instrument_name(session, conid=t.conid, fallback=t.symbol)
        qty = float(t.quantity or 0)
        # net_cash = proceeds net of commission — the actual cash movement,
        # matching the old Sheet's "Importe".
        net = float(t.net_cash or 0)
        lines = [
            f"<b>{action} {_ddmmyyyy(target)}</b>",
            f"{name}:",
            f"{verb} {qty:.0f} {name}",
            f"Importe: {_amounts(net, t.fx_rate_to_base, base_ccy, t.currency or '')}",
        ]
        if t.currency and t.currency != base_ccy:
            lines.append(f"Tipo cambio: {_fmt_fx(float(t.fx_rate_to_base or 0))}")
        blocks.append("\n".join(lines))

    for e in eae:
        name = _instrument_name(session, conid=e.conid, fallback=e.symbol)
        qty = float(e.quantity or 0)
        blocks.append(
            f"<b>Ejercicio/Asignación {_ddmmyyyy(target)}</b>\n"
            f"{name}:\n{e.transaction_type or 'E&A'} {qty:.0f} {name}"
        )

    for c in cash:
        amt = float(c.amount or 0)
        action = "Ingreso" if amt >= 0 else "Retirada"
        desc = (c.description or "").strip()
        lines = [f"<b>{action} {_ddmmyyyy(target)}</b>"]
        if desc:
            lines.append(f"{desc}:")
        lines.append(
            f"Importe: {_amounts(amt, c.fx_rate_to_base, base_ccy, c.currency or '')}"
        )
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def commissions_report(
    session: Session, ref: date, base_ccy: str
) -> str | None:
    """Month-to-date commissions as of `ref`, in their native (mostly USD)
    amount — the raw `ib_commission` sum, matching the old Sheet's "...$"
    figure. None if no trades this month.
    """
    period = resolve_period(period="mtd", today=ref)
    total = session.execute(
        select(func.sum(Trade.ib_commission))
        .where(Trade.trade_date >= period.start)
        .where(Trade.trade_date <= period.end)
    ).scalar()
    if not total:
        return None
    return (
        f"Comisiones mensuales utilizadas hasta fecha {_ddmmyyyy(ref)}: "
        f"{_fmt(abs(float(total)))}$"
    )


# ------------------------------------------------------- token expiry warning
def token_expiry_message(
    expiry: date, today: date, warn_days: int
) -> str | None:
    """Telegram warning if the Flex token expires within `warn_days` days (or
    already has). None when it's further out than the window. `expiry` is the
    date shown in IBKR Client Portal → Flex Web Service (the token returns
    HTTP 604 once past it)."""
    days = (expiry - today).days
    portal = (
        "IBKR Client Portal → Performance & Reports → Flex Queries → Flex Web Service"
    )
    if days < 0:
        return (
            f"🔴 <b>Token Flex CADUCADO</b>\n"
            f"El token de IBKR Flex caducó el {_ddmmyyyy(expiry)} (hace {-days} día/s). "
            f"El pull diario falla con HTTP 604 hasta que lo regeneres: {portal}, "
            f"y actualiza IB_FLEX_TOKEN + IB_FLEX_TOKEN_EXPIRY en .env."
        )
    if days <= warn_days:
        cuando = "hoy" if days == 0 else f"en {days} día/s"
        return (
            f"⚠️ <b>El token Flex caduca pronto</b>\n"
            f"Caduca el {_ddmmyyyy(expiry)} ({cuando}). Regenéralo en {portal} "
            f"y actualiza IB_FLEX_TOKEN + IB_FLEX_TOKEN_EXPIRY en .env."
        )
    return None


# ------------------------------------------------------------ orchestration
def _get_watermark(session: Session) -> date | None:
    row = session.get(NotificationState, _WATERMARK_KEY)
    if row is None or not row.value:
        return None
    try:
        return date.fromisoformat(row.value)
    except ValueError:
        return None


def _set_watermark(session: Session, d: date) -> None:
    row = session.get(NotificationState, _WATERMARK_KEY)
    if row is None:
        session.add(NotificationState(key=_WATERMARK_KEY, value=d.isoformat()))
    else:
        row.value = d.isoformat()


def daily_notify(
    session: Session,
    *,
    target: date | None = None,
    settings: Settings | None = None,
    force: bool = False,
    update_watermark: bool = True,
    today: date | None = None,
    max_catchup_days: int = 14,
) -> dict:
    """Build and send the dividend / operations / commissions messages.

    `target` defaults to *yesterday*. To stop a single failed pull from
    silently dropping a day, the normal (non-forced) path **catches up every
    unnotified day** between the `NotificationState` watermark and `target` —
    a lone high-water mark would otherwise leapfrog (and lose forever) any day
    whose run failed. The catch-up is capped at `max_catchup_days` so a very
    stale watermark can't flood the chat. A forced/explicit `target` is a
    one-day operation that bypasses the watermark.

    A send error is logged, never raised — the caller is the Flex pipeline and
    must not be broken by a Telegram outage. Returns a summary dict.
    """
    s = settings or get_settings()
    today = today or date.today()
    target = target or (today - timedelta(days=1))

    last = _get_watermark(session)
    if force or last is None:
        days = [target]
    elif target <= last:
        return {"target": target.isoformat(), "skipped": "already notified", "sent": []}
    else:
        start = last + timedelta(days=1)
        floor = target - timedelta(days=max_catchup_days - 1)
        if start < floor:
            log.warning(
                "Notify catch-up %s→%s exceeds %d days; older days skipped.",
                start, target, max_catchup_days,
            )
            start = floor
        days = [start + timedelta(days=i) for i in range((target - start).days + 1)]

    configured = telegram.is_configured(s)
    built: list[str] = []
    sent: list[str] = []
    for day in days:
        day_reports = {
            "operations": operations_report(session, day),
            "dividends": dividends_report(session, day, s.base_currency),
            "commissions": commissions_report(session, day, s.base_currency),
        }
        messages = {k: v for k, v in day_reports.items() if v}
        if not messages:
            continue
        built.extend(messages.keys())
        if configured:
            for kind, text in messages.items():
                try:
                    if telegram.send_message(text, settings=s):
                        sent.append(kind)
                except Exception as e:  # noqa: BLE001
                    log.warning("Telegram notify '%s' for %s failed: %s", kind, day, e)
        else:
            log.info("Telegram not configured; %d message(s) for %s not sent.", len(messages), day)

    if update_watermark and not force:
        _set_watermark(session, target)

    return {
        "target": target.isoformat(),
        "days": [d.isoformat() for d in days],
        "built": built,
        "sent": sent,
        "configured": configured,
    }


__all__ = [
    "commissions_report",
    "daily_notify",
    "dividends_report",
    "operations_report",
    "token_expiry_message",
]
