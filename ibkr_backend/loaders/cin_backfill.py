"""One-off data steps for the performance-math fixes (2026-10).

Each function is idempotent and dry-run unless `apply=True`; the CLI wrappers
live in `cli.py` (`backfill-cin-columns`, `drop-csv-flow-rows`,
`drop-navless-nav-rows`). Back up the DB before running with `--apply`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..analytics.fx import fx_lookup
from ..db.models import CashTransaction, ChangeInNavDaily, NavDaily

_DW_TYPE = "Deposits/Withdrawals"


def _dec(v: str | None) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def backfill_cin_columns(session: Session, *, apply: bool = False) -> dict[str, int]:
    """Fill `asset_transfers` and `sales_tax` from each row's `raw_xml`."""
    rows = session.execute(select(ChangeInNavDaily)).scalars().all()
    scanned = changed = no_raw = nonzero_asset = nonzero_sales = 0
    for row in rows:
        scanned += 1
        if not row.raw_xml:
            no_raw += 1
            continue
        try:
            el = ET.fromstring(row.raw_xml.strip())
        except ET.ParseError:
            no_raw += 1
            continue
        at = _dec(el.get("assetTransfers"))
        st = _dec(el.get("salesTax"))
        if at:
            nonzero_asset += 1
        if st:
            nonzero_sales += 1
        if row.asset_transfers != at or row.sales_tax != st:
            changed += 1
            if apply:
                row.asset_transfers = at
                row.sales_tax = st
    if apply:
        session.flush()
    return {
        "scanned": scanned,
        "changed": changed,
        "without_raw_xml": no_raw,
        "nonzero_asset_transfers": nonzero_asset,
        "nonzero_sales_tax": nonzero_sales,
    }


def _cash_dw_base(session: Session) -> tuple[float, int]:
    """Σ Deposits/Withdrawals cash rows in CHF (NULL fx → `fx_rates` as-of)."""
    rows = session.execute(
        select(CashTransaction).where(CashTransaction.type == _DW_TYPE)
    ).scalars().all()
    total = 0.0
    for r in rows:
        if r.amount is None:
            continue
        if r.fx_rate_to_base is not None:
            total += float(r.amount) * float(r.fx_rate_to_base)
            continue
        ccy = (r.currency or "CHF").upper()
        d: date | None = r.settle_date or (r.event_datetime.date() if r.event_datetime else None)
        rate = 1.0
        if ccy != "CHF" and d is not None:
            rate = fx_lookup(session, [d], ccy, "CHF", allow_fallback=False)["rates"].get(d, 1.0)
        total += float(r.amount) * rate
    return total, len(rows)


def drop_csv_flow_rows(
    session: Session, *, apply: bool = False, tolerance: float = 0.002
) -> dict[str, Any]:
    """Delete the Plan-B CSV Deposit/Withdrawal cash rows (`csv_` ids).

    Flows now come from ChangeInNAV, so these rows only pollute cash-based
    reports. Guard: Σ CIN `depositsWithdrawals` / Σ cash D/W (CSV rows included,
    NULL fx resolved from `fx_rates`) must be within `tolerance` of 1.0,
    otherwise nothing is deleted. With no `csv_` rows left there is nothing to
    do (the ratio is then informational only), so re-runs are no-ops.
    """
    cin_total = float(
        session.execute(
            select(func.coalesce(func.sum(ChangeInNavDaily.deposits_withdrawals), 0))
        ).scalar() or 0
    )
    cash_total, cash_rows = _cash_dw_base(session)
    ratio = (cin_total / cash_total) if cash_total else None
    ids = session.execute(
        select(CashTransaction.transaction_id).where(
            CashTransaction.type == _DW_TYPE,
            CashTransaction.transaction_id.like("csv\\_%", escape="\\"),
        )
    ).scalars().all()
    ok = ratio is not None and abs(ratio - 1.0) <= tolerance
    deleted = 0
    if apply and ok and ids:
        deleted = session.execute(
            delete(CashTransaction).where(CashTransaction.transaction_id.in_(ids))
        ).rowcount or 0
    return {
        "cin_deposits_withdrawals_sum": cin_total,
        "cash_dw_sum": cash_total,
        "cash_dw_rows": cash_rows,
        "ratio_cin_over_cash": ratio,
        "ratio_ok": ok,
        "csv_rows": len(ids),
        "deleted": deleted,
    }


def drop_navless_rows(session: Session, *, apply: bool = False) -> dict[str, Any]:
    """Delete `nav_daily` rows with no NAV (cash-flow-only residue, B50)."""
    dates = session.execute(
        select(NavDaily.snapshot_date).where(NavDaily.nav_total.is_(None))
    ).scalars().all()
    deleted = 0
    if apply and dates:
        deleted = session.execute(
            delete(NavDaily).where(NavDaily.nav_total.is_(None))
        ).rowcount or 0
    return {"navless_rows": len(dates), "dates": [d.isoformat() for d in dates], "deleted": deleted}
