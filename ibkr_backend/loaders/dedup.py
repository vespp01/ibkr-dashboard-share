"""Remove duplicate rows left by overlapping CSV (Plan B) + Flex imports.

The `Extractos/` CSV/XML loader mints synthetic primary keys
(`csv_<sha1>`) while the Flex pipeline uses IBKR-supplied / Flex-synthetic
ids. When the same economic event arrives through both paths it lands as
two rows with different PKs — duplicating everything derived from
`trades` / `cash_transactions` / `transaction_fees` (commissions,
operation counts, dividend totals, FIFO P&L).

Safe rule: a `csv_*` row is a duplicate **only** when a non-`csv_` (Flex)
row shares its economic key. Those `csv_*` rows are removed; Flex is the
canonical copy (it carries fx rate + commission the CSV lacks).
`csv_*` rows with no Flex twin (history Flex doesn't cover) and genuine
repeated Flex fills (two non-`csv_` rows, same key) are **kept**.

Idempotent and dry-run by default. Mirrors `reconcile_external_cashflows`.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db.models import CashTransaction, TransactionFee, Trade

log = logging.getLogger(__name__)

_TICKER_RE = re.compile(r"^\s*([A-Za-z0-9.]+)\s*\(")


def _is_csv(pk_value: str) -> bool:
    return pk_value.startswith("csv_")


def _num(v) -> float | None:
    return float(v) if v is not None else None


def _ticker(desc: str | None) -> str | None:
    """Leading ticker in a cash-tx description (`DEMO(US...) Cash Dividend...`).

    Added to the cash-tx dedup key so two *different* securities that happen
    to share a settle date + amount can never collapse into one another;
    only the same security's csv/Flex copies pair up. Case-insensitive
    because csv and Flex render the description in different cases.
    """
    if not desc:
        return None
    m = _TICKER_RE.match(desc)
    return m.group(1).upper() if m else None


# (Model, pk attr, economic-key function). Keys mirror the float-normalised
# grouping used to audit the duplication, so the report is exact.
_SPECS = [
    (
        Trade, "trade_id",
        lambda r: (
            r.account_id, r.symbol, r.trade_date, r.buy_sell,
            _num(r.quantity), _num(r.trade_price), r.currency,
        ),
    ),
    (
        CashTransaction, "transaction_id",
        lambda r: (
            r.account_id, r.type, r.settle_date, _num(r.amount), r.currency,
            _ticker(r.description),
        ),
    ),
    (
        TransactionFee, "fee_id",
        lambda r: (
            r.account_id, r.symbol, r.report_date, _num(r.tax_amount), r.currency,
        ),
    ),
]


def dedup_csv_imports(session: Session, *, apply: bool = False) -> dict:
    """Audit (and optionally delete) CSV-import duplicates across tables.

    Returns a per-table report: ``total`` rows, ``removable_csv`` (csv rows
    with a Flex twin), ``orphan_csv_kept`` (csv rows with no twin), and
    ``genuine_dupe_groups`` (groups of ≥2 non-csv rows, left untouched).
    With ``apply=True`` the removable rows are deleted in the same session.
    """
    report: dict[str, dict] = {}
    for Model, pk, keyfn in _SPECS:
        rows = session.execute(select(Model)).scalars().all()
        groups: dict[tuple, list] = defaultdict(list)
        for r in rows:
            groups[keyfn(r)].append(r)

        removable: list[str] = []
        orphan_csv = 0
        genuine_groups = 0
        for members in groups.values():
            csv_rows = [m for m in members if _is_csv(getattr(m, pk))]
            flex_rows = [m for m in members if not _is_csv(getattr(m, pk))]
            if flex_rows and csv_rows:
                removable.extend(getattr(m, pk) for m in csv_rows)
            elif csv_rows and not flex_rows:
                orphan_csv += len(csv_rows)
            if len(flex_rows) > 1:
                genuine_groups += 1

        report[Model.__tablename__] = {
            "total": len(rows),
            "removable_csv": len(removable),
            "orphan_csv_kept": orphan_csv,
            "genuine_dupe_groups": genuine_groups,
        }

        if apply and removable:
            for i in range(0, len(removable), 500):
                session.execute(
                    delete(Model).where(getattr(Model, pk).in_(removable[i:i + 500]))
                )
            log.info("dedup %s: removed %d csv duplicates", Model.__tablename__, len(removable))

    report["_applied"] = apply
    return report


__all__ = ["dedup_csv_imports"]
