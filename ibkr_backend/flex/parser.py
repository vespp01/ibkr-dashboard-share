"""XML parser for Activity Flex Query responses.

Uses pure stdlib `xml.etree.ElementTree` so the parser keeps working when IBKR
adds new attributes (we read by name, ignore unknown). This is more robust in
practice than schema-strict parsers like ibflex when IBKR ships changes.

Returns plain dataclasses keyed by IBKR-issued IDs so the loaders can run
idempotent UPSERTs.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET  # Element types + tostring()
from defusedxml.ElementTree import fromstring as _safe_fromstring  # parsing entry only
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


# ---------------------------------------------------------------- type coercion
def _dec(v: str | None) -> Decimal | None:
    if v is None or v == "" or v == "--":
        return None
    try:
        return Decimal(v)
    except (InvalidOperation, ValueError):
        return None


def _first_dec(el: ET.Element, *names: str) -> Decimal | None:
    """First attribute in `names` that parses to a Decimal, zero included.

    `_dec(a) or _dec(b)` treats a legitimate 0 as "missing" and falls through
    to the next name (or None). Use this wherever an attribute can alias
    another (B37).
    """
    for name in names:
        value = _dec(el.get(name))
        if value is not None:
            return value
    return None


def _int(v: str | None) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _str(v: str | None) -> str | None:
    if v is None:
        return None
    s = v.strip()
    return s or None


def _date(v: str | None) -> date | None:
    """Accept yyyymmdd or yyyy-mm-dd; return python date."""
    if not v:
        return None
    v = v.strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _dt(v: str | None) -> datetime | None:
    """Accept yyyymmdd, yyyymmdd;HHmmss, yyyy-mm-dd HH:MM:SS, ISO."""
    if not v:
        return None
    v = v.strip().replace(";", " ").replace("T", " ")
    for fmt in (
        "%Y%m%d %H%M%S",
        "%Y%m%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H%M%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------- result types
@dataclass(slots=True)
class AccountInfo:
    account_id: str
    base_currency: str | None = None
    alias: str | None = None
    account_type: str | None = None


@dataclass(slots=True)
class Instrument:
    conid: int
    symbol: str
    description: str | None = None
    asset_class: str | None = None
    sub_category: str | None = None
    isin: str | None = None
    cusip: str | None = None
    figi: str | None = None
    listing_exchange: str | None = None
    issuer: str | None = None
    issuer_country: str | None = None
    currency: str | None = None
    multiplier: Decimal | None = None
    sector: str | None = None
    industry: str | None = None


@dataclass(slots=True)
class FxRate:
    rate_date: date
    from_ccy: str
    to_ccy: str
    rate: Decimal


@dataclass(slots=True)
class Trade:
    trade_id: str
    account_id: str
    conid: int
    symbol: str
    trade_datetime: datetime
    trade_date: date
    settle_date: date | None
    buy_sell: str | None
    quantity: Decimal
    trade_price: Decimal
    proceeds: Decimal
    taxes: Decimal | None
    ib_commission: Decimal | None
    ib_commission_ccy: str | None
    net_cash: Decimal
    fifo_pnl_realized: Decimal | None
    mtm_pnl: Decimal | None
    open_close: str | None
    currency: str
    fx_rate_to_base: Decimal | None
    transaction_type: str | None
    exchange: str | None
    notes: str | None
    asset_class: str | None
    multiplier: Decimal | None
    ib_order_id: str | None
    raw: str = ""


@dataclass(slots=True)
class CashTransaction:
    transaction_id: str
    account_id: str
    conid: int | None
    symbol: str | None
    type: str
    amount: Decimal
    currency: str
    fx_rate_to_base: Decimal | None
    event_datetime: datetime
    settle_date: date | None
    report_date: date | None
    description: str | None
    country: str | None
    related_trade_id: str | None
    code: str | None
    asset_class: str | None
    raw: str = ""


@dataclass(slots=True)
class OpenPosition:
    snapshot_date: date
    account_id: str
    conid: int
    symbol: str
    quantity: Decimal
    mark_price: Decimal | None
    position_value: Decimal | None
    cost_basis_money: Decimal | None
    cost_basis_price: Decimal | None
    fifo_pnl_unrealized: Decimal | None
    pct_of_nav: Decimal | None
    currency: str
    fx_rate_to_base: Decimal | None
    multiplier: Decimal | None
    asset_class: str | None
    holding_period_dt: datetime | None
    open_dt: datetime | None
    raw: str = ""


@dataclass(slots=True)
class CorporateAction:
    ca_id: str  # synthesized from (conid|datetime|type|amount) when no native ID
    account_id: str
    conid: int | None
    symbol: str | None
    type: str | None
    description: str | None
    event_datetime: datetime
    quantity: Decimal | None
    proceeds: Decimal | None
    value: Decimal | None
    currency: str | None
    raw: str = ""


@dataclass(slots=True)
class CashReportRow:
    snapshot_date: date
    account_id: str
    currency: str
    ending_cash: Decimal | None
    ending_settled_cash: Decimal | None
    dividends: Decimal | None
    withholding_tax: Decimal | None
    broker_interest: Decimal | None
    commissions: Decimal | None
    fees_receivables: Decimal | None
    deposits_withdrawals: Decimal | None


@dataclass(slots=True)
class NavRow:
    snapshot_date: date
    account_id: str
    base_currency: str
    nav_total: Decimal | None
    cash: Decimal | None
    stock: Decimal | None
    options: Decimal | None
    bonds: Decimal | None
    funds: Decimal | None
    dividends_receivable: Decimal | None
    interest_receivable: Decimal | None
    accrued_dividends: Decimal | None
    realized_pnl: Decimal | None
    unrealized_pnl: Decimal | None


@dataclass(slots=True)
class DividendAccrual:
    account_id: str
    conid: int | None
    symbol: str | None
    ex_date: date | None
    pay_date: date | None
    quantity: Decimal | None
    gross_rate: Decimal | None
    gross_amount: Decimal | None
    net_amount: Decimal | None
    tax: Decimal | None
    fee: Decimal | None
    currency: str | None
    code: str | None


@dataclass(slots=True)
class InterestAccrual:
    account_id: str
    accrual_date: date
    currency: str
    interest_type: str | None
    interest: Decimal | None
    ending_balance: Decimal | None
    raw: str = ""


@dataclass(slots=True)
class TransactionFee:
    account_id: str
    trade_id: str | None
    symbol: str | None
    conid: int | None
    event_datetime: datetime | None
    report_date: date | None
    quantity: Decimal | None
    tax_amount: Decimal
    tax_description: str | None
    currency: str | None
    raw: str = ""


@dataclass(slots=True)
class ChangeInNavRow:
    snapshot_date: date
    account_id: str
    currency: str
    starting_value: Decimal | None
    ending_value: Decimal | None
    mtm: Decimal | None
    realized: Decimal | None
    change_in_unrealized: Decimal | None
    dividends: Decimal | None
    withholding_tax: Decimal | None
    change_in_dividend_accruals: Decimal | None
    interest: Decimal | None
    change_in_interest_accruals: Decimal | None
    commissions: Decimal | None
    broker_fees: Decimal | None
    other_fees: Decimal | None
    transaction_tax: Decimal | None
    deposits_withdrawals: Decimal | None
    fx_translation: Decimal | None
    twr: Decimal | None
    raw: str = ""
    asset_transfers: Decimal | None = None
    sales_tax: Decimal | None = None


@dataclass(slots=True)
class FifoPerformanceRow:
    snapshot_date: date
    account_id: str
    conid: int
    symbol: str | None
    asset_class: str | None
    cost_adj: Decimal | None
    realized_st_profit: Decimal | None
    realized_st_loss: Decimal | None
    realized_lt_profit: Decimal | None
    realized_lt_loss: Decimal | None
    total_realized_pnl: Decimal | None
    unrealized_profit: Decimal | None
    unrealized_loss: Decimal | None
    unrealized_st_profit: Decimal | None
    unrealized_st_loss: Decimal | None
    unrealized_lt_profit: Decimal | None
    unrealized_lt_loss: Decimal | None
    total_unrealized_pnl: Decimal | None
    total_fifo_pnl: Decimal | None
    transferred_pnl: Decimal | None
    raw: str = ""


@dataclass(slots=True)
class MtmPerformanceRow:
    snapshot_date: date
    account_id: str
    conid: int
    symbol: str | None
    asset_class: str | None
    prev_close_quantity: Decimal | None
    prev_close_price: Decimal | None
    close_quantity: Decimal | None
    close_price: Decimal | None
    transaction_mtm: Decimal | None
    prior_open_mtm: Decimal | None
    commissions: Decimal | None
    other: Decimal | None
    other_with_accruals: Decimal | None
    total: Decimal | None
    total_with_accruals: Decimal | None
    raw: str = ""


@dataclass(slots=True)
class TradeConfirmRow:
    """Per-fill execution row from Trade Confirmation Flex (TCF)."""

    exec_id: str
    account_id: str
    conid: int
    symbol: str | None
    asset_class: str | None
    trade_id: str | None
    order_id: str | None
    brokerage_order_id: str | None
    ext_exec_id: str | None
    block_id: str | None
    order_time: datetime | None
    execution_time: datetime
    trade_date: date
    settle_date: date | None
    report_date: date | None
    buy_sell: str | None
    quantity: Decimal
    price: Decimal
    amount: Decimal | None
    proceeds: Decimal | None
    net_cash: Decimal | None
    net_cash_with_billable: Decimal | None
    commission: Decimal | None
    broker_execution_commission: Decimal | None
    broker_clearing_commission: Decimal | None
    third_party_execution_commission: Decimal | None
    third_party_clearing_commission: Decimal | None
    third_party_regulatory_commission: Decimal | None
    other_commission: Decimal | None
    commission_currency: str | None
    tax: Decimal | None
    sales_tax: Decimal | None
    other_tax: Decimal | None
    trade_charge: Decimal | None
    currency: str
    multiplier: Decimal | None
    exchange: str | None
    transaction_type: str | None
    order_type: str | None
    open_close: str | None
    is_api_order: str | None
    trader_id: str | None
    order_reference: str | None
    orig_trade_id: str | None
    orig_trade_price: Decimal | None
    orig_trade_date: date | None
    raw: str = ""


@dataclass(slots=True)
class OptionEaeRow:
    eae_id: str
    account_id: str
    conid: int | None
    symbol: str | None
    underlying_conid: int | None
    underlying_symbol: str | None
    transaction_type: str | None
    event_date: date | None
    quantity: Decimal | None
    multiplier: Decimal | None
    trade_price: Decimal | None
    proceeds: Decimal | None
    commission: Decimal | None
    realized_pnl: Decimal | None
    currency: str | None
    raw: str = ""


@dataclass(slots=True)
class TransferRow:
    transfer_id: str
    account_id: str
    conid: int | None
    symbol: str | None
    direction: str | None
    transfer_type: str | None
    event_date: date | None
    quantity: Decimal | None
    position_value: Decimal | None
    cash_value: Decimal | None
    cost_basis: Decimal | None
    delivering_broker: str | None
    currency: str | None
    raw: str = ""


@dataclass(slots=True)
class ParsedStatement:
    account: AccountInfo
    from_date: date | None
    to_date: date | None
    when_generated: datetime | None
    instruments: list[Instrument] = field(default_factory=list)
    fx_rates: list[FxRate] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    cash_transactions: list[CashTransaction] = field(default_factory=list)
    open_positions: list[OpenPosition] = field(default_factory=list)
    corporate_actions: list[CorporateAction] = field(default_factory=list)
    cash_report: list[CashReportRow] = field(default_factory=list)
    nav_rows: list[NavRow] = field(default_factory=list)
    dividend_accruals: list[DividendAccrual] = field(default_factory=list)
    interest_accruals: list[InterestAccrual] = field(default_factory=list)
    transaction_fees: list[TransactionFee] = field(default_factory=list)
    change_in_nav: list[ChangeInNavRow] = field(default_factory=list)
    fifo_performance: list[FifoPerformanceRow] = field(default_factory=list)
    mtm_performance: list[MtmPerformanceRow] = field(default_factory=list)
    trade_confirms: list[TradeConfirmRow] = field(default_factory=list)
    option_eae: list[OptionEaeRow] = field(default_factory=list)
    transfers: list[TransferRow] = field(default_factory=list)


# --------------------------------------------------------------- public entry
def parse_flex_xml(xml_bytes: bytes | str) -> list[ParsedStatement]:
    """Parse an Activity Flex XML response and return one ParsedStatement per
    `<FlexStatement>` element. A single Flex Query may contain multiple
    statements when run against linked accounts.
    """
    if isinstance(xml_bytes, str):
        xml_bytes = xml_bytes.encode("utf-8")
    root = _safe_fromstring(xml_bytes)  # defusedxml — blocks DTD expansion

    if root.tag == "FlexStatementResponse":
        # Defensive: this should be handled by the client, but if it slipped
        # through, surface a helpful error.
        code = root.findtext(".//ErrorCode")
        msg = root.findtext(".//ErrorMessage") or "Flex returned a status envelope"
        raise ValueError(f"Flex {code}: {msg}")

    if root.tag != "FlexQueryResponse":
        raise ValueError(f"Unexpected root element: {root.tag}")

    return [_parse_statement(stmt) for stmt in root.iter("FlexStatement")]


# --------------------------------------------------------------- statement
def _parse_statement(stmt: ET.Element) -> ParsedStatement:
    account_id = stmt.get("accountId") or ""
    info_el = stmt.find(".//AccountInformation")
    info_alias = info_el.get("acctAlias") if info_el is not None else None
    account = AccountInfo(
        account_id=account_id,
        alias=stmt.get("acctAlias") or info_alias,
        base_currency=_find_base_currency(stmt),
        account_type=stmt.get("accountType") or _find_account_type(stmt),
    )
    parsed = ParsedStatement(
        account=account,
        from_date=_date(stmt.get("fromDate")),
        to_date=_date(stmt.get("toDate")),
        when_generated=_dt(stmt.get("whenGenerated")),
    )

    parsed.instruments = list(_parse_instruments(stmt))
    parsed.fx_rates = list(_parse_conversion_rates(stmt))
    parsed.trades = list(_parse_trades(stmt, account_id))
    parsed.cash_transactions = list(_parse_cash_transactions(stmt, account_id))
    parsed.open_positions = list(_parse_open_positions(stmt, account_id, parsed.to_date))
    parsed.corporate_actions = list(_parse_corporate_actions(stmt, account_id))
    parsed.cash_report = list(_parse_cash_report(stmt, account_id, parsed.to_date))
    parsed.nav_rows = list(
        _parse_nav(stmt, account_id, parsed.to_date or parsed.from_date)
    )
    parsed.dividend_accruals = list(_parse_dividend_accruals(stmt, account_id))
    parsed.interest_accruals = list(_parse_interest_accruals(stmt, account_id))
    parsed.transaction_fees = list(_parse_transaction_fees(stmt, account_id))
    parsed.change_in_nav = list(_parse_change_in_nav(stmt, account_id))
    parsed.fifo_performance = list(_parse_fifo_performance(stmt, account_id))
    parsed.mtm_performance = list(_parse_mtm_performance(stmt, account_id))
    parsed.trade_confirms = list(_parse_trade_confirms(stmt, account_id))
    parsed.option_eae = list(_parse_option_eae(stmt, account_id))
    parsed.transfers = list(_parse_transfers(stmt, account_id))

    return parsed


# --------------------------------------------------------- helper extractors
def _find_base_currency(stmt: ET.Element) -> str | None:
    for path in ("AccountInformation", "EquitySummaryInBase"):
        el = stmt.find(f".//{path}")
        if el is not None:
            cur = el.get("currency") or el.get("baseCurrency")
            if cur:
                return cur
    # fallback: first NAV row currency
    nav = stmt.find(".//EquitySummaryByReportDateInBase")
    if nav is not None:
        cur = nav.get("currency")
        if cur:
            return cur
    return None


def _find_account_type(stmt: ET.Element) -> str | None:
    el = stmt.find(".//AccountInformation")
    return el.get("accountType") if el is not None else None


def _iter(stmt: ET.Element, container: str, row: str) -> Iterable[ET.Element]:
    """Iterate `<row>` elements under any `<container>` matching the section."""
    for c in stmt.findall(f".//{container}"):
        for r in c.findall(row):
            yield r
    # Some Flex outputs put rows directly under FlexStatement without a wrapper.
    for r in stmt.findall(f"./{row}"):
        yield r


def _row_xml(el: ET.Element) -> str:
    try:
        return ET.tostring(el, encoding="unicode")
    except Exception:
        return ""


# --------------------------------------------------------- section parsers
def _parse_instruments(stmt: ET.Element) -> Iterable[Instrument]:
    """Parse `<SecuritiesInfo><SecurityInfo .../></SecuritiesInfo>`."""
    seen: set[int] = set()
    for el in _iter(stmt, "SecuritiesInfo", "SecurityInfo"):
        cid = _int(el.get("conid"))
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        yield Instrument(
            conid=cid,
            symbol=el.get("symbol") or "",
            description=_str(el.get("description")),
            asset_class=_str(el.get("assetCategory")),
            sub_category=_str(el.get("subCategory")),
            isin=_str(el.get("isin")),
            cusip=_str(el.get("cusip")),
            figi=_str(el.get("figi")),
            listing_exchange=_str(el.get("listingExchange") or el.get("primaryExchange")),
            issuer=_str(el.get("issuer")),
            issuer_country=_str(el.get("issuerCountryCode")),
            currency=_str(el.get("currency")),
            multiplier=_dec(el.get("multiplier")),
            sector=_str(el.get("sector")),
            industry=_str(el.get("industry") or el.get("subProduct")),
        )


def _parse_conversion_rates(stmt: ET.Element) -> Iterable[FxRate]:
    for el in _iter(stmt, "ConversionRates", "ConversionRate"):
        d = _date(el.get("reportDate"))
        from_ccy = _str(el.get("fromCurrency"))
        to_ccy = _str(el.get("toCurrency"))
        rate = _dec(el.get("rate"))
        if d and from_ccy and to_ccy and rate is not None:
            yield FxRate(rate_date=d, from_ccy=from_ccy, to_ccy=to_ccy, rate=rate)


def _parse_trades(stmt: ET.Element, account_id: str) -> Iterable[Trade]:
    for el in _iter(stmt, "Trades", "Trade"):
        # Skip aggregate (Order/ClosedLot/Lot) rows when present — only EXECUTION
        level = el.get("levelOfDetail")
        if level and level.upper() not in ("EXECUTION", "TRADE"):
            continue
        trade_id = _str(el.get("tradeID")) or _str(el.get("transactionID"))
        cid = _int(el.get("conid"))
        if not trade_id or cid is None:
            continue
        dt = _dt(el.get("dateTime") or el.get("tradeDate"))
        if dt is None:
            continue
        yield Trade(
            trade_id=trade_id,
            account_id=el.get("accountId") or account_id,
            conid=cid,
            symbol=el.get("symbol") or "",
            trade_datetime=dt,
            trade_date=_date(el.get("tradeDate")) or dt.date(),
            settle_date=_date(el.get("settleDateTarget") or el.get("settleDate")),
            buy_sell=_str(el.get("buySell")),
            quantity=_dec(el.get("quantity")) or Decimal(0),
            trade_price=_dec(el.get("tradePrice")) or Decimal(0),
            proceeds=_dec(el.get("proceeds")) or Decimal(0),
            taxes=_dec(el.get("taxes")),
            ib_commission=_dec(el.get("ibCommission")),
            ib_commission_ccy=_str(el.get("ibCommissionCurrency")),
            net_cash=_dec(el.get("netCash")) or Decimal(0),
            fifo_pnl_realized=_dec(el.get("fifoPnlRealized")),
            mtm_pnl=_dec(el.get("mtmPnl")),
            open_close=_str(el.get("openCloseIndicator")),
            currency=el.get("currency") or "",
            fx_rate_to_base=_dec(el.get("fxRateToBase")),
            transaction_type=_str(el.get("transactionType")),
            exchange=_str(el.get("exchange")),
            notes=_str(el.get("notes")),
            asset_class=_str(el.get("assetCategory")),
            multiplier=_dec(el.get("multiplier")),
            ib_order_id=_str(el.get("ibOrderID")),
            raw=_row_xml(el),
        )


def _parse_cash_transactions(
    stmt: ET.Element, account_id: str
) -> Iterable[CashTransaction]:
    for el in _iter(stmt, "CashTransactions", "CashTransaction"):
        tid = _str(el.get("transactionID"))
        if not tid:
            # Some types (e.g. accruals) may lack a transactionID; synthesize
            # from key attributes so loads remain idempotent.
            tid = _synthetic_id(
                el.get("accountId") or account_id,
                el.get("conid") or "",
                el.get("type") or "",
                el.get("dateTime") or el.get("settleDate") or "",
                el.get("amount") or "",
                el.get("description") or "",
            )
        dt = _dt(el.get("dateTime") or el.get("settleDate"))
        if dt is None:
            continue
        amount = _dec(el.get("amount"))
        if amount is None:
            continue
        country = _country_from_description(el.get("description") or "")
        yield CashTransaction(
            transaction_id=tid,
            account_id=el.get("accountId") or account_id,
            conid=_int(el.get("conid")),
            symbol=_str(el.get("symbol")),
            type=el.get("type") or "",
            amount=amount,
            currency=el.get("currency") or "",
            fx_rate_to_base=_dec(el.get("fxRateToBase")),
            event_datetime=dt,
            settle_date=_date(el.get("settleDate")),
            report_date=_date(el.get("reportDate")),
            description=_str(el.get("description")),
            country=country,
            related_trade_id=_str(el.get("tradeID")),
            code=_str(el.get("code")),
            asset_class=_str(el.get("assetCategory")),
            raw=_row_xml(el),
        )


def _parse_open_positions(
    stmt: ET.Element, account_id: str, snapshot: date | None
) -> Iterable[OpenPosition]:
    if snapshot is None:
        return
    grouped: dict[int, dict[str, list[ET.Element]]] = {}
    for el in _iter(stmt, "OpenPositions", "OpenPosition"):
        cid = _int(el.get("conid"))
        if cid is None:
            continue
        level = (el.get("levelOfDetail") or "").upper()
        if level and level not in ("SUMMARY", "LOT", ""):
            continue
        slot = grouped.setdefault(cid, {"SUMMARY": [], "LOT": [], "": []})
        slot[level if level in ("SUMMARY", "LOT") else ""].append(el)

    for cid, levels in grouped.items():
        if levels["SUMMARY"]:
            yield _open_position_from_xml(levels["SUMMARY"][0], account_id, snapshot)
            continue
        if levels["LOT"]:
            yield _aggregate_open_position_lots(levels["LOT"], account_id, snapshot)
            continue
        if levels[""]:
            yield _open_position_from_xml(levels[""][0], account_id, snapshot)


def _open_position_from_xml(
    el: ET.Element, account_id: str, snapshot: date | None
) -> OpenPosition:
    cid = _int(el.get("conid"))
    if cid is None:
        raise ValueError("OpenPosition missing conid")
    return OpenPosition(
        snapshot_date=_date(el.get("reportDate")) or snapshot,
        account_id=el.get("accountId") or account_id,
        conid=cid,
        symbol=el.get("symbol") or "",
        quantity=_dec(el.get("position")) or Decimal(0),
        mark_price=_dec(el.get("markPrice")),
        position_value=_dec(el.get("positionValue")),
        cost_basis_money=_dec(el.get("costBasisMoney")),
        cost_basis_price=_dec(el.get("costBasisPrice")),
        fifo_pnl_unrealized=_dec(el.get("fifoPnlUnrealized")),
        pct_of_nav=_dec(el.get("percentOfNAV")),
        currency=el.get("currency") or "",
        fx_rate_to_base=_dec(el.get("fxRateToBase")),
        multiplier=_dec(el.get("multiplier")),
        asset_class=_str(el.get("assetCategory")),
        holding_period_dt=_dt(el.get("holdingPeriodDateTime")),
        open_dt=_dt(el.get("openDateTime")),
        raw=_row_xml(el),
    )


def _aggregate_open_position_lots(
    rows: list[ET.Element], account_id: str, snapshot: date | None
) -> OpenPosition:
    first = rows[0]
    quantity = sum((_dec(el.get("position")) or Decimal(0)) for el in rows)
    position_value = sum((_dec(el.get("positionValue")) or Decimal(0)) for el in rows)
    cost_basis_money = sum((_dec(el.get("costBasisMoney")) or Decimal(0)) for el in rows)
    fifo_pnl_unrealized = sum((_dec(el.get("fifoPnlUnrealized")) or Decimal(0)) for el in rows)
    pct_of_nav = sum((_dec(el.get("percentOfNAV")) or Decimal(0)) for el in rows) or None
    cost_basis_price = (cost_basis_money / quantity) if quantity else None
    mark_price = _dec(first.get("markPrice"))
    if mark_price is None and quantity:
        mark_price = position_value / quantity
    open_dates = [
        dt for dt in (_dt(el.get("openDateTime")) for el in rows)
        if dt is not None
    ]
    holding_dates = [
        dt for dt in (_dt(el.get("holdingPeriodDateTime")) for el in rows)
        if dt is not None
    ]
    cid = _int(first.get("conid"))
    if cid is None:
        raise ValueError("OpenPosition missing conid")
    return OpenPosition(
        snapshot_date=_date(first.get("reportDate")) or snapshot,
        account_id=first.get("accountId") or account_id,
        conid=cid,
        symbol=first.get("symbol") or "",
        quantity=quantity,
        mark_price=mark_price,
        position_value=position_value,
        cost_basis_money=cost_basis_money,
        cost_basis_price=cost_basis_price,
        fifo_pnl_unrealized=fifo_pnl_unrealized,
        pct_of_nav=pct_of_nav,
        currency=first.get("currency") or "",
        fx_rate_to_base=_dec(first.get("fxRateToBase")),
        multiplier=_dec(first.get("multiplier")),
        asset_class=_str(first.get("assetCategory")),
        holding_period_dt=min(holding_dates) if holding_dates else None,
        open_dt=min(open_dates) if open_dates else None,
        raw=_row_xml(first),
    )


def _parse_corporate_actions(
    stmt: ET.Element, account_id: str
) -> Iterable[CorporateAction]:
    for el in _iter(stmt, "CorporateActions", "CorporateAction"):
        # Flex emits each event twice: a SUMMARY roll-up and the DETAIL row.
        # Keep DETAIL only (B59); a row without levelOfDetail (older query
        # layouts) is kept as-is.
        level = (el.get("levelOfDetail") or "").strip().upper()
        if level and level != "DETAIL":
            continue
        dt = _dt(el.get("dateTime") or el.get("reportDate"))
        if dt is None:
            continue
        ca_id = _str(el.get("transactionID"))
        if not ca_id:
            ca_id = _synthetic_id(
                el.get("accountId") or account_id,
                el.get("conid") or "",
                el.get("type") or "",
                el.get("dateTime") or "",
                el.get("amount") or "",
                el.get("actionDescription") or el.get("description") or "",
            )
        yield CorporateAction(
            ca_id=ca_id,
            account_id=el.get("accountId") or account_id,
            conid=_int(el.get("conid")),
            symbol=_str(el.get("symbol")),
            type=_str(el.get("type")),
            description=_str(el.get("actionDescription") or el.get("description")),
            event_datetime=dt,
            quantity=_dec(el.get("quantity")),
            proceeds=_dec(el.get("proceeds")),
            value=_dec(el.get("value") or el.get("amount")),
            currency=_str(el.get("currency")),
            raw=_row_xml(el),
        )


def _parse_cash_report(
    stmt: ET.Element, account_id: str, snapshot: date | None
) -> Iterable[CashReportRow]:
    if snapshot is None:
        return
    for el in _iter(stmt, "CashReport", "CashReportCurrency"):
        cur = _str(el.get("currency"))
        if not cur or cur == "BASE_SUMMARY":
            continue
        yield CashReportRow(
            snapshot_date=snapshot,
            account_id=el.get("accountId") or account_id,
            currency=cur,
            ending_cash=_dec(el.get("endingCash")),
            ending_settled_cash=_dec(el.get("endingSettledCash")),
            dividends=_dec(el.get("dividends")),
            withholding_tax=_dec(el.get("withholdingTax")),
            broker_interest=_first_dec(el, "brokerInterest", "brokerInterestPaid"),
            commissions=_dec(el.get("commissions")),
            fees_receivables=_dec(el.get("feesReceivables")),
            # IBKR's CashReportCurrency exposes a combined `depositsWithdrawals`
            # net field. Separate `deposits` / `withdrawals` are alternative
            # legacy names. Prefer the net, fall back to deposits, then
            # withdrawals. Previous logic dropped the combined field entirely
            # and short-circuited at the first non-zero deposit row.
            deposits_withdrawals=_first_dec(
                el, "depositsWithdrawals", "deposits", "withdrawals"
            ),
        )


def _parse_nav(
    stmt: ET.Element, account_id: str, snapshot: date | None
) -> Iterable[NavRow]:
    """`EquitySummaryByReportDateInBase` carries the daily NAV by component.

    Older Flex configurations expose `EquitySummaryInBase` (single row, period
    totals). We accept both.

    IBKR uses the same tag for the wrapper and the row. We detect rows by the
    presence of `reportDate` and `total`/`nav_total`-equivalent attributes.
    """
    rows = [
        el
        for el in stmt.findall(".//EquitySummaryByReportDateInBase")
        if el.get("reportDate") and el.get("total") is not None
    ]
    if not rows:
        rows = [
            el
            for el in stmt.findall(".//EquitySummaryInBase")
            if el.get("total") is not None
        ]
    for el in rows:
        d = _date(el.get("reportDate")) or snapshot
        if d is None:
            continue
        # IBKR exposes the accruals as `dividendAccruals` / `interestAccruals`
        # on the modern EquitySummaryByReportDateInBase schema. Older statement
        # variants used `dividendsReceivable` / `interestReceivable` /
        # `accruedDividends`. We prefer the canonical name and fall back to
        # the legacy ones — NEVER duplicate the same value across the two
        # distinct columns `dividends_receivable` and `accrued_dividends`,
        # which would cause downstream waterfalls to double-count.
        div_receivable = _dec(
            el.get("dividendsReceivable") or el.get("dividendAccruals")
        )
        # No fallback here: `dividendAccruals` already feeds
        # `dividends_receivable` above. An empty accruedDividends="" (a common
        # Flex pattern) must yield None, not inherit the same value into both
        # columns. `_dec` maps ""/None to None.
        accrued_div = _dec(el.get("accruedDividends"))
        int_accruals = _dec(el.get("interestAccruals") or el.get("interestReceivable"))
        yield NavRow(
            snapshot_date=d,
            account_id=el.get("accountId") or account_id,
            base_currency=el.get("currency") or "",
            nav_total=_dec(el.get("total")),
            cash=_dec(el.get("cash")),
            stock=_dec(el.get("stock")),
            options=_dec(el.get("options")),
            bonds=_dec(el.get("bond") or el.get("bonds")),
            funds=_dec(el.get("fund") or el.get("funds")),
            dividends_receivable=div_receivable,
            interest_receivable=int_accruals,
            accrued_dividends=accrued_div,
            realized_pnl=_dec(el.get("realizedPnl")),
            unrealized_pnl=_dec(el.get("unrealizedPnl")),
        )


def _parse_dividend_accruals(
    stmt: ET.Element, account_id: str
) -> Iterable[DividendAccrual]:
    for container, row in (
        ("OpenDividendAccruals", "OpenDividendAccrual"),
        ("ChangeInDividendAccruals", "ChangeInDividendAccrual"),
    ):
        for el in _iter(stmt, container, row):
            yield DividendAccrual(
                account_id=el.get("accountId") or account_id,
                conid=_int(el.get("conid")),
                symbol=_str(el.get("symbol")),
                ex_date=_date(el.get("exDate")),
                pay_date=_date(el.get("payDate")),
                quantity=_dec(el.get("quantity")),
                gross_rate=_dec(el.get("grossRate")),
                gross_amount=_dec(el.get("grossAmount")),
                net_amount=_dec(el.get("netAmount")),
                tax=_dec(el.get("tax")),
                fee=_dec(el.get("fee")),
                currency=_str(el.get("currency")),
                code=_str(el.get("code")),
            )


def _parse_interest_accruals(
    stmt: ET.Element, account_id: str
) -> Iterable[InterestAccrual]:
    for el in _iter(stmt, "InterestAccruals", "InterestAccrualsCurrency"):
        d = _date(el.get("toDate") or el.get("reportDate") or el.get("accrualDate"))
        cur = _str(el.get("currency"))
        if d is None or not cur or cur == "BASE_SUMMARY":
            continue
        yield InterestAccrual(
            account_id=el.get("accountId") or account_id,
            accrual_date=d,
            currency=cur,
            # Flex sends `interestAccrued` (+ `accrualReversal`, `fxTranslation`
            # in raw_xml); `interest` is the pre-2026 spelling (B34). The row
            # key is (account, day, currency), so interest_type stays NULL.
            interest_type=None,
            interest=_first_dec(el, "interestAccrued", "interest"),
            ending_balance=_dec(el.get("endingAccrualBalance")),
            raw=_row_xml(el),
        )


def _parse_transaction_fees(
    stmt: ET.Element, account_id: str
) -> Iterable[TransactionFee]:
    """`Transaction Fees` (formerly `Transaction Tax`) — regulatory fees."""
    for container, row in (
        ("TransactionTaxes", "TransactionTax"),
        ("TransactionFees", "TransactionFee"),
    ):
        for el in _iter(stmt, container, row):
            amount = _dec(el.get("taxAmount") or el.get("amount"))
            if amount is None:
                continue
            yield TransactionFee(
                account_id=el.get("accountId") or account_id,
                # IBKR spells the id `tradeId` on this section (B35); keep the
                # `tradeID` spelling of the other sections as a fallback.
                trade_id=_str(el.get("tradeId") or el.get("tradeID")),
                symbol=_str(el.get("symbol")),
                conid=_int(el.get("conid")),
                event_datetime=_dt(el.get("dateTime") or el.get("date")),
                report_date=_date(el.get("reportDate")),
                quantity=_dec(el.get("quantity")),
                tax_amount=amount,
                tax_description=_str(el.get("taxDescription") or el.get("description")),
                currency=_str(el.get("currency")),
                raw=_row_xml(el),
            )


def _parse_change_in_nav(
    stmt: ET.Element, account_id: str
) -> Iterable[ChangeInNavRow]:
    """`<ChangeInNAV>` — one row per (account, day) carrying the full
    waterfall from starting NAV to ending NAV. Daily TWR is also here.
    """
    for el in stmt.findall(".//ChangeInNAV"):
        if el.get("startingValue") is None or el.get("endingValue") is None:
            continue
        # The Flex period is usually a single day (fromDate == toDate).
        d = _date(el.get("toDate") or el.get("fromDate") or el.get("reportDate"))
        if d is None:
            continue
        yield ChangeInNavRow(
            snapshot_date=d,
            account_id=el.get("accountId") or account_id,
            currency=el.get("currency") or "",
            starting_value=_dec(el.get("startingValue")),
            ending_value=_dec(el.get("endingValue")),
            mtm=_dec(el.get("mtm")),
            realized=_dec(el.get("realized")),
            change_in_unrealized=_dec(el.get("changeInUnrealized")),
            dividends=_dec(el.get("dividends")),
            withholding_tax=_dec(el.get("withholdingTax")),
            change_in_dividend_accruals=_dec(el.get("changeInDividendAccruals")),
            interest=_dec(el.get("interest")),
            change_in_interest_accruals=_dec(el.get("changeInInterestAccruals")),
            commissions=_dec(el.get("commissions")),
            broker_fees=_dec(el.get("brokerFees")),
            other_fees=_dec(el.get("otherFees")),
            transaction_tax=_dec(el.get("transactionTax")),
            deposits_withdrawals=_dec(el.get("depositsWithdrawals")),
            fx_translation=_dec(el.get("fxTranslation")),
            twr=_dec(el.get("twr")),
            raw=_row_xml(el),
            asset_transfers=_dec(el.get("assetTransfers")),
            sales_tax=_dec(el.get("salesTax")),
        )


def _parse_fifo_performance(
    stmt: ET.Element, account_id: str
) -> Iterable[FifoPerformanceRow]:
    """`<FIFOPerformanceSummaryUnderlying>` — per-symbol FIFO P&L per day.

    Each row carries its own ``reportDate`` (the snapshot date). Wrappers may
    be ``FIFOPerformanceSummaryInBase`` or ``FIFOPerformanceSummary`` depending
    on the query config — `_iter` walks any container in the statement.

    Rows without a conid (CASH FX rollups and account-level totals appear with
    empty/missing conid) are skipped — the table is keyed on a real instrument.
    """
    seen: set[tuple[date, str, int]] = set()
    for el in _iter(stmt, "FIFOPerformanceSummaryInBase", "FIFOPerformanceSummaryUnderlying"):
        cid = _int(el.get("conid"))
        if cid is None:
            continue
        snap = _date(el.get("reportDate"))
        if snap is None:
            continue
        acct = el.get("accountId") or account_id
        key = (snap, acct, cid)
        if key in seen:
            continue
        seen.add(key)
        yield FifoPerformanceRow(
            snapshot_date=snap,
            account_id=acct,
            conid=cid,
            symbol=_str(el.get("symbol")),
            asset_class=_str(el.get("assetCategory")),
            cost_adj=_dec(el.get("costAdj")),
            realized_st_profit=_dec(el.get("realizedSTProfit")),
            realized_st_loss=_dec(el.get("realizedSTLoss")),
            realized_lt_profit=_dec(el.get("realizedLTProfit")),
            realized_lt_loss=_dec(el.get("realizedLTLoss")),
            total_realized_pnl=_dec(el.get("totalRealizedPnl")),
            unrealized_profit=_dec(el.get("unrealizedProfit")),
            unrealized_loss=_dec(el.get("unrealizedLoss")),
            unrealized_st_profit=_dec(el.get("unrealizedSTProfit")),
            unrealized_st_loss=_dec(el.get("unrealizedSTLoss")),
            unrealized_lt_profit=_dec(el.get("unrealizedLTProfit")),
            unrealized_lt_loss=_dec(el.get("unrealizedLTLoss")),
            total_unrealized_pnl=_dec(el.get("totalUnrealizedPnl")),
            total_fifo_pnl=_dec(el.get("totalFifoPnl")),
            transferred_pnl=_dec(el.get("transferredPnl")),
            raw=_row_xml(el),
        )


def _parse_mtm_performance(
    stmt: ET.Element, account_id: str
) -> Iterable[MtmPerformanceRow]:
    """`<MTMPerformanceSummaryUnderlying>` — per-symbol DAILY MTM P&L."""
    seen: set[tuple[date, str, int]] = set()
    for el in _iter(stmt, "MTMPerformanceSummaryInBase", "MTMPerformanceSummaryUnderlying"):
        cid = _int(el.get("conid"))
        if cid is None:
            continue
        snap = _date(el.get("reportDate"))
        if snap is None:
            continue
        acct = el.get("accountId") or account_id
        key = (snap, acct, cid)
        if key in seen:
            continue
        seen.add(key)
        yield MtmPerformanceRow(
            snapshot_date=snap,
            account_id=acct,
            conid=cid,
            symbol=_str(el.get("symbol")),
            asset_class=_str(el.get("assetCategory")),
            prev_close_quantity=_dec(el.get("prevCloseQuantity")),
            prev_close_price=_dec(el.get("prevClosePrice")),
            close_quantity=_dec(el.get("closeQuantity")),
            close_price=_dec(el.get("closePrice")),
            transaction_mtm=_dec(el.get("transactionMtm")),
            prior_open_mtm=_dec(el.get("priorOpenMtm")),
            commissions=_dec(el.get("commissions")),
            other=_dec(el.get("other")),
            other_with_accruals=_dec(el.get("otherWithAccruals")),
            total=_dec(el.get("total")),
            total_with_accruals=_dec(el.get("totalWithAccruals")),
            raw=_row_xml(el),
        )


def _parse_trade_confirms(
    stmt: ET.Element, account_id: str
) -> Iterable[TradeConfirmRow]:
    """`<TradeConfirm>` rows from Trade Confirmation Flex (TCF).

    TC emits THREE row types under `<TradeConfirms>`: `SymbolSummary`, `Order`,
    and `TradeConfirm`. Only the execution-level `TradeConfirm` carries
    `execID` and is unique-per-fill — the other two are aggregates that
    duplicate fields. We filter strictly on `levelOfDetail="EXECUTION"`.

    A TC pull against an Activity Flex query id will yield zero rows here,
    and vice-versa — both paths share `parse_flex_xml` so this is safe.
    """
    for el in _iter(stmt, "TradeConfirms", "TradeConfirm"):
        if (el.get("levelOfDetail") or "").upper() != "EXECUTION":
            continue
        exec_id = el.get("execID") or ""
        if not exec_id:
            continue
        cid = _int(el.get("conid"))
        if cid is None:
            continue
        exec_dt = _dt(el.get("dateTime"))
        td = _date(el.get("tradeDate"))
        if exec_dt is None or td is None:
            continue
        yield TradeConfirmRow(
            exec_id=exec_id,
            account_id=el.get("accountId") or account_id,
            conid=cid,
            symbol=_str(el.get("symbol")),
            asset_class=_str(el.get("assetCategory")),
            trade_id=_str(el.get("tradeID")),
            order_id=_str(el.get("orderID")),
            brokerage_order_id=_str(el.get("brokerageOrderID")),
            ext_exec_id=_str(el.get("extExecID")),
            block_id=_str(el.get("blockID")),
            order_time=_dt(el.get("orderTime")),
            execution_time=exec_dt,
            trade_date=td,
            settle_date=_date(el.get("settleDate")),
            report_date=_date(el.get("reportDate")),
            buy_sell=_str(el.get("buySell")),
            quantity=_dec(el.get("quantity")) or Decimal(0),
            price=_dec(el.get("price")) or Decimal(0),
            amount=_dec(el.get("amount")),
            proceeds=_dec(el.get("proceeds")),
            net_cash=_dec(el.get("netCash")),
            net_cash_with_billable=_dec(el.get("netCashWithBillable")),
            commission=_dec(el.get("commission")),
            broker_execution_commission=_dec(el.get("brokerExecutionCommission")),
            broker_clearing_commission=_dec(el.get("brokerClearingCommission")),
            third_party_execution_commission=_dec(el.get("thirdPartyExecutionCommission")),
            third_party_clearing_commission=_dec(el.get("thirdPartyClearingCommission")),
            third_party_regulatory_commission=_dec(el.get("thirdPartyRegulatoryCommission")),
            other_commission=_dec(el.get("otherCommission")),
            commission_currency=_str(el.get("commissionCurrency")),
            tax=_dec(el.get("tax")),
            sales_tax=_dec(el.get("salesTax")),
            other_tax=_dec(el.get("otherTax")),
            trade_charge=_dec(el.get("tradeCharge")),
            currency=_str(el.get("currency")) or "",
            multiplier=_dec(el.get("multiplier")),
            exchange=_str(el.get("exchange")),
            transaction_type=_str(el.get("transactionType")),
            order_type=_str(el.get("orderType")),
            # TC ships the open/close flag in `code` (not `openCloseIndicator`).
            open_close=_str(el.get("code")),
            is_api_order=_str(el.get("isAPIOrder")),
            trader_id=_str(el.get("traderID")),
            order_reference=_str(el.get("orderReference")),
            orig_trade_id=_str(el.get("origTradeID")),
            orig_trade_price=_dec(el.get("origTradePrice")),
            orig_trade_date=_date(el.get("origTradeDate")),
            raw=_row_xml(el),
        )


def _parse_option_eae(
    stmt: ET.Element, account_id: str
) -> Iterable[OptionEaeRow]:
    """`<OptionEAE>` — option Exercise / Assignment / Expiration events."""
    for el in _iter(stmt, "OptionEAE", "OptionEAE"):
        # The `_iter` helper might yield the wrapper itself when the wrapper
        # tag equals the row tag (true here). Filter to rows with attributes.
        if el.get("conid") is None and el.get("transactionID") is None:
            continue
        eae_id = _str(el.get("transactionID"))
        if not eae_id:
            eae_id = _synthetic_id(
                el.get("accountId") or account_id,
                el.get("conid") or "",
                el.get("transactionType") or "",
                el.get("date") or el.get("reportDate") or "",
                el.get("quantity") or "",
                el.get("tradePrice") or "",
            )
        yield OptionEaeRow(
            eae_id=eae_id,
            account_id=el.get("accountId") or account_id,
            conid=_int(el.get("conid")),
            symbol=_str(el.get("symbol")),
            underlying_conid=_int(el.get("underlyingConid")),
            underlying_symbol=_str(el.get("underlyingSymbol")),
            transaction_type=_str(el.get("transactionType")),
            event_date=_date(el.get("date") or el.get("reportDate")),
            quantity=_dec(el.get("quantity")),
            multiplier=_dec(el.get("multiplier")),
            trade_price=_dec(el.get("tradePrice")),
            proceeds=_dec(el.get("proceeds")),
            commission=_dec(el.get("commisionsAndTax") or el.get("commission")),
            realized_pnl=_dec(el.get("realizedPnl")),
            currency=_str(el.get("currency")),
            raw=_row_xml(el),
        )


def _parse_transfers(
    stmt: ET.Element, account_id: str
) -> Iterable[TransferRow]:
    """`<Transfers>` — ACAT / position transfers. Empty in many statements;
    wired defensively so the first transfer event lands without backfill.
    """
    for el in _iter(stmt, "Transfers", "Transfer"):
        cid = _int(el.get("conid"))
        tid = _str(el.get("transactionID")) or _synthetic_id(
            el.get("accountId") or account_id,
            el.get("conid") or "",
            el.get("type") or "",
            el.get("date") or el.get("reportDate") or "",
            el.get("quantity") or "",
            el.get("direction") or "",
        )
        yield TransferRow(
            transfer_id=tid,
            account_id=el.get("accountId") or account_id,
            conid=cid,
            symbol=_str(el.get("symbol")),
            direction=_str(el.get("direction")),
            transfer_type=_str(el.get("type") or el.get("transferType")),
            event_date=_date(el.get("date") or el.get("reportDate")),
            quantity=_dec(el.get("quantity")),
            position_value=_dec(el.get("positionAmount") or el.get("positionAmountInBase")),
            cash_value=_dec(el.get("cashTransfer") or el.get("cashTransferAmount")),
            cost_basis=_dec(el.get("costAdjustmentAmount") or el.get("transferAdjustmentAmount")),
            delivering_broker=_str(el.get("deliveringBroker")),
            currency=_str(el.get("currency")),
            raw=_row_xml(el),
        )


# --------------------------------------------------------------- description
def _country_from_description(desc: str) -> str | None:
    """Extract ISO-2 country from withholding-tax descriptions like
    'SGN(US8688612048) PAYMENT IN LIEU OF DIVIDEND - US TAX'.
    """
    if not desc:
        return None
    # token at the end like ' - US TAX'
    m = desc.rfind(" - ")
    if m > 0:
        tail = desc[m + 3 :].strip()
        # tail looks like 'US TAX' or 'US WITHHOLDING'
        first = tail.split()[0] if tail else ""
        if len(first) == 2 and first.isalpha() and first.isupper():
            return first
    return None


def _synthetic_id(*parts: Any) -> str:
    """Stable hash for Flex rows that don't carry an IBKR-issued ID."""
    import hashlib

    payload = "|".join(str(p or "") for p in parts).encode("utf-8")
    return "syn_" + hashlib.sha1(payload).hexdigest()[:24]
