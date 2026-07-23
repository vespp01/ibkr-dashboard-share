"""Money-weighted return — XIRR via pyxirr.

External cash flows from `cash_transactions` (Deposits/Withdrawals,
Internal Transfers) plus a single positive terminal value = current NAV.

Sign convention (required for pyxirr to converge):
  - Deposits (capital INTO the account)  → NEGATIVE
  - Withdrawals (capital OUT)            → POSITIVE
  - Terminal NAV (realised at terminal)  → POSITIVE
IBKR's PortfolioAnalyst uses the same convention.

In `CashTransaction.amount`, deposits are stored as positive numbers
(broker-liability view), so we negate them when building the flow list.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import CashTransaction, NavDaily
from .filters import PeriodFilter
from .metrics import DEPOSIT_TYPES


def xirr_summary(
    session: Session,
    *,
    period: PeriodFilter | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    try:
        from pyxirr import xirr
    except ImportError:  # pragma: no cover
        return {"xirr_pct": None, "error": "pyxirr is not installed"}

    note: str | None = None

    base_amount = CashTransaction.amount * func.coalesce(
        CashTransaction.fx_rate_to_base, 1
    )
    q = select(
        CashTransaction.settle_date,
        CashTransaction.event_datetime,
        func.sum(base_amount).label("amount"),
    ).where(CashTransaction.type.in_(DEPOSIT_TYPES)).group_by(
        CashTransaction.settle_date, CashTransaction.event_datetime
    )
    if account_id:
        q = q.where(CashTransaction.account_id == account_id)
    if period and period.start:
        q = q.where(CashTransaction.settle_date >= period.start)
    if period and period.end:
        q = q.where(CashTransaction.settle_date <= period.end)

    flows: list[tuple[date, float]] = []
    for r in session.execute(q):
        d = r.settle_date or (r.event_datetime.date() if r.event_datetime else None)
        if d is None or r.amount is None:
            continue
        # CashTransaction.amount is positive for deposits, negative for withdrawals.
        # pyxirr needs the opposite (cash into the investment = negative outflow
        # from the investor's perspective), so we negate the stored sign.
        flows.append((d, -float(r.amount)))

    # Sub-period XIRR: synthesise the opening NAV as a negative "initial
    # investment" flow at period.start. Without this, pyxirr treats the
    # cumulative wealth at start as if it materialised from the few deposits
    # made during the period — producing absurd rates like +1000000 %.
    # All-time XIRR (period.start is None) does not need this synthesis.
    if period is not None and period.start is not None:
        opening_q = (
            select(NavDaily.snapshot_date, NavDaily.nav_total)
            .where(NavDaily.snapshot_date < period.start)
            .where(NavDaily.nav_total.is_not(None))  # skip cashflow-only rows
            .order_by(NavDaily.snapshot_date.desc())
            .limit(1)
        )
        if account_id:
            opening_q = opening_q.where(NavDaily.account_id == account_id)
        opening = session.execute(opening_q).first()
        if opening is not None and opening.nav_total is not None:
            flows.insert(0, (period.start, -float(opening.nav_total)))
        else:
            note = (
                "No NAV snapshot before period.start; cannot synthesise opening "
                "investment. Falling back to deposits-only flows (likely understated)."
            )

    nav_q = (
        select(NavDaily.snapshot_date, NavDaily.nav_total)
        .where(NavDaily.nav_total.is_not(None))  # skip cashflow-only rows
        .order_by(NavDaily.snapshot_date.desc())
        .limit(1)
    )
    if account_id:
        nav_q = nav_q.where(NavDaily.account_id == account_id)
    if period and period.end:
        nav_q = nav_q.where(NavDaily.snapshot_date <= period.end)
    nav_row = session.execute(nav_q).first()
    if nav_row is None or nav_row.nav_total is None:
        return {
            "xirr_pct": None,
            "flows": [{"date": d.isoformat(), "amount": a} for d, a in flows],
            "error": "No NAV available for terminal value",
        }
    flows.append((nav_row.snapshot_date, float(nav_row.nav_total)))

    if len(flows) < 2:
        return {"xirr_pct": None, "flows": [{"date": d.isoformat(), "amount": a} for d, a in flows]}

    # pyxirr signature: xirr(dates, amounts) or xirr([(d, a), ...])
    try:
        rate = xirr(flows)
    except Exception as e:  # noqa: BLE001
        return {
            "xirr_pct": None,
            "error": f"XIRR did not converge: {e}",
            "flows": [{"date": d.isoformat(), "amount": a} for d, a in flows],
        }
    return {
        "xirr_pct": rate * 100 if rate is not None else None,
        "terminal_nav_base": float(nav_row.nav_total),
        "terminal_date": nav_row.snapshot_date.isoformat(),
        "flow_count": len(flows),
        "flows": [{"date": d.isoformat(), "amount": a} for d, a in flows],
        "note": note,
    }
