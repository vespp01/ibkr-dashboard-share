"""Account-level housekeeping: orphan pruning + base-currency backfill.

Both operations target the `accounts` dimension. They are intentionally
data-driven (no hardcoded account ids) so they stay correct as the schema and
the real account set evolve.
"""

from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..db.models import Account, NavDaily
from ..db.session import Base


def _account_referencing_tables() -> list:
    """Every mapped table (except `accounts`) that carries an `account_id`.

    Introspected from the metadata so a newly-added fact table is covered
    automatically — an account is only an orphan when *no* such table
    references it.
    """
    return [
        t
        for t in Base.metadata.tables.values()
        if t.name != Account.__tablename__ and "account_id" in t.c
    ]


def prune_orphan_accounts(session: Session, *, apply: bool = False) -> dict:
    """Delete accounts with no rows in any account-scoped fact table.

    These are test/bootstrap residue (e.g. ``U99``/``UNKNOWN``). Dry-run by
    default; pass ``apply=True`` to delete. Returns a report dict.
    """
    all_ids = set(session.execute(select(Account.account_id)).scalars())
    referenced: set[str] = set()
    for table in _account_referencing_tables():
        ids = session.execute(select(table.c.account_id).distinct()).scalars()
        referenced.update(i for i in ids if i)
    orphans = sorted(all_ids - referenced)
    if apply and orphans:
        session.execute(delete(Account).where(Account.account_id.in_(orphans)))
    return {
        "total": len(all_ids),
        "kept": sorted(all_ids & referenced),
        "orphans": orphans,
        "deleted": orphans if apply else [],
    }


def backfill_base_currency(session: Session) -> list[tuple[str, str]]:
    """Fill NULL ``accounts.base_currency`` from the account's latest NAV row.

    The base currency is an account-level constant. A statement whose
    ``AccountInformation``/``EquitySummary`` omits the currency (e.g. a
    weekend ``LastBusinessDay`` pull) must not leave the column NULL when
    ``nav_daily`` — sourced from ``EquitySummaryByReportDateInBase`` — already
    carries it. Returns the ``(account_id, currency)`` pairs repaired.
    """
    null_accounts = session.execute(
        select(Account.account_id).where(Account.base_currency.is_(None))
    ).scalars().all()
    fixed: list[tuple[str, str]] = []
    for acct in null_accounts:
        ccy = session.execute(
            select(NavDaily.base_currency)
            .where(NavDaily.account_id == acct, NavDaily.base_currency.is_not(None))
            .order_by(NavDaily.snapshot_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if ccy:
            session.execute(
                update(Account)
                .where(Account.account_id == acct)
                .values(base_currency=ccy)
            )
            fixed.append((acct, ccy))
    return fixed
