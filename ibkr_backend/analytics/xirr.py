"""Money-weighted return — XIRR via pyxirr.

External cash flows come from `change_in_nav_daily` (`depositsWithdrawals +
assetTransfers`, on the ChangeInNAV date — see `flows.py`) plus a single
positive terminal value = ending NAV. In-kind transfers are flows: counting
them as profit overstates XIRR.

Sign convention (required for pyxirr to converge):
  - Deposits (capital INTO the account)  -> NEGATIVE
  - Withdrawals (capital OUT)            -> POSITIVE
  - Terminal NAV (realised at terminal)  -> POSITIVE

XIRR is an annual rate: for windows shorter than 365 days `xirr_pct` is null
(GIPS forbids annualising short periods); the window length is reported.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import NavDaily
from .filters import PeriodFilter
from .flows import cin_flows

_ANNUALISE_MIN_DAYS = 365


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

    # CIN flows: deposits positive -> negated for pyxirr.
    flows: list[tuple[date, float]] = [
        (d, -a)
        for d, a in cin_flows(
            session,
            account_id=account_id,
            start=period.start if period else None,
            end=period.end if period else None,
        )
    ]

    # Sub-period XIRR: synthesise the opening NAV as a negative "initial
    # investment" flow at period.start. Without this, pyxirr treats the
    # cumulative wealth at start as if it materialised from the few deposits
    # made during the period — producing absurd rates like +1000000 %.
    # All-time XIRR (period.start is None) does not need this synthesis.
    if period is not None and period.start is not None:
        opening_q = (
            select(NavDaily.snapshot_date, NavDaily.nav_total)
            .where(NavDaily.snapshot_date < period.start)
            .where(NavDaily.nav_total.is_not(None))  # skip NAV-less rows
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
        .where(NavDaily.nav_total.is_not(None))  # skip NAV-less rows
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

    window_days = (nav_row.snapshot_date - min(d for d, _ in flows)).days

    # pyxirr signature: xirr(dates, amounts) or xirr([(d, a), ...])
    try:
        rate = xirr(flows)
    except Exception as e:  # noqa: BLE001
        return {
            "xirr_pct": None,
            "error": f"XIRR did not converge: {e}",
            "flows": [{"date": d.isoformat(), "amount": a} for d, a in flows],
        }
    annualised = window_days >= _ANNUALISE_MIN_DAYS
    if not annualised:
        note = "; ".join(filter(None, [note, "Ventana < 365 días: XIRR no se anualiza (GIPS)."]))
    return {
        "xirr_pct": rate * 100 if (rate is not None and annualised) else None,
        "annualised": annualised,
        "window_days": window_days,
        "terminal_nav_base": float(nav_row.nav_total),
        "terminal_date": nav_row.snapshot_date.isoformat(),
        "flow_count": len(flows),
        "flows": [{"date": d.isoformat(), "amount": a} for d, a in flows],
        "flow_source": "change_in_nav_daily.depositsWithdrawals + assetTransfers",
        "note": note,
    }
