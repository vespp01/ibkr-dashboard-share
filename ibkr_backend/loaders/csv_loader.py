"""Loader for IBKR Activity Statement CSV files (Client Portal download).

Parses the multi-section CSV format where each row begins with:
    SectionName,RowType,...values...

Usage:
    python -m ibkr_backend.cli load-csv Extractos/statement.csv
"""

from __future__ import annotations

import csv
import hashlib
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ._asset_class import canon as _canon_asset_class
from ..db import session_scope
from ..db.models import (
    Account,
    CashReportRow,
    CashTransaction,
    CorporateAction,
    Instrument,
    NavDaily,
    PositionEod,
    Trade,
    TransactionFee,
)
from ._upsert import upsert_ignore, upsert_replace
from .pipeline import LoadResult

log = logging.getLogger(__name__)

# When the Header row has a different section name than its Data rows.
_HEADER_ALIASES: dict[str, str] = {
    "Change in NAV": "Change in Combined NAV",
}


# ── value coercion ─────────────────────────────────────────────────────────────

def _dec(v: str | None) -> Decimal | None:
    if not v:
        return None
    v = v.strip()
    if v in ("", "--", "N/A", "-"):
        return None
    try:
        return Decimal(v.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _date(v: str | None) -> date | None:
    if not v:
        return None
    v = v.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%B %d, %Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    # IBKR format: "2025-03-10, 12:00:01" → "2025-03-10 12:00:01"
    v = v.strip().replace(", ", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def _hash_id(*parts: Any) -> str:
    payload = "|".join(str(p or "") for p in parts).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:24]


def _synthetic_conid(symbol: str, currency: str, asset_class: str) -> int:
    h = int(_hash_id(symbol, currency, asset_class), 16) % (2**30)
    return -(h + 1)


def _parse_period(v: str) -> tuple[date | None, date | None]:
    if " - " not in v:
        return None, None
    lo, hi = v.split(" - ", 1)
    return _date(lo.strip()), _date(hi.strip())


def _country_from_description(desc: str | None) -> str | None:
    """Extract ISO-2 country from statement descriptions like '... - US Tax'."""
    if not desc:
        return None
    marker = desc.rfind(" - ")
    if marker <= 0:
        return None
    tail = desc[marker + 3 :].strip().upper()
    first = tail.split()[0] if tail else ""
    if len(first) == 2 and first.isalpha():
        return first
    return None


# ── CSV section parser ─────────────────────────────────────────────────────────

def _parse_sections(path: str | Path) -> dict[str, list[dict]]:
    """
    Read the activity statement CSV into:
        { section_name: [{col: val}, ...] }

    Only 'Data' rows are kept. Multiple Header rows per section are handled by
    updating the active column list on each Header encountered.
    """
    sections: dict[str, list[dict]] = {}
    current_header: dict[str, list[str]] = {}

    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or len(row) < 2:
                continue
            section = row[0].strip()
            row_type = row[1].strip()
            rest = row[2:]

            if row_type == "Header":
                cols = [c.strip() for c in rest]
                current_header[section] = cols
            elif row_type == "Data":
                # Check if this section's header came under an alias name.
                hdr_key = _HEADER_ALIASES.get(section, section)
                cols = current_header.get(hdr_key, current_header.get(section))
                if cols is None:
                    continue
                padded = (rest + [""] * len(cols))[: len(cols)]
                record = {c: padded[i].strip() for i, c in enumerate(cols)}
                sections.setdefault(section, []).append(record)
            # Skip: Total, SubTotal, Notes, etc.

    return sections


# ── metadata extraction ────────────────────────────────────────────────────────

def _extract_metadata(sections: dict) -> tuple[str, str, date | None, date | None]:
    """Return (account_id, base_currency, from_date, to_date)."""
    account_id = "UNKNOWN"
    base_currency = "USD"
    from_date = to_date = None

    for row in sections.get("Account Information", []):
        fn = row.get("Field Name", "")
        fv = row.get("Field Value", "")
        if fn == "Account":
            account_id = fv.split("(")[0].strip()
        elif fn == "Base Currency":
            base_currency = fv.strip()

    for row in sections.get("Statement", []):
        fn = row.get("Field Name", "")
        fv = row.get("Field Value", "")
        if fn == "Period":
            from_date, to_date = _parse_period(fv)

    return account_id, base_currency, from_date, to_date


# ── instrument lookup ──────────────────────────────────────────────────────────

def _build_instrument_map(rows: list[dict]) -> dict[tuple[str, str], int]:
    """{ (symbol, asset_class): conid } from Financial Instrument Information."""
    m: dict[tuple[str, str], int] = {}
    for r in rows:
        raw = r.get("Conid", "").strip()
        if not raw:
            continue
        try:
            conid = int(raw)
        except ValueError:
            continue
        if conid <= 0:
            continue
        symbol = r.get("Symbol", "").strip()
        asset_class = _canon_asset_class(r.get("Asset Category", "").strip()) or ""
        if symbol:
            m[(symbol, asset_class)] = conid
            m.setdefault((symbol, ""), conid)
    return m


def _get_conid(symbol: str, asset_class: str, inst_map: dict) -> int | None:
    return inst_map.get((symbol, asset_class)) or inst_map.get((symbol, ""))


def _ensure_instrument(session, conid: int, symbol: str, currency: str, asset_class: str) -> None:
    from sqlalchemy import select
    exists = session.execute(
        select(Instrument.conid).where(Instrument.conid == conid)
    ).scalar_one_or_none()
    if exists is None:
        session.add(Instrument(conid=conid, symbol=symbol, asset_class=asset_class, currency=currency))
        session.flush()


# ── section loaders ────────────────────────────────────────────────────────────

def _load_account(session, account_id: str, base_currency: str) -> LoadResult:
    n = upsert_replace(
        session, Account,
        [{"account_id": account_id, "base_currency": base_currency}],
        pk_cols=["account_id"],
    )
    return LoadResult(section="accounts", replaced=n)


def _load_instruments(session, rows: list[dict]) -> LoadResult:
    to_upsert = []
    for r in rows:
        raw = r.get("Conid", "").strip()
        if not raw:
            continue
        try:
            conid = int(raw)
        except ValueError:
            continue
        if conid <= 0:
            continue
        to_upsert.append({
            "conid": conid,
            "symbol": r.get("Symbol", "").strip(),
            "description": r.get("Description", "").strip() or None,
            "asset_class": _canon_asset_class(r.get("Asset Category", "").strip()),
            "isin": r.get("Security ID", "").strip() or None,
            "listing_exchange": r.get("Listing Exch", "").strip() or None,
            "multiplier": _dec(r.get("Multiplier")),
        })
    if not to_upsert:
        return LoadResult(section="instruments")
    n = upsert_replace(
        session, Instrument, to_upsert, pk_cols=["conid"],
        update_cols=["symbol", "description", "asset_class", "isin", "listing_exchange", "multiplier"],
    )
    return LoadResult(section="instruments", replaced=n)


def _load_positions(
    session, rows: list[dict], account_id: str, snapshot_date: date | None, inst_map: dict
) -> LoadResult:
    if not snapshot_date:
        return LoadResult(section="positions_eod")
    to_upsert = []
    for r in rows:
        if r.get("DataDiscriminator", "") != "Summary":
            continue
        symbol = r.get("Symbol", "").strip()
        asset_class = _canon_asset_class(r.get("Asset Category", "").strip()) or ""
        currency = r.get("Currency", "").strip()
        conid = _get_conid(symbol, asset_class, inst_map)
        if conid is None:
            conid = _synthetic_conid(symbol, currency, asset_class)
            log.debug("Position: synthetic conid %d for %s", conid, symbol)
        _ensure_instrument(session, conid, symbol, currency, asset_class)
        qty = _dec(r.get("Quantity"))
        if qty is None:
            continue
        to_upsert.append({
            "snapshot_date": snapshot_date,
            "account_id": account_id,
            "conid": conid,
            "symbol": symbol,
            "quantity": qty,
            "mark_price": _dec(r.get("Close Price")),
            "position_value": _dec(r.get("Value")),
            "cost_basis_money": _dec(r.get("Cost Basis")),
            "cost_basis_price": _dec(r.get("Cost Price")),
            "fifo_pnl_unrealized": _dec(r.get("Unrealized P/L")),
            "currency": currency or None,
            "multiplier": _dec(r.get("Mult")),
            "asset_class": asset_class or None,
        })
    if not to_upsert:
        return LoadResult(section="positions_eod")
    n = upsert_replace(session, PositionEod, to_upsert)
    return LoadResult(section="positions_eod", replaced=n)


def _load_trades(session, rows: list[dict], account_id: str, inst_map: dict) -> LoadResult:
    to_insert = []
    for r in rows:
        disc = r.get("DataDiscriminator", "").strip()
        if disc not in ("Order", ""):
            continue
        symbol = r.get("Symbol", "").strip()
        if not symbol:
            continue
        asset_class = _canon_asset_class(r.get("Asset Category", "").strip()) or ""
        currency = r.get("Currency", "").strip()
        acc = r.get("Account", account_id).strip() or account_id
        dt_str = r.get("Date/Time", "").strip()
        dt = _dt(dt_str)
        if not dt:
            continue
        qty = _dec(r.get("Quantity"))
        price = _dec(r.get("T. Price"))
        if qty is None:
            continue

        conid = _get_conid(symbol, asset_class, inst_map)
        if conid is None:
            conid = _synthetic_conid(symbol, currency, asset_class)
            log.debug("Trade: synthetic conid %d for %s", conid, symbol)
        _ensure_instrument(session, conid, symbol, currency, asset_class)

        proceeds = _dec(r.get("Proceeds")) or Decimal(0)
        commission = _dec(r.get("Comm/Fee")) or Decimal(0)
        trade_id = "csv_" + _hash_id(acc, symbol, dt_str, str(qty), str(price))

        to_insert.append({
            "trade_id": trade_id,
            "account_id": acc,
            "conid": conid,
            "symbol": symbol,
            "trade_date": dt.date(),
            "trade_datetime": dt,
            "buy_sell": "BUY" if qty > 0 else "SELL",
            "quantity": qty,
            "trade_price": price or Decimal(0),
            "proceeds": proceeds,
            "ib_commission": commission,
            "ib_commission_ccy": currency or None,
            "net_cash": proceeds + commission,
            "fifo_pnl_realized": _dec(r.get("Realized P/L")),
            "mtm_pnl": _dec(r.get("MTM P/L")),
            "open_close": r.get("Code", "").strip() or None,
            "currency": currency,
            "asset_class": asset_class or None,
            "multiplier": Decimal("1"),
        })
    if not to_insert:
        return LoadResult(section="trades")
    ins, sk = upsert_ignore(session, Trade, to_insert)
    return LoadResult(section="trades", inserted=ins, skipped=sk)


def _cash_tx_rows(raw_rows: list[dict], tx_type: str, account_id: str) -> list[dict]:
    out = []
    for r in raw_rows:
        currency = r.get("Currency", "").strip()
        acc = r.get("Account", account_id).strip() or account_id
        desc = r.get("Description", "").strip()
        amount = _dec(r.get("Amount"))
        if amount is None:
            continue
        # Dividends/Interest use 'Date'; Deposits use 'Settle Date'
        dt_str = r.get("Date", r.get("Settle Date", "")).strip()
        dt = _dt(dt_str)
        if not dt:
            continue
        tx_id = "csv_" + _hash_id(acc, tx_type, dt_str, str(amount), desc[:40])
        out.append({
            "transaction_id": tx_id,
            "account_id": acc,
            "type": tx_type,
            "amount": amount,
            "currency": currency,
            "event_datetime": dt,
            "settle_date": _date(dt_str),
            "description": desc or None,
            "country": _country_from_description(desc),
        })
    return out


def _load_cash_transactions(session, sections: dict, account_id: str) -> LoadResult:
    all_rows: list[dict] = []
    all_rows += _cash_tx_rows(sections.get("Dividends", []), "Dividends", account_id)
    all_rows += _cash_tx_rows(sections.get("Withholding Tax", []), "Withholding Tax", account_id)
    all_rows += _cash_tx_rows(sections.get("Interest", []), "Broker Interest Paid", account_id)
    all_rows += _cash_tx_rows(sections.get("Deposits & Withdrawals", []), "Deposits/Withdrawals", account_id)
    if not all_rows:
        return LoadResult(section="cash_transactions")
    ins, sk = upsert_ignore(session, CashTransaction, all_rows)
    return LoadResult(section="cash_transactions", inserted=ins, skipped=sk)


def _load_tx_fees(
    session, rows: list[dict], account_id: str, snapshot_date: date | None
) -> LoadResult:
    to_insert = []
    for r in rows:
        acc = r.get("Account", account_id).strip() or account_id
        symbol = r.get("Symbol", "").strip() or None
        desc = r.get("Description", "").strip() or None
        amount = _dec(r.get("Amount"))
        if amount is None:
            continue
        dt_str = r.get("Date/Time", "").strip()
        dt = _dt(dt_str)
        fee_id = "csv_" + _hash_id(acc, symbol or "", dt_str, str(amount), desc or "")
        to_insert.append({
            "fee_id": fee_id,
            "account_id": acc,
            "symbol": symbol,
            "event_datetime": dt,
            "report_date": snapshot_date,
            "tax_amount": amount,
            "tax_description": desc,
            "currency": r.get("Currency", "").strip() or None,
            "quantity": _dec(r.get("Quantity")),
        })
    if not to_insert:
        return LoadResult(section="transaction_fees")
    ins, sk = upsert_ignore(session, TransactionFee, to_insert, pk_cols=["fee_id"])
    return LoadResult(section="transaction_fees", inserted=ins, skipped=sk)


def _load_cash_report(
    session, rows: list[dict], account_id: str, snapshot_date: date | None
) -> LoadResult:
    if not snapshot_date:
        return LoadResult(section="cash_report")
    # Pivot rows (category × currency) into one row per currency.
    by_currency: dict[str, dict[str, Decimal]] = {}
    for r in rows:
        category = r.get("Currency Summary", "").strip()
        currency = r.get("Currency", "").strip()
        if not currency:
            continue
        if currency == "Base Currency Summary":
            currency = "BASE"
        total = _dec(r.get("Total"))
        if total is None:
            continue
        by_currency.setdefault(currency, {})[category] = total

    to_upsert = []
    for currency, cats in by_currency.items():
        dep = (cats.get("Deposits") or Decimal(0)) + (cats.get("Withdrawals") or Decimal(0))
        to_upsert.append({
            "snapshot_date": snapshot_date,
            "account_id": account_id,
            "currency": currency,
            "ending_cash": cats.get("Ending Cash"),
            "ending_settled_cash": cats.get("Ending Settled Cash"),
            "dividends": cats.get("Dividends"),
            "withholding_tax": cats.get("Withholding Tax"),
            "broker_interest": cats.get("Broker Interest Paid and Received"),
            "commissions": cats.get("Commissions"),
            "deposits_withdrawals": dep if dep else None,
        })
    if not to_upsert:
        return LoadResult(section="cash_report")
    n = upsert_replace(session, CashReportRow, to_upsert)
    return LoadResult(section="cash_report", replaced=n)


def _load_nav(
    session, sections: dict, account_id: str, base_currency: str, to_date: date | None
) -> LoadResult:
    if not to_date:
        return LoadResult(section="nav_daily")

    # Asset breakdown from "Net Asset Value" section
    nav_breakdown: dict[str, Decimal] = {}
    for r in sections.get("Net Asset Value", []):
        asset_class = r.get("Asset Class", "").strip()
        current_total = _dec(r.get("Current Total"))
        if asset_class and current_total is not None:
            nav_breakdown[asset_class.strip()] = current_total

    # Ending NAV and external cashflow from "Change in NAV" section
    ending_nav: Decimal | None = None
    external_cashflow: Decimal = Decimal(0)
    for r in sections.get("Change in NAV", []):
        fn = r.get("Field Name", "").strip()
        fv = _dec(r.get("Field Value"))
        if fn == "Ending Value":
            ending_nav = fv
        elif fn == "Deposits & Withdrawals" and fv is not None:
            external_cashflow += fv

    nav_total = ending_nav or nav_breakdown.get("Total")
    if nav_total is None:
        return LoadResult(section="nav_daily")

    rows = [{
        "snapshot_date": to_date,
        "account_id": account_id,
        "base_currency": base_currency,
        "nav_total": nav_total,
        "cash": nav_breakdown.get("Cash"),
        "stock": nav_breakdown.get("Stock"),
        "options": nav_breakdown.get("Options"),
        "bonds": nav_breakdown.get("Bond") or nav_breakdown.get("Bonds"),
        "funds": nav_breakdown.get("Fund") or nav_breakdown.get("Funds"),
        "dividends_receivable": nav_breakdown.get("Dividend Accruals"),
        "interest_receivable": nav_breakdown.get("Interest Accruals"),
        "external_cashflow": external_cashflow,
    }]
    upsert_replace(
        session, NavDaily, rows,
        update_cols=[
            "base_currency", "nav_total", "cash", "stock", "options",
            "bonds", "funds", "dividends_receivable", "interest_receivable",
            "external_cashflow",
        ],
    )
    return LoadResult(section="nav_daily", replaced=1)


def _load_corporate_actions(session, rows: list[dict], account_id: str) -> LoadResult:
    to_insert = []
    for r in rows:
        acc = r.get("Account", account_id).strip() or account_id
        dt_str = r.get("Date/Time", "").strip()
        dt = _dt(dt_str)
        if not dt:
            continue
        desc = r.get("Description", "").strip()
        ca_id = "csv_" + _hash_id(acc, dt_str, desc[:60])
        to_insert.append({
            "ca_id": ca_id,
            "account_id": acc,
            "description": desc or None,
            "event_datetime": dt,
            "quantity": _dec(r.get("Quantity")),
            "proceeds": _dec(r.get("Proceeds")),
            "value": _dec(r.get("Value")),
            "currency": r.get("Currency", "").strip() or None,
        })
    if not to_insert:
        return LoadResult(section="corporate_actions")
    ins, sk = upsert_ignore(session, CorporateAction, to_insert, pk_cols=["ca_id"])
    return LoadResult(section="corporate_actions", inserted=ins, skipped=sk)


# ── public API ─────────────────────────────────────────────────────────────────

def load_csv_file(path: str | Path) -> list[LoadResult]:
    """Parse and load an IBKR Activity Statement CSV file from disk."""
    path = Path(path)
    sections = _parse_sections(path)
    account_id, base_currency, from_date, to_date = _extract_metadata(sections)
    log.info("CSV load: account=%s  period=%s → %s", account_id, from_date, to_date)

    inst_rows = sections.get("Financial Instrument Information", [])
    inst_map = _build_instrument_map(inst_rows)
    log.info("CSV load: %d instruments found", len(inst_map) // 2)

    out: list[LoadResult] = []
    with session_scope() as session:
        # FK order: account → instruments → everything else
        out.append(_load_account(session, account_id, base_currency))
        out.append(_load_instruments(session, inst_rows))
        out.append(_load_positions(session, sections.get("Open Positions", []), account_id, to_date, inst_map))
        out.append(_load_trades(session, sections.get("Trades", []), account_id, inst_map))
        out.append(_load_cash_transactions(session, sections, account_id))
        out.append(_load_tx_fees(session, sections.get("Transaction Fees", []), account_id, to_date))
        out.append(_load_cash_report(session, sections.get("Cash Report", []), account_id, to_date))
        out.append(_load_nav(session, sections, account_id, base_currency, to_date))
        out.append(_load_corporate_actions(session, sections.get("Corporate Actions", []), account_id))

    return out
