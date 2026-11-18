"""External-flow definition — one place, used by TWR reconciliation, XIRR,
all-time return and the waterfall.

An external flow is capital crossing the account boundary. IBKR books it in
`ChangeInNAV` on the SAME date as the NAV move it causes, in base currency:

    flow_t = depositsWithdrawals_t + assetTransfers_t

`assetTransfers` are in-kind contributions (ACATS / FOP / internal transfers of
securities). They add NAV without being performance. `cash_transactions`
cannot supply them (the `transfers` table has no base value) and keys flows on
`settle_date`, which drifts from the NAV date, so it is NOT used for flows.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import ChangeInNavDaily

# SQL expression: the daily external flow in base currency.
CIN_FLOW = (
    func.coalesce(ChangeInNavDaily.deposits_withdrawals, 0)
    + func.coalesce(ChangeInNavDaily.asset_transfers, 0)
)


def cin_flows(
    session: Session,
    *,
    account_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[tuple[date, float]]:
    """Non-zero external flows per CIN date (deposits positive), base currency."""
    q = (
        select(ChangeInNavDaily.snapshot_date, func.sum(CIN_FLOW))
        .group_by(ChangeInNavDaily.snapshot_date)
        .order_by(ChangeInNavDaily.snapshot_date.asc())
    )
    if account_id:
        q = q.where(ChangeInNavDaily.account_id == account_id)
    if start:
        q = q.where(ChangeInNavDaily.snapshot_date >= start)
    if end:
        q = q.where(ChangeInNavDaily.snapshot_date <= end)
    out: list[tuple[date, float]] = []
    for d, amount in session.execute(q):
        if amount is None or float(amount) == 0.0:
            continue
        out.append((d, float(amount)))
    return out


def cin_net_flows(
    session: Session,
    *,
    account_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> float:
    """Σ external flows over the window (base currency)."""
    return sum(a for _, a in cin_flows(session, account_id=account_id, start=start, end=end))
