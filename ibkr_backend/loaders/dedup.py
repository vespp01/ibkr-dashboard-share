"""One-time cleanup of the rows the retired CSV (Plan B) loader left behind.

The `Extractos/` CSV loader (retired 2026-10, D3) minted synthetic primary
keys (`csv_<sha1>`) while the Flex pipeline uses IBKR-supplied ids. Where both
paths covered the same period, every economic event landed twice, with an
exact twin only in the Flex copy (which carries `fxRateToBase`, order ids and
`raw_xml`). The doubling touched trades (realized P&L 2.27x in 2024), cash
transactions (dividends +14 %), fees, corporate actions and 31-Dec snapshots.

Two entry points, both idempotent and dry-run by default:

* `dedup_csv_imports`  - removes only `csv_*` rows that have a Flex twin.
* `purge_csv_residue`  - the full cleanup: twins plus the CSV-only residue
  (subtotal fee rows, CSV corporate actions, CSV positions/NAV/BASE cash rows,
  negative-conid instruments, Flex SUMMARY corporate-action duplicates). It
  runs a reconciliation first and refuses to delete when it fails.

Twin matching is deliberately wider than the old exact key:

* trades   - the CSV holds one row per ORDER, Flex one per FILL. Fills are
  summed per Flex order (or per day), compared on side + quantity, and option
  symbols are compared in OCC-normalised form; the date window is +/-1 day on
  the execution timestamp.
* cash     - key (settle date, currency, amount, ticker) with type FAMILIES
  ({Dividends, Payment In Lieu}, {Broker Interest Paid, Received}) because the
  CSV types a whole section with one label.
* fees     - (symbol, amount, currency) within 4 days of the CSV event; CSV
  subtotal rows (no symbol, no currency) are residue.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..analytics.options_parsing import enrich_option
from ..db.models import (
    CashReportRow,
    CashTransaction,
    ChangeInNavDaily,
    CorporateAction,
    FifoPerformanceUnderlying,
    Instrument,
    NavDaily,
    PositionEod,
    TransactionFee,
    Trade,
)
from ..db.session import Base

log = logging.getLogger(__name__)

_TICKER_RE = re.compile(r"^\s*([A-Za-z0-9.]+)\s*\(")

# Realized-P&L reconciliation tolerance (relative) with an absolute floor in
# base currency. Observed 2024 diff 0.0000 %, 2025 0.03 % (one FX rate).
RECON_REL_TOL = 0.005
RECON_ABS_FLOOR = 1.0
QTY_TOL = 1e-4


class PurgeRefused(RuntimeError):
    """The pre-delete reconciliation failed; nothing was deleted."""


def _is_csv(pk_value: str) -> bool:
    return pk_value.startswith("csv_")


def _num(v) -> float | None:
    return float(v) if v is not None else None


def _ticker(desc: str | None) -> str | None:
    """Leading ticker in a cash-tx description (`DEMO(US...) Cash Dividend...`).

    Part of the cash key so two different securities that share a settle date
    and amount never collapse into one another. Case-insensitive because the
    CSV and Flex render the description in different cases.
    """
    if not desc:
        return None
    m = _TICKER_RE.match(desc)
    return m.group(1).upper() if m else None


def _norm_symbol(symbol: str | None) -> str:
    """Compare key for a trade symbol; options become `UND|expiry|right|strike`.

    The CSV writes `ACME 21NOV25 22 C`, Flex writes the OCC form
    `ACME  251121C00022000`; both normalise to the same string.
    """
    sym = (symbol or "").strip()
    c = enrich_option(sym, None)
    if c.underlying and c.expiry and c.right and c.strike is not None:
        return f"{c.underlying}|{c.expiry.isoformat()}|{c.right}|{c.strike:g}"
    return sym.upper()


_CASH_FAMILY = {
    "Dividends": "DIV",
    "Payment In Lieu Of Dividends": "DIV",
    "Broker Interest Paid": "INT",
    "Broker Interest Received": "INT",
    "Bond Interest Paid": "INT",
    "Bond Interest Received": "INT",
}


def _family(tx_type: str | None) -> str:
    return _CASH_FAMILY.get(tx_type or "", tx_type or "")


def _day(dt: datetime | date | None) -> date | None:
    return dt.date() if isinstance(dt, datetime) else dt


# --------------------------------------------------------------- twin finders
def find_trade_twins(session: Session) -> tuple[dict[str, str], list[str]]:
    """Map each `csv_` trade id to a Flex twin group, plus the unmatched ids.

    A twin is a Flex order (or, without order ids, a Flex day) on the same
    normalised symbol and side whose summed fills equal the CSV quantity, with
    a volume-weighted price within 2 %. Each Flex group is used once.
    """
    rows = session.execute(select(Trade)).scalars().all()
    flex = [r for r in rows if not _is_csv(r.trade_id)]
    csv_rows = sorted(
        (r for r in rows if _is_csv(r.trade_id)),
        key=lambda r: (r.trade_datetime, r.trade_id),
    )

    # (symbol, side) -> {group key -> [fills]}
    groups: dict[tuple[str, str], dict[tuple, list[Trade]]] = defaultdict(dict)
    for f in flex:
        side = f.buy_sell or ("BUY" if f.quantity > 0 else "SELL")
        g = groups[(_norm_symbol(f.symbol), side)]
        if f.ib_order_id:
            g.setdefault(("order", f.ib_order_id), []).append(f)
        g.setdefault(("day", _day(f.trade_datetime)), []).append(f)

    used: set[tuple] = set()
    twins: dict[str, str] = {}
    unmatched: list[str] = []
    for r in csv_rows:
        side = r.buy_sell or ("BUY" if r.quantity > 0 else "SELL")
        sym = _norm_symbol(r.symbol)
        d = _day(r.trade_datetime)
        best = None
        for key, fills in groups.get((sym, side), {}).items():
            if (sym, side, key) in used:
                continue
            first = min(f.trade_datetime for f in fills)
            if abs((_day(first) - d).days) > 1:
                continue
            qty = sum(float(f.quantity) for f in fills)
            if abs(abs(qty) - abs(float(r.quantity))) > QTY_TOL:
                continue
            tot = sum(abs(float(f.quantity)) for f in fills)
            vwap = (
                sum(abs(float(f.quantity)) * float(f.trade_price) for f in fills) / tot
                if tot else 0.0
            )
            px = float(r.trade_price)
            if px and vwap and abs(vwap - px) / px > 0.02:
                continue
            # prefer order groups over day groups, then the closest timestamp
            rank = (0 if key[0] == "order" else 1, abs((first - r.trade_datetime).total_seconds()))
            if best is None or rank < best[0]:
                best = (rank, key)
        if best is None:
            unmatched.append(r.trade_id)
        else:
            used.add((sym, side, best[1]))
            twins[r.trade_id] = f"{best[1][0]}:{best[1][1]}"
    return twins, unmatched


def _cash_key(r: CashTransaction) -> tuple:
    return (
        r.settle_date, r.currency, round(float(r.amount), 4),
        _ticker(r.description), _family(r.type),
    )


def find_cash_twins(session: Session) -> tuple[dict[str, str], list[str]]:
    """Map each `csv_` cash row to a distinct Flex row with the same key."""
    rows = session.execute(select(CashTransaction)).scalars().all()
    flex_by_key: dict[tuple, list[str]] = defaultdict(list)
    for r in rows:
        if not _is_csv(r.transaction_id):
            flex_by_key[_cash_key(r)].append(r.transaction_id)
    twins: dict[str, str] = {}
    unmatched: list[str] = []
    for r in sorted((x for x in rows if _is_csv(x.transaction_id)), key=lambda x: x.transaction_id):
        pool = flex_by_key.get(_cash_key(r))
        if pool:
            twins[r.transaction_id] = pool.pop(0)
        else:
            unmatched.append(r.transaction_id)
    return twins, unmatched


def find_fee_twins(session: Session) -> tuple[dict[str, str], list[str], list[str]]:
    """Return (twins, subtotal rows, unmatched) for CSV fee rows.

    Subtotals are CSV rows with neither symbol nor currency (the yearly totals
    the CSV section ends with). A real CSV row is a twin of a Flex fee with the
    same symbol, amount and currency within 4 days of its event time. The match
    is not exclusive: Flex used to collapse equal-amount fees into one row
    (B35), and the CSV rows it swallowed are still duplicates of real fees.
    """
    rows = session.execute(select(TransactionFee)).scalars().all()
    flex = [r for r in rows if not _is_csv(r.fee_id)]
    twins: dict[str, str] = {}
    subtotals: list[str] = []
    unmatched: list[str] = []
    for r in rows:
        if not _is_csv(r.fee_id):
            continue
        if not r.symbol and not r.currency:
            subtotals.append(r.fee_id)
            continue
        ev = _day(r.event_datetime) or r.report_date
        best = None
        for f in flex:
            if (f.symbol or "").upper() != (r.symbol or "").upper() or f.currency != r.currency:
                continue
            if abs(float(f.tax_amount) - float(r.tax_amount)) > 1e-4:
                continue
            if f.report_date is None or ev is None:
                continue
            gap = abs((f.report_date - ev).days)
            if gap <= 4 and (best is None or gap < best[0]):
                best = (gap, f.fee_id)
        if best:
            twins[r.fee_id] = best[1]
        else:
            unmatched.append(r.fee_id)
    return twins, subtotals, unmatched


# ------------------------------------------------------- reconciliation check
def realized_reconciliation(session: Session) -> dict:
    """Per year: sum of Flex-only trade realized P&L vs the FIFO summary.

    Both sides are in base currency and restricted to instruments that have a
    Flex trade (the FIFO summary also carries bare-currency FX conids that no
    trade row represents). The year is the trade / snapshot year.
    """
    not_csv = ~Trade.trade_id.like("csv\\_%", escape="\\")
    t_year = func.strftime("%Y", Trade.trade_date)
    trades = dict(session.execute(
        select(t_year, func.sum(Trade.fifo_pnl_realized * Trade.fx_rate_to_base))
        .where(not_csv)
        .group_by(t_year)
    ).all())
    f_year = func.strftime("%Y", FifoPerformanceUnderlying.snapshot_date)
    fifo = dict(session.execute(
        select(f_year, func.sum(FifoPerformanceUnderlying.total_realized_pnl))
        .where(FifoPerformanceUnderlying.conid.in_(select(Trade.conid).where(not_csv)))
        .group_by(f_year)
    ).all())
    csv_only = dict(session.execute(
        select(t_year, func.sum(Trade.fifo_pnl_realized))
        .where(Trade.trade_id.like("csv\\_%", escape="\\"))
        .group_by(t_year)
    ).all())
    out: dict[str, dict] = {}
    ok = True
    for year in sorted(set(trades) | set(fifo)):
        t = float(trades.get(year) or 0.0)
        f = float(fifo.get(year) or 0.0)
        good = abs(t - f) <= max(RECON_REL_TOL * abs(f), RECON_ABS_FLOOR)
        ok = ok and good
        out[year] = {
            "flex_trades_realized": round(t, 4),
            "fifo_summary_realized": round(f, 4),
            "ratio": round(t / f, 5) if f else None,
            "csv_realized_in_trades_native": round(float(csv_only.get(year) or 0.0), 4),
            "ok": good,
        }
    return {"ok": ok, "years": out}


# ------------------------------------------------------------------ the plan
@dataclass
class PurgePlan:
    """What `purge_csv_residue` would delete, and why it may be refused."""

    ids: dict[str, list] = field(default_factory=dict)
    kept: dict[str, int] = field(default_factory=dict)
    checks: dict = field(default_factory=dict)
    review: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.ids.items()}


def plan_csv_purge(session: Session) -> PurgePlan:
    """Compute the full CSV-residue purge without writing anything."""
    plan = PurgePlan()
    plan.checks["realized_reconciliation"] = realized_reconciliation(session)

    trade_twins, trade_orphans = find_trade_twins(session)
    plan.ids["trades"] = sorted(trade_twins)
    plan.kept["trades_csv_without_twin"] = len(trade_orphans)
    for tid in trade_orphans:
        t = session.get(Trade, tid)
        plan.review.append(
            f"CSV trade without Flex twin kept: conid={t.conid} {t.symbol} "
            f"{t.trade_datetime} qty={t.quantity}"
        )

    cash_twins, cash_orphans = find_cash_twins(session)
    plan.ids["cash_transactions"] = sorted(cash_twins)
    plan.kept["cash_csv_without_twin"] = len(cash_orphans)  # the 10 D/W rows

    fee_twins, fee_subtotals, fee_orphans = find_fee_twins(session)
    plan.ids["transaction_fees"] = sorted(set(fee_twins) | set(fee_subtotals))
    plan.kept["fees_csv_without_twin"] = len(fee_orphans)
    for fid in fee_orphans:
        f = session.get(TransactionFee, fid)
        plan.review.append(
            f"CSV fee without Flex twin kept: {f.symbol} {f.event_datetime} {f.tax_amount}"
        )

    # Corporate actions: every csv_ row, and the Flex SUMMARY roll-ups
    # (`syn_` ids; the DETAIL row carries the real transactionID).
    plan.ids["corporate_actions"] = sorted(
        c.ca_id for c in session.execute(select(CorporateAction)).scalars()
        if _is_csv(c.ca_id) or (c.raw_xml and 'levelOfDetail="SUMMARY"' in c.raw_xml)
    )

    # Positions: the CSV never stores raw_xml; Flex always does.
    plan.ids["positions_eod"] = [
        (p.snapshot_date, p.account_id, p.conid)
        for p in session.execute(
            select(PositionEod).where(PositionEod.raw_xml.is_(None))
        ).scalars()
    ]
    plan.ids["cash_report"] = [
        (c.snapshot_date, c.account_id, c.currency)
        for c in session.execute(
            select(CashReportRow).where(CashReportRow.currency == "BASE")
        ).scalars()
    ]
    # CSV NAV row on a weekend date: no Flex change-in-NAV row, a total set
    # (the NAV-less 2023-11-18 row belongs to the performance cleanup).
    cin_dates = set(session.execute(select(ChangeInNavDaily.snapshot_date)).scalars())
    plan.ids["nav_daily"] = [
        (n.snapshot_date, n.account_id)
        for n in session.execute(
            select(NavDaily).where(NavDaily.nav_total.is_not(None))
        ).scalars()
        if n.snapshot_date.weekday() >= 5 and n.snapshot_date not in cin_dates
    ]

    # Negative-conid instruments left unreferenced once the rows above go.
    plan.ids["instruments"] = _unreferenced_negative_conids(session, plan)
    return plan


def _unreferenced_negative_conids(session: Session, plan: PurgePlan) -> list[int]:
    neg = set(session.execute(select(Instrument.conid).where(Instrument.conid < 0)).scalars())
    if not neg:
        return []
    deleting_trades = set(plan.ids.get("trades", []))
    deleting_pos = set(plan.ids.get("positions_eod", []))
    referenced: set[int] = set()
    for table in Base.metadata.tables.values():
        if table.name == Instrument.__tablename__ or "conid" not in table.c:
            continue
        if table.name == "trades":
            q = select(table.c.trade_id, table.c.conid).where(table.c.conid < 0)
            referenced.update(c for tid, c in session.execute(q) if tid not in deleting_trades)
        elif table.name == "positions_eod":
            q = select(table.c.snapshot_date, table.c.account_id, table.c.conid).where(table.c.conid < 0)
            referenced.update(c for d, a, c in session.execute(q) if (d, a, c) not in deleting_pos)
        else:
            referenced.update(session.execute(select(table.c.conid).where(table.c.conid < 0)).scalars())
    return sorted(neg - referenced)


def purge_csv_residue(session: Session, *, apply: bool = False) -> dict:
    """Plan (and with `apply=True` execute) the CSV-residue purge.

    Refuses with `PurgeRefused` when the realized-P&L reconciliation fails.
    Returns `{applied, counts, kept, checks, review}`. Deletes run in one
    session, children before instruments.
    """
    plan = plan_csv_purge(session)
    recon = plan.checks["realized_reconciliation"]
    if apply and not recon["ok"]:
        raise PurgeRefused(
            f"Flex-only realized P&L does not match the FIFO summary: {recon['years']}"
        )
    if apply:
        _chunked_delete(session, Trade, Trade.trade_id, plan.ids["trades"])
        _chunked_delete(
            session, CashTransaction, CashTransaction.transaction_id, plan.ids["cash_transactions"]
        )
        _chunked_delete(session, TransactionFee, TransactionFee.fee_id, plan.ids["transaction_fees"])
        _chunked_delete(session, CorporateAction, CorporateAction.ca_id, plan.ids["corporate_actions"])
        for d, a, c in plan.ids["positions_eod"]:
            session.execute(delete(PositionEod).where(
                PositionEod.snapshot_date == d, PositionEod.account_id == a,
                PositionEod.conid == c))
        for d, a, c in plan.ids["cash_report"]:
            session.execute(delete(CashReportRow).where(
                CashReportRow.snapshot_date == d, CashReportRow.account_id == a,
                CashReportRow.currency == c))
        for d, a in plan.ids["nav_daily"]:
            session.execute(delete(NavDaily).where(
                NavDaily.snapshot_date == d, NavDaily.account_id == a))
        _chunked_delete(session, Instrument, Instrument.conid, plan.ids["instruments"])
        session.flush()
        log.info("purge-csv-residue applied: %s", plan.counts())
    return {
        "applied": apply,
        "counts": plan.counts(),
        "kept": plan.kept,
        "checks": plan.checks,
        "review": plan.review,
    }


def _chunked_delete(session: Session, model, col, ids: list) -> None:
    for i in range(0, len(ids), 500):
        session.execute(delete(model).where(col.in_(ids[i:i + 500])))


# ----------------------------------------------------- fee id re-key (B35)
def rekey_fee_ids(session: Session, *, apply: bool = False) -> dict:
    """Move stored Flex fee rows to the current `fee_id_for` formula.

    The id formula gained conid, quantity and an occurrence counter, so rows
    stored under the old formula would be re-inserted by the next pull of the
    same days. The new id is computed from the stored columns only (no network,
    no raw_xml needed): rows of one (account, date, amount, description, conid,
    quantity) group are numbered in old-id order. Idempotent: rows already on
    the new id are counted as `unchanged`. `csv_` rows are not touched.

    Fees the old formula collapsed (two equal fills stored as one row) cannot
    be recovered here; the next pull covering those days restores them.
    """
    from .pipeline import fee_id_for

    rows = [
        r for r in session.execute(select(TransactionFee)).scalars()
        if not _is_csv(r.fee_id)
    ]
    groups: dict[str, list[TransactionFee]] = defaultdict(list)
    for r in rows:
        groups[fee_id_for(r)].append(r)
    mapping: dict[str, str] = {}
    for base, members in groups.items():
        # Valid ids for this group: occurrence 0 (= base), 1, 2, ... A row that
        # already holds one keeps it, so a re-run is a no-op and never swaps
        # ids between rows (which would collide on the primary key).
        valid = [base] + [fee_id_for(members[0], n) for n in range(1, len(members))]
        taken = {m.fee_id for m in members if m.fee_id in valid}
        free = [v for v in valid if v not in taken]
        for r in sorted((m for m in members if m.fee_id not in taken), key=lambda m: m.fee_id):
            mapping[r.fee_id] = free.pop(0)
    existing = {r.fee_id for r in rows}
    clashes = {old for old, new in mapping.items() if new in existing and new not in mapping}
    for old in clashes:
        mapping.pop(old)
    if apply:
        # Two phases through temporary ids, so no UPDATE can hit a primary key
        # another row of this run still holds.
        fees = TransactionFee.__table__
        temp = {old: f"rekeytmp_{i}" for i, old in enumerate(mapping)}
        for old, tmp in temp.items():
            session.execute(fees.update().where(fees.c.fee_id == old).values(fee_id=tmp))
        for old, new in mapping.items():
            session.execute(fees.update().where(fees.c.fee_id == temp[old]).values(fee_id=new))
        session.flush()
    return {
        "applied": apply,
        "flex_fee_rows": len(rows),
        "rekeyed": len(mapping),
        "unchanged": len(rows) - len(mapping) - len(clashes),
        "clashes_skipped": len(clashes),
    }


# ------------------------------------------------- twin-only dedup (original)
# Genuine-duplicate groups (>=2 non-csv rows) still use the old exact key so
# the report keeps its meaning.
_GENUINE_KEYS = [
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
    ``genuine_dupe_groups`` (groups of >=2 non-csv rows, left untouched).
    With ``apply=True`` the removable rows are deleted in the same session.
    Subtotal fee rows are not twins: they count as kept here and are removed
    by `purge_csv_residue`.
    """
    trade_twins, trade_orphans = find_trade_twins(session)
    cash_twins, cash_orphans = find_cash_twins(session)
    fee_twins, subtotals, fee_orphans = find_fee_twins(session)
    removable = {
        "trades": sorted(trade_twins),
        "cash_transactions": sorted(cash_twins),
        "transaction_fees": sorted(fee_twins),
    }
    orphans = {
        "trades": len(trade_orphans),
        "cash_transactions": len(cash_orphans),
        "transaction_fees": len(fee_orphans) + len(subtotals),
    }
    report: dict[str, dict] = {}
    for Model, pk, keyfn in _GENUINE_KEYS:
        rows = session.execute(select(Model)).scalars().all()
        groups: dict[tuple, int] = defaultdict(int)
        for r in rows:
            if not _is_csv(getattr(r, pk)):
                groups[keyfn(r)] += 1
        name = Model.__tablename__
        report[name] = {
            "total": len(rows),
            "removable_csv": len(removable[name]),
            "orphan_csv_kept": orphans[name],
            "genuine_dupe_groups": sum(1 for n in groups.values() if n > 1),
        }
        if apply and removable[name]:
            _chunked_delete(session, Model, getattr(Model, pk), removable[name])
            log.info("dedup %s: removed %d csv duplicates", name, len(removable[name]))

    report["_applied"] = apply
    return report


__all__ = [
    "PurgePlan",
    "PurgeRefused",
    "dedup_csv_imports",
    "find_cash_twins",
    "find_fee_twins",
    "find_trade_twins",
    "plan_csv_purge",
    "purge_csv_residue",
    "realized_reconciliation",
    "rekey_fee_ids",
]
