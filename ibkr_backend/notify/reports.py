"""Daily Telegram reports — dividends, operations, month-to-date commissions.

Re-implements, against the dashboard's own DB, the three notifications a
legacy spreadsheet workflow used to send — one dated message per section, each
item as ONE block showing native + base currency, instead of two separate
per-currency blocks:

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

import html
import logging
import re
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analytics.commissions import commissions_summary
from ..analytics.dividends_calc import DIVIDEND_TYPES, WITHHOLDING_TYPES
from ..analytics.filters import PeriodFilter, resolve_period
from ..config import Settings, get_settings
from ..db.models import (
    CashTransaction,
    Instrument,
    NotificationState,
    OptionExerciseAssignment,
    Trade,
    _utcnow,
)
from . import telegram

log = logging.getLogger(__name__)

_WATERMARK_KEY = "last_notified_date"
# Timestamp of the last *clean* notify run — the clock for the late-arrival
# re-scan (items whose Flex row was ingested after we already reported a day).
_RUN_TS_KEY = "last_notify_run_at"
_LATE_PREFIX = "⏰ <i>Movimiento con fecha atrasada</i>\n"
# One performance pulse per day (a same-day re-run must not duplicate it).
_PERF_PULSE_KEY = "last_perf_pulse_date"
# Value signature of the last commissions message sent — the MTD commissions
# figure is a cumulative snapshot, not a per-day event, so it is deduped by
# value (month start + total) instead of by day.
_COMMISSIONS_KEY = "last_commissions_sig"

# `TICKER(ISIN) Cash Dividend USD 1.67 per Share (...)` — ticker is the
# leading token, ISIN sits in the parens, the per-share rate + currency
# follow "Cash Dividend". Case varies (some rows are upper-cased).
_TICKER_RE = re.compile(r"^\s*([A-Za-z0-9.]+)\s*\(")
_ISIN_RE = re.compile(r"\(([A-Z]{2}[A-Z0-9]{9}\d)\)")
_RATE_RE = re.compile(r"(?i)\b([A-Z]{3})\s+([\d.]+)\s+per\s+share")


# ------------------------------------------------------------- small helpers
def _fmt(x: float | None) -> str:
    return f"{(x or 0):,.2f}"


def _fmt_fx(x: float | None) -> str:
    return f"{x:.6f}" if x else "1.000000"


def _fmt_rate(x: float | None) -> str:
    if not x:
        return "0"
    return f"{x:.4f}".rstrip("0").rstrip(".")


def _pct(x: float | None) -> str:
    return f"{x:+.2f}%" if x is not None else "n/d"


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
    session: Session,
    target: date,
    base_ccy: str,
    *,
    inserted_after: datetime | None = None,
) -> str | None:
    """Dividends collected on `target` (by settle_date). None if there are none.

    `inserted_after` restricts to rows whose Flex row was ingested after that
    timestamp — used by the late-arrival re-scan to catch dividends that landed
    in Flex only after the day was first reported (no duplicates of items
    already sent).
    """
    query = (
        select(CashTransaction)
        .where(CashTransaction.type.in_(DIVIDEND_TYPES + WITHHOLDING_TYPES))
        .where(CashTransaction.settle_date == target)
    )
    if inserted_after is not None:
        query = query.where(CashTransaction.inserted_at > inserted_after)
    rows = session.execute(query).scalars().all()
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
    total_net_base = 0.0
    for g in sorted(groups.values(), key=lambda x: -abs(x["gross"])):
        name = html.escape(
            _instrument_name(
                session, conid=g["conid"], isin=g["isin"], fallback=g["ticker"]
            )
        )
        ccy = g["currency"] or ""
        fx = g["fx"]
        net = g["gross"] + g["wht"]
        total_net_base += net * float(fx or 1)
        shares = (g["gross"] / g["rate"]) if g["rate"] else None
        rate_txt = (
            f"{_fmt_rate(g['rate'])} {ccy}/acc × {shares:.0f}"
            if g["rate"] else ""
        )
        if g["wht"]:
            pct = (-g["wht"] / g["gross"] * 100) if g["gross"] else 0
            bruto_label = f"Bruto ({rate_txt})" if rate_txt else "Bruto"
            lines = [
                f"<b>{name}</b>",
                f"Neto: <b>{_amounts(net, fx, base_ccy, ccy)}</b>",
                f"{bruto_label}: {_amounts(g['gross'], fx, base_ccy, ccy)}",
                f"Retenido ({pct:.2f}%): {_amounts(g['wht'], fx, base_ccy, ccy)}",
            ]
        else:
            importe = f"Importe: <b>{_amounts(net, fx, base_ccy, ccy)}</b>"
            if rate_txt:
                importe += f" · {rate_txt}"
            lines = [f"<b>{name}</b>", importe]
        if ccy and ccy != base_ccy:
            lines.append(f"Cambio: {_fmt_fx(fx)}")
        blocks.append("\n".join(lines))

    parts = [f"💰 <b>Dividendos {_ddmmyyyy(target)}</b>", *blocks]
    if len(blocks) >= 2:
        parts.append(f"Total neto del día: <b>{total_net_base:,.2f} {base_ccy}</b>")
    return "\n\n".join(parts)


def operations_report(
    session: Session,
    target: date,
    base_ccy: str,
    *,
    inserted_after: datetime | None = None,
) -> str | None:
    """Operations on `target`: stock/option buys/sells (forex and option
    exercise/assignment book trades excluded), option exercise/assignment, and
    cash deposits/withdrawals. None if there were none.

    `inserted_after` restricts trades + cash to rows ingested after that
    timestamp (late-arrival re-scan). Exercise/assignment rows have no ingestion
    timestamp, so they are omitted from that mode to avoid re-sending them."""
    trade_query = (
        select(Trade)
        .where(Trade.trade_date == target)
        .where(Trade.asset_class.in_(("STK", "OPT")))
        .order_by(Trade.trade_datetime)
    )
    if inserted_after is not None:
        trade_query = trade_query.where(Trade.inserted_at > inserted_after)
    trades = session.execute(trade_query).scalars().all()
    # Exclude exercise/assignment book trades — they are the *result* of an
    # option E&A (shown in their own block), not a discretionary buy/sell.
    trades = [t for t in trades if (t.transaction_type or "") != "BookTrade"]

    if inserted_after is None:
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
    else:
        eae = []  # no inserted_at on E&A rows — cannot late-scan them safely
    cash_query = (
        select(CashTransaction)
        .where(CashTransaction.type == "Deposits/Withdrawals")
        .where(CashTransaction.settle_date == target)
        .order_by(CashTransaction.event_datetime)
    )
    if inserted_after is not None:
        cash_query = cash_query.where(CashTransaction.inserted_at > inserted_after)
    cash = session.execute(cash_query).scalars().all()
    if not (trades or eae or cash):
        return None

    blocks: list[str] = []

    for t in trades:
        is_buy = (t.buy_sell or "").upper() == "BUY"
        action = "Compra" if is_buy else "Venta"
        name = html.escape(_instrument_name(session, conid=t.conid, fallback=t.symbol))
        qty = float(t.quantity or 0)
        ccy = t.currency or ""
        # Raw per-unit fill price (multiplier NOT applied — that only affects
        # proceeds/positionValue on derivatives).
        price = f"{float(t.trade_price or 0):g}"
        # net_cash = proceeds net of commission — the actual cash movement,
        # matching the old Sheet's "Importe".
        net = float(t.net_cash or 0)
        lines = [
            f"<b>{action}</b> · {qty:.0f} × {name} a {price} {ccy}",
            f"Importe: {_amounts(net, t.fx_rate_to_base, base_ccy, ccy)}",
        ]
        if ccy and ccy != base_ccy:
            lines.append(f"Cambio: {_fmt_fx(float(t.fx_rate_to_base or 0))}")
        blocks.append("\n".join(lines))

    for e in eae:
        name = html.escape(_instrument_name(session, conid=e.conid, fallback=e.symbol))
        qty = float(e.quantity or 0)
        tipo_ea = {"Assignment": "Asignación", "Exercise": "Ejercicio"}.get(
            e.transaction_type or ""
        ) or (html.escape(e.transaction_type) if e.transaction_type else "E&amp;A")
        blocks.append(f"<b>{tipo_ea}</b> · {qty:.0f} × {name}")

    for c in cash:
        amt = float(c.amount or 0)
        action = "Ingreso" if amt >= 0 else "Retirada"
        desc = (c.description or "").strip()
        head = f"<b>{action}</b>"
        if desc:
            head += f" · {html.escape(desc)}"
        blocks.append(
            f"{head}\n"
            f"Importe: {_amounts(amt, c.fx_rate_to_base, base_ccy, c.currency or '')}"
        )

    return "\n\n".join([f"🧾 <b>Operaciones {_ddmmyyyy(target)}</b>", *blocks])


def _commissions_mtd_total(
    session: Session, ref: date
) -> tuple[PeriodFilter, float | None]:
    """Month-to-date broker commissions as of `ref` in base currency
    (`commissions_summary`: `ib_commission` converted per its own currency),
    together with the resolved MTD period. Total is None if there are none."""
    period = resolve_period(period="mtd", today=ref)
    total = commissions_summary(session, period=period)["total_base"]
    return period, (float(total) if total else None)


def commissions_report(
    session: Session, ref: date, base_ccy: str
) -> str | None:
    """Month-to-date commissions as of `ref`, in base currency, anchored
    against the previous full month. None if no commissions this month.
    """
    period, total = _commissions_mtd_total(session, ref)
    if total is None:
        return None
    # Previous natural month: same sum over its full window.
    _prev_period, prev_total = _commissions_mtd_total(
        session, period.start - timedelta(days=1)
    )
    text = f"💸 Comisiones del mes a {_ddmmyyyy(ref)}: <b>{abs(total):,.2f} {base_ccy}</b>"
    if prev_total:
        text += f" · mes anterior completo: {abs(prev_total):,.2f} {base_ccy}"
    return text


# ------------------------------------------------------- performance pulse
def performance_report(
    session: Session,
    *,
    base_ccy: str,
    today: date | None = None,
    account_id: str | None = None,
) -> str | None:
    """Daily performance pulse — month + YTD return vs S&P 500 + margin APR.

    Sourced from the same analytics the Rentabilidad / Margen pages use (chained
    ChangeInNAV daily TWR, base currency; MTD and YTD include day 1 of the
    window). Lines with unreliable or absent data
    are omitted; returns None when nothing is available. Never invents metrics.
    """
    from ..analytics import margin_interest_summary, twr_series, vs_benchmark

    today = today or date.today()
    lines = [f"📊 <b>Pulso de cartera · {_ddmmyyyy(today)}</b>"]
    has_body = False

    mtd = twr_series(
        session,
        period=resolve_period(period="mtd", today=today),
        account_id=account_id,
        display_currency=base_ccy,
        summary=True,
    )
    mtd_pct = mtd.get("twr_pct")
    if mtd_pct is not None and (mtd.get("quality") or {}).get("is_reliable", True):
        lines.append(f"Mes: <b>{_pct(mtd_pct)}</b>")
        has_body = True

    vsb = vs_benchmark(
        session,
        period=resolve_period(period="ytd", today=today),
        account_id=account_id,
        benchmark_currency=base_ccy,
        display_currency=base_ccy,
    )
    level = vsb.get("level_comparison") if vsb.get("is_reliable") else None
    if level:
        lines.append(
            f"YTD: <b>{_pct(level.get('portfolio_return_pct'))}</b> · "
            f"S&amp;P 500 (en {base_ccy}) {_pct(level.get('benchmark_return_pct'))} · "
            f"exceso <b>{_pct(level.get('excess_return_pct'))}</b>"
        )
        has_body = True
    else:
        ytd = twr_series(
            session,
            period=resolve_period(period="ytd", today=today),
            account_id=account_id,
            display_currency=base_ccy,
            summary=True,
        )
        ytd_pct = ytd.get("twr_pct")
        if ytd_pct is not None and (ytd.get("quality") or {}).get("is_reliable", True):
            lines.append(f"YTD: <b>{_pct(ytd_pct)}</b>")
            has_body = True

    margin = margin_interest_summary(
        session,
        period=resolve_period(period="ytd", today=today),
        account_id=account_id,
    )
    apr = margin.get("effective_apr_pct")
    if apr is not None and margin.get("days_with_debt"):
        margin_line = f"Margen: {apr:.2f}% APR efectivo"
        paid = margin.get("paid_total_base")
        if paid:
            margin_line += f" · {abs(paid):,.0f} {base_ccy} pagados YTD"
        lines.append(margin_line)
        has_body = True

    return "\n".join(lines) if has_body else None


# ------------------------------------------------------- token expiry warning
def token_expiry_message(
    expiry: date, today: date, warn_days: int
) -> str | None:
    """Telegram warning if the Flex token expires within `warn_days` days (or
    already has). None when it's further out than the window. `expiry` is the
    date shown in IBKR Client Portal → Flex Web Service (the token returns
    HTTP 604 once past it)."""
    days = (expiry - today).days
    checklist = (
        "1. Regenera el token: IBKR Client Portal → Performance &amp; Reports → "
        "Flex Queries → Flex Web Service\n"
        "2. Actualiza IB_FLEX_TOKEN e IB_FLEX_TOKEN_EXPIRY en .env\n"
        "3. Aplica el cambio: docker compose up -d backend"
    )
    if days < 0:
        hace_txt = "hace 1 día" if days == -1 else f"hace {-days} días"
        return (
            f"🔴 <b>Token Flex caducado {hace_txt}</b>\n"
            f"Caducó el {_ddmmyyyy(expiry)} · el pull diario falla con HTTP 604.\n"
            f"{checklist}"
        )
    if days <= warn_days:
        if days == 0:
            cuando = "hoy"
        elif days == 1:
            cuando = "mañana"
        else:
            cuando = f"en {days} días"
        return (
            f"⚠️ <b>Token Flex caduca {cuando}</b> ({_ddmmyyyy(expiry)})\n"
            f"{checklist}"
        )
    return None


# ------------------------------------------------------------ orchestration
def _wants(sections: list[str] | None, name: str) -> bool:
    """Whether the digest should include a section (``None`` → all sections)."""
    return sections is None or name in sections


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


def _get_run_ts(session: Session) -> datetime | None:
    row = session.get(NotificationState, _RUN_TS_KEY)
    if row is None or not row.value:
        return None
    try:
        return datetime.fromisoformat(row.value)
    except ValueError:
        return None


def _set_run_ts(session: Session, ts: datetime) -> None:
    row = session.get(NotificationState, _RUN_TS_KEY)
    if row is None:
        session.add(NotificationState(key=_RUN_TS_KEY, value=ts.isoformat()))
    else:
        row.value = ts.isoformat()


def _set_perf_pulse_date(session: Session, d: date) -> None:
    row = session.get(NotificationState, _PERF_PULSE_KEY)
    if row is None:
        session.add(NotificationState(key=_PERF_PULSE_KEY, value=d.isoformat()))
    else:
        row.value = d.isoformat()


def _get_commissions_sig(session: Session) -> str | None:
    row = session.get(NotificationState, _COMMISSIONS_KEY)
    if row is None or not row.value:
        return None
    return row.value


def _set_commissions_sig(session: Session, sig: str) -> None:
    row = session.get(NotificationState, _COMMISSIONS_KEY)
    if row is None:
        session.add(NotificationState(key=_COMMISSIONS_KEY, value=sig))
    else:
        row.value = sig


def send_performance_pulse(
    session: Session,
    *,
    settings: Settings | None = None,
    today: date | None = None,
    force: bool = False,
) -> dict:
    """Send the daily performance pulse once per day (deduped by date).

    No-op (never raises) if Telegram is not configured or there is no reliable
    data yet. The sent-date marker only advances after a successful send, so a
    transient Telegram failure retries on the next run (at-least-once).
    """
    s = settings or get_settings()
    today = today or date.today()
    if not telegram.is_configured(s):
        return {"sent": False, "skipped": "not configured"}
    if not force:
        row = session.get(NotificationState, _PERF_PULSE_KEY)
        if row is not None and row.value == today.isoformat():
            return {"sent": False, "skipped": "already sent today"}
    text = performance_report(session, base_ccy=s.base_currency, today=today)
    if not text:
        return {"sent": False, "skipped": "no data"}
    try:
        delivered = telegram.send_message(text, settings=s)
    except Exception as e:  # noqa: BLE001
        log.warning("Performance pulse send failed: %s", e)
        return {"sent": False, "error": str(e)}
    if not delivered:  # Telegram answered {ok:false}: leave the marker unarmed
        return {"sent": False, "error": "telegram not ok"}
    if not force:
        _set_perf_pulse_date(session, today)
    return {"sent": True}


def daily_notify(
    session: Session,
    *,
    target: date | None = None,
    settings: Settings | None = None,
    force: bool = False,
    update_watermark: bool = True,
    today: date | None = None,
    now: datetime | None = None,
    max_catchup_days: int = 14,
    late_lookback_days: int = 5,
    sections: list[str] | None = None,
) -> dict:
    """Build and send the dividend / operations / commissions messages.

    `sections` optionally restricts which of ``dividends`` / ``operations`` /
    ``commissions`` are built and sent (``None`` → all three), so the dashboard
    can toggle digest content per section. Dividends and operations are per-day
    events and go through the per-day catch-up loop; the month-to-date
    commissions figure is a cumulative snapshot, sent at most once per run (for
    `target` only) and deduped by value signature — never once per caught-up day.

    `target` defaults to *yesterday*. To stop a single failed pull from
    silently dropping a day, the normal (non-forced) path **catches up every
    unnotified day** between the `NotificationState` watermark and `target` —
    a lone high-water mark would otherwise leapfrog (and lose forever) any day
    whose run failed. The catch-up is capped at `max_catchup_days` so a very
    stale watermark can't flood the chat. A forced/explicit `target` is a
    one-day operation that bypasses the watermark.

    Delivery is **at-least-once**: the watermark only advances to the last day
    whose messages were all sent successfully. If Telegram fails mid-run the
    watermark stops there, so the next run's catch-up retries the undelivered
    day instead of losing it (mirrors the margin-alert re-arm pattern).

    A **late-arrival re-scan** then re-sends dividends/operations whose Flex row
    was ingested since the last clean run for days already reported — catching
    figures that landed in Flex after their day's digest went out. Each is
    flagged as a late movement (`inserted_after` filter → no duplicates).

    A send error is logged, never raised past this function — the caller is the
    Flex pipeline and must not be broken by a Telegram outage. Returns a summary.
    """
    s = settings or get_settings()
    now_ts = now or _utcnow()
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
    run_ts = _get_run_ts(session)
    built: list[str] = []
    sent: list[str] = []
    delivered_through: date | None = None
    outage = False

    for day in days:
        day_reports: dict[str, str | None] = {}
        if _wants(sections, "operations"):
            day_reports["operations"] = operations_report(session, day, s.base_currency)
        if _wants(sections, "dividends"):
            day_reports["dividends"] = dividends_report(session, day, s.base_currency)
        messages = {k: v for k, v in day_reports.items() if v}
        if not messages:
            delivered_through = day  # nothing to send — safe to advance past
            continue
        built.extend(messages.keys())
        if not configured:
            log.info("Telegram not configured; %d message(s) for %s not sent.", len(messages), day)
            delivered_through = day  # no channel — don't hold a backlog
            continue
        day_ok = True
        for kind, text in messages.items():
            try:
                if telegram.send_message(text, settings=s):
                    sent.append(kind)
                else:
                    day_ok = False
            except Exception as e:  # noqa: BLE001
                log.warning("Telegram notify '%s' for %s failed: %s", kind, day, e)
                day_ok = False
        if day_ok:
            delivered_through = day
        else:
            outage = True  # stop advancing here; next run's catch-up retries
            break

    # Late-arrival re-scan (only on a clean run, and only with a prior run
    # timestamp to compare against — else every row looks "new").
    late_sent: list[str] = []
    if configured and not force and not outage and run_ts is not None and days:
        window_start = target - timedelta(days=late_lookback_days - 1)
        reported_upto = days[0] - timedelta(days=1)
        late_day = window_start
        while late_day <= reported_upto:
            late_reports: dict[str, str | None] = {}
            if _wants(sections, "operations"):
                late_reports["operations"] = operations_report(
                    session, late_day, s.base_currency, inserted_after=run_ts
                )
            if _wants(sections, "dividends"):
                late_reports["dividends"] = dividends_report(
                    session, late_day, s.base_currency, inserted_after=run_ts
                )
            for kind, text in late_reports.items():
                if not text:
                    continue
                try:
                    if telegram.send_message(_LATE_PREFIX + text, settings=s):
                        late_sent.append(f"{kind}@{late_day.isoformat()}")
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "Telegram late notify '%s' for %s failed: %s", kind, late_day, e
                    )
            late_day += timedelta(days=1)

    # Month-to-date commissions — a cumulative snapshot, not a per-day event:
    # sent at most once per run, for `target` only, deduped by value signature
    # (month start + total) so an unchanged figure isn't re-sent day after day.
    # The marker only advances after a successful send (at-least-once), and
    # `force` bypasses the dedup without writing it — mirroring the watermark.
    if _wants(sections, "commissions") and configured and not outage:
        period, total = _commissions_mtd_total(session, target)
        if total:
            sig = f"{period.start.isoformat()}|{abs(total):.2f}"
            if force or _get_commissions_sig(session) != sig:
                built.append("commissions")
                text = commissions_report(session, target, s.base_currency)
                try:
                    if telegram.send_message(text, settings=s):
                        sent.append("commissions")
                        if not force:
                            _set_commissions_sig(session, sig)
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "Telegram notify 'commissions' for %s failed: %s", target, e
                    )

    if update_watermark and not force:
        if delivered_through is not None:
            _set_watermark(session, delivered_through)
        elif last is None:
            # First-ever run failed on its only day: anchor just before target
            # so the next catch-up retries this day instead of leapfrogging it.
            _set_watermark(session, target - timedelta(days=1))
        # else: keep the existing watermark; catch-up resumes from last+1.

    # Advance the late-scan clock only after a clean run, so late items that
    # failed to send are retried next time.
    if not force and not outage:
        _set_run_ts(session, now_ts)

    return {
        "target": target.isoformat(),
        "days": [d.isoformat() for d in days],
        "built": built,
        "sent": sent,
        "late_sent": late_sent,
        "delivered_through": delivered_through.isoformat() if delivered_through else None,
        "outage": outage,
        "configured": configured,
    }


__all__ = [
    "commissions_report",
    "daily_notify",
    "dividends_report",
    "operations_report",
    "performance_report",
    "send_performance_pulse",
    "token_expiry_message",
]
