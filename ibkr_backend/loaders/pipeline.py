"""Pipeline orchestrator — fetch Flex → parse → idempotent UPSERT → log.

Each Flex section is mapped via a dedicated loader function below. The
top-level `run_full_pipeline()` is what cron / APScheduler / FastAPI call.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope
from ..db.models import (
    Account,
    CashReportRow,
    CashTransaction,
    ChangeInNavDaily,
    CorporateAction,
    DividendAccrual,
    FifoPerformanceUnderlying,
    FlexRun,
    FxRate,
    Instrument,
    InterestAccrual,
    MtmPerformanceUnderlying,
    NavDaily,
    OptionExerciseAssignment,
    PositionEod,
    Trade,
    TradeExecution,
    Transfer,
    TransactionFee,
)
from ..flex.client import FlexClient
from ..flex.parser import (
    ParsedStatement,
    parse_flex_xml,
)
from ..analytics._common import null_fx_counts
from ..analytics.options_parsing import enrich_option
from ._asset_class import canon as _canon_asset_class
from ._upsert import upsert_ignore, upsert_replace
from .hygiene import backfill_base_currency

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- result types
@dataclass(slots=True)
class LoadResult:
    section: str
    inserted: int = 0
    skipped: int = 0
    replaced: int = 0


@dataclass(slots=True)
class PipelineResult:
    query_id: str
    reference_code: str | None
    from_date: date | None
    to_date: date | None
    statements: int = 0
    sections: list[LoadResult] = field(default_factory=list)
    error: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))

    @property
    def total_inserted(self) -> int:
        return sum(s.inserted for s in self.sections)

    @property
    def total_skipped(self) -> int:
        return sum(s.skipped for s in self.sections)

    @property
    def total_replaced(self) -> int:
        return sum(s.replaced for s in self.sections)


def _previous_business_day(d: date) -> date:
    """Most recent Mon-Fri strictly before `d` (exchange holidays ignored)."""
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def is_truncated_statement(to_date: date | None, today: date | None = None) -> bool:
    """True when a statement ends before (last business day - 1).

    A query whose portal period stops covering recent days still returns
    `success` with 0 new rows (Aug-Sep 2026: found 26 days late). Marking the
    run `stale` makes the health checks, which only count `success` runs, see
    it. A statement without a `toDate` cannot be judged and is not stale.
    """
    if to_date is None:
        return False
    last_bd = _previous_business_day((today or date.today()))
    return to_date < _previous_business_day(last_bd)


# -------------------------------------------------------------------- public
def run_full_pipeline(
    *,
    query_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    period: str | None = None,
    note: str | None = None,
) -> PipelineResult:
    """Fetch a Flex Query and persist all sections. Idempotent.

    The same orchestrator handles both **Activity Flex** queries (sections
    Trades / Cash / NAV / FIFO / MTM …) and **Trade Confirmation Flex**
    queries (TradeConfirms only). Section loaders short-circuit when their
    payload is empty, so calling with a TC query id is safe and only writes
    to `trade_executions`.
    """
    settings = get_settings()
    qid = query_id or settings.ib_flex_query_id
    if not settings.ib_flex_token or not qid:
        raise RuntimeError(
            "IB_FLEX_TOKEN and IB_FLEX_QUERY_ID must be configured (.env). "
            "Generate the token in Client Portal → Settings → Account Settings "
            "→ Reporting → Flex Web Service."
        )

    result = PipelineResult(query_id=qid, reference_code=None, from_date=None, to_date=None)
    try:
        with FlexClient(
            token=settings.ib_flex_token,
            user_agent=settings.flex_user_agent,
            max_wait_seconds=settings.flex_max_wait_seconds,
            poll_interval_seconds=settings.flex_poll_interval_seconds,
        ) as client:
            resp = client.fetch(
                qid, from_date=from_date, to_date=to_date, period=period
            )
        result.reference_code = resp.reference_code

        statements = parse_flex_xml(resp.xml)
        result.statements = len(statements)
        if statements:
            result.from_date = min(s.from_date for s in statements if s.from_date) if any(s.from_date for s in statements) else None
            result.to_date = max(s.to_date for s in statements if s.to_date) if any(s.to_date for s in statements) else None

        # A pull with an explicit window (bootstrap, TC backfill) is historical
        # by design, so only the saved-period pulls are judged for staleness.
        stale = not (from_date or to_date) and is_truncated_statement(result.to_date)
        if stale:
            log.warning(
                "Flex statement ends %s, before the last business day - 1: "
                "run marked stale (check the query's portal period)",
                result.to_date,
            )
        with session_scope() as session:
            for stmt in statements:
                section_results = load_statement(session, stmt)
                result.sections.extend(section_results)
            _log_run(session, result, note=note, status="stale" if stale else "success")
    except Exception as e:  # noqa: BLE001
        result.error = _safe_error(e, secrets=(settings.ib_flex_token, qid))
        log.exception("Pipeline failed: %s", result.error)
        with session_scope() as session:
            _log_run(session, result, note=note, status="error")
        raise
    return result


def run_tc_pipeline(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    note: str | None = None,
) -> PipelineResult:
    """Run the Trade Confirmation Flex pipeline (uses ``IB_FLEX_QUERY_ID_TC``).

    TC queries are fixed to ``Period=Today`` in the portal; ``from_date`` /
    ``to_date`` override that at fetch time, capped to IBKR's 45-day rolling
    TC window.
    """
    settings = get_settings()
    if not settings.ib_flex_query_id_tc:
        raise RuntimeError(
            "IB_FLEX_QUERY_ID_TC is not set. Add it to .env after creating a "
            "Trade Confirmation Flex Query in IBKR Portal → Reporting → "
            "Flex Queries → Trade Confirmation Flex Query."
        )
    return run_full_pipeline(
        query_id=settings.ib_flex_query_id_tc,
        from_date=from_date,
        to_date=to_date,
        note=note or "tc",
    )


def load_statement(session: Session, stmt: ParsedStatement) -> list[LoadResult]:
    """Persist a single ParsedStatement. Order matters for FK integrity."""
    out: list[LoadResult] = []
    out.append(_load_account(session, stmt))
    out.append(_load_instruments(session, stmt))
    out.append(_load_fx_rates(session, stmt))
    out.append(_load_trades(session, stmt))
    out.append(_load_cash_transactions(session, stmt))
    out.append(_load_positions(session, stmt))
    out.append(_load_corporate_actions(session, stmt))
    out.append(_load_cash_report(session, stmt))
    out.append(_load_nav(session, stmt))
    out.append(_load_dividend_accruals(session, stmt))
    out.append(_load_interest_accruals(session, stmt))
    out.append(_load_transaction_fees(session, stmt))
    out.append(_load_change_in_nav(session, stmt))
    out.append(_load_fifo_performance(session, stmt))
    out.append(_load_mtm_performance(session, stmt))
    out.append(_load_trade_executions(session, stmt))
    out.append(_load_option_eae(session, stmt))
    out.append(_load_transfers(session, stmt))
    # Self-heal: repair any account left without a base_currency from the NAV
    # rows just loaded (statements without an EquitySummary currency leave it
    # NULL — nav_daily carries the canonical value).
    backfill_base_currency(session)
    null_fx_counts(session)  # logs a warning when a row would be dropped from base sums
    return out


def _safe_error(exc: Exception, *, secrets: tuple[str | None, ...] = ()) -> str:
    """Return an error string safe for DB/API/log surfaces."""
    msg = f"{type(exc).__name__}: {exc}"
    msg = re.sub(r"([?&](?:t|q)=)[^&\\s'\")]+", r"\1<redacted>", msg)
    for secret in secrets:
        if secret:
            msg = msg.replace(str(secret), "<redacted>")
    return msg


def dashboard_has_data(session: Session) -> bool:
    """Return True when the dashboard already has enough data to render."""
    checks = (
        select(func.count()).select_from(NavDaily),
        select(func.count()).select_from(PositionEod),
        select(func.count()).select_from(CashTransaction),
        select(func.count()).select_from(Trade),
    )
    return any((session.execute(stmt).scalar_one() or 0) > 0 for stmt in checks)


# ------------------------------------------------------------- section loaders
def _load_account(session: Session, stmt: ParsedStatement) -> LoadResult:
    a = stmt.account
    if not a.account_id:
        return LoadResult(section="accounts")
    row: dict = {"account_id": a.account_id}
    # Identity columns describe the account, not a point-in-time snapshot. Write
    # only the ones this statement actually carries: a pull that omits them
    # (e.g. a weekend LastBusinessDay statement without an EquitySummary /
    # AccountInformation currency) must never overwrite a previously-known value
    # with NULL. backfill_base_currency() repairs any account left without one.
    for col, val in (
        ("base_currency", a.base_currency),
        ("alias", a.alias),
        ("account_type", a.account_type),
    ):
        if val is not None:
            row[col] = val
    data_cols = [c for c in row if c != "account_id"]
    if data_cols:
        n = upsert_replace(
            session, Account, [row],
            pk_cols=["account_id"], update_cols=[*data_cols, "updated_at"],
        )
    else:
        # Nothing but the id this time — insert if new, leave any existing row
        # (and its known base_currency/alias) untouched.
        n, _ = upsert_ignore(session, Account, [row], pk_cols=["account_id"])
    return LoadResult(section="accounts", replaced=n)


def _load_instruments(session: Session, stmt: ParsedStatement) -> LoadResult:
    """Auto-discover instrument rows from any section that names a conid.

    Many Flex configurations omit `SecuritiesInfo`. We always backfill from
    `OpenPositions`, `Trades`, and `CashTransactions` so FK constraints hold.
    """
    seen: dict[int, dict] = {i.conid: _instrument_row(i) for i in stmt.instruments}
    for tr in stmt.trades:
        seen.setdefault(tr.conid, _stub_instrument_row(
            tr.conid, tr.symbol, tr.asset_class, tr.currency, tr.multiplier,
        ))
    for op in stmt.open_positions:
        seen.setdefault(op.conid, _stub_instrument_row(
            op.conid, op.symbol, op.asset_class, op.currency, op.multiplier,
        ))
    for eae in stmt.option_eae:
        if eae.conid is not None:
            seen.setdefault(eae.conid, _stub_instrument_row(
                eae.conid, eae.symbol or "", "OPT", eae.currency, eae.multiplier,
            ))
    for ct in stmt.cash_transactions:
        if ct.conid is not None:
            seen.setdefault(ct.conid, _stub_instrument_row(
                ct.conid, ct.symbol or "", ct.asset_class, ct.currency,
            ))
    if not seen:
        return LoadResult(section="instruments")
    # Rows from SecuritiesInfo carry the full enrichment: replace so newer
    # values win. Discovery stubs (conid known only from trades/positions)
    # carry NULL for every enrichment field, so they must only create missing
    # rows and never overwrite an enriched one (B38).
    full_conids = {i.conid for i in stmt.instruments}
    full_rows = [r for c, r in seen.items() if c in full_conids]
    stub_rows = [r for c, r in seen.items() if c not in full_conids]
    n = upsert_replace(
        session, Instrument, full_rows, pk_cols=["conid"],
        update_cols=[
            "symbol", "description", "asset_class", "sub_category",
            "isin", "cusip", "figi", "listing_exchange", "issuer",
            "issuer_country", "currency", "multiplier", "option_underlying",
            "option_expiry", "option_right", "option_strike", "sector", "industry",
        ],
    )
    created, _ = upsert_ignore(session, Instrument, stub_rows, pk_cols=["conid"])
    return LoadResult(section="instruments", inserted=created, replaced=n)


def _instrument_row(i) -> dict:
    asset_class = _canon_asset_class(i.asset_class)
    return {
        "conid": i.conid,
        "symbol": i.symbol,
        "description": i.description,
        "asset_class": asset_class,
        "sub_category": i.sub_category,
        "isin": i.isin,
        "cusip": i.cusip,
        "figi": i.figi,
        "listing_exchange": i.listing_exchange,
        "issuer": i.issuer,
        "issuer_country": i.issuer_country,
        "currency": i.currency,
        "multiplier": i.multiplier,
        **_option_contract_fields(i.symbol, i.description, asset_class),
        "sector": i.sector,
        "industry": i.industry,
    }


def _stub_instrument_row(
    conid: int,
    symbol: str,
    asset_class=None,
    currency=None,
    multiplier=None,
) -> dict:
    """Minimal instrument row for FK backfill — all optional fields None."""
    canonical_asset_class = _canon_asset_class(asset_class)
    return {
        "conid": conid,
        "symbol": symbol or "",
        "description": None,
        "asset_class": canonical_asset_class,
        "sub_category": None,
        "isin": None,
        "cusip": None,
        "figi": None,
        "listing_exchange": None,
        "issuer": None,
        "issuer_country": None,
        "currency": currency,
        "multiplier": multiplier,
        **_option_contract_fields(symbol, None, canonical_asset_class),
        "sector": None,
        "industry": None,
    }


def _option_contract_fields(
    symbol: str | None,
    description: str | None,
    asset_class: str | None,
) -> dict:
    if asset_class != "OPT":
        return {
            "option_underlying": None,
            "option_expiry": None,
            "option_right": None,
            "option_strike": None,
        }
    contract = enrich_option(symbol, description)
    strike = Decimal(str(contract.strike)) if contract.strike is not None else None
    return {
        "option_underlying": contract.underlying,
        "option_expiry": contract.expiry,
        "option_right": contract.right,
        "option_strike": strike,
    }


def _load_fx_rates(session: Session, stmt: ParsedStatement) -> LoadResult:
    rows = [
        {
            "rate_date": r.rate_date,
            "from_ccy": r.from_ccy,
            "to_ccy": r.to_ccy,
            "rate": r.rate,
        }
        for r in stmt.fx_rates
    ]
    if not rows:
        return LoadResult(section="fx_rates")
    n = upsert_replace(session, FxRate, rows)
    return LoadResult(section="fx_rates", replaced=n)


def _load_trades(session: Session, stmt: ParsedStatement) -> LoadResult:
    rows = [
        {
            "trade_id": t.trade_id,
            "ib_order_id": t.ib_order_id,
            "account_id": t.account_id,
            "conid": t.conid,
            "symbol": t.symbol,
            "trade_date": t.trade_date,
            "trade_datetime": t.trade_datetime,
            "settle_date": t.settle_date,
            "buy_sell": t.buy_sell,
            "quantity": t.quantity,
            "trade_price": t.trade_price,
            "proceeds": t.proceeds,
            "taxes": t.taxes,
            "ib_commission": t.ib_commission,
            "ib_commission_ccy": t.ib_commission_ccy,
            "net_cash": t.net_cash,
            "fifo_pnl_realized": t.fifo_pnl_realized,
            "mtm_pnl": t.mtm_pnl,
            "open_close": t.open_close,
            "currency": t.currency,
            "fx_rate_to_base": t.fx_rate_to_base,
            "transaction_type": t.transaction_type,
            "exchange": t.exchange,
            "notes": t.notes,
            "asset_class": _canon_asset_class(t.asset_class),
            "multiplier": t.multiplier,
            "raw_xml": t.raw,
        }
        for t in stmt.trades
        if t.account_id and t.account_id != "-"  # skip IBKR summary rows
    ]
    if not rows:
        return LoadResult(section="trades")
    inserted, skipped = upsert_ignore(session, Trade, rows)
    return LoadResult(section="trades", inserted=inserted, skipped=skipped)


def _load_cash_transactions(session: Session, stmt: ParsedStatement) -> LoadResult:
    rows = [
        {
            "transaction_id": ct.transaction_id,
            "account_id": ct.account_id,
            "conid": ct.conid,
            "symbol": ct.symbol,
            "type": ct.type,
            "amount": ct.amount,
            "currency": ct.currency,
            "fx_rate_to_base": ct.fx_rate_to_base,
            "event_datetime": ct.event_datetime,
            "settle_date": ct.settle_date,
            "report_date": ct.report_date,
            "description": ct.description,
            "country": ct.country,
            "related_trade_id": ct.related_trade_id,
            "code": ct.code,
            "asset_class": _canon_asset_class(ct.asset_class),
            "raw_xml": ct.raw,
        }
        for ct in stmt.cash_transactions
        if ct.account_id and ct.account_id != "-"  # skip IBKR summary rows
    ]
    if not rows:
        return LoadResult(section="cash_transactions")
    inserted, skipped = upsert_ignore(session, CashTransaction, rows)
    return LoadResult(section="cash_transactions", inserted=inserted, skipped=skipped)


def _load_positions(session: Session, stmt: ParsedStatement) -> LoadResult:
    rows = [
        {
            "snapshot_date": p.snapshot_date,
            "account_id": p.account_id,
            "conid": p.conid,
            "symbol": p.symbol,
            "quantity": p.quantity,
            "mark_price": p.mark_price,
            "position_value": p.position_value,
            "cost_basis_money": p.cost_basis_money,
            "cost_basis_price": p.cost_basis_price,
            "fifo_pnl_unrealized": p.fifo_pnl_unrealized,
            "pct_of_nav": p.pct_of_nav,
            "currency": p.currency,
            "fx_rate_to_base": p.fx_rate_to_base,
            "multiplier": p.multiplier,
            "asset_class": _canon_asset_class(p.asset_class),
            "holding_period_dt": p.holding_period_dt,
            "open_dt": p.open_dt,
            "raw_xml": p.raw,
        }
        for p in stmt.open_positions
    ]
    if not rows:
        return LoadResult(section="positions_eod")
    # Replace on (date, account, conid) so re-running for the same day fixes
    # any partial earlier loads.
    n = upsert_replace(session, PositionEod, rows)
    return LoadResult(section="positions_eod", replaced=n)


def _load_corporate_actions(session: Session, stmt: ParsedStatement) -> LoadResult:
    rows = [
        {
            "ca_id": c.ca_id,
            "account_id": c.account_id,
            "conid": c.conid,
            "symbol": c.symbol,
            "type": c.type,
            "description": c.description,
            "event_datetime": c.event_datetime,
            "quantity": c.quantity,
            "proceeds": c.proceeds,
            "value": c.value,
            "currency": c.currency,
            "raw_xml": c.raw,
        }
        for c in stmt.corporate_actions
    ]
    if not rows:
        return LoadResult(section="corporate_actions")
    inserted, skipped = upsert_ignore(session, CorporateAction, rows)
    return LoadResult(section="corporate_actions", inserted=inserted, skipped=skipped)


def _load_cash_report(session: Session, stmt: ParsedStatement) -> LoadResult:
    rows = [
        {
            "snapshot_date": r.snapshot_date,
            "account_id": r.account_id,
            "currency": r.currency,
            "ending_cash": r.ending_cash,
            "ending_settled_cash": r.ending_settled_cash,
            "dividends": r.dividends,
            "withholding_tax": r.withholding_tax,
            "broker_interest": r.broker_interest,
            "commissions": r.commissions,
            "fees_receivables": r.fees_receivables,
            "deposits_withdrawals": r.deposits_withdrawals,
        }
        for r in stmt.cash_report
        if r.account_id and r.account_id != "-"  # skip IBKR summary rows
    ]
    if not rows:
        return LoadResult(section="cash_report")
    n = upsert_replace(session, CashReportRow, rows)
    return LoadResult(section="cash_report", replaced=n)


def _load_nav(session: Session, stmt: ParsedStatement) -> LoadResult:
    rows = [
        {
            "snapshot_date": n.snapshot_date,
            "account_id": n.account_id,
            "base_currency": n.base_currency,
            "nav_total": n.nav_total,
            "cash": n.cash,
            "stock": n.stock,
            "options": n.options,
            "bonds": n.bonds,
            "funds": n.funds,
            "dividends_receivable": n.dividends_receivable,
            "interest_receivable": n.interest_receivable,
            "accrued_dividends": n.accrued_dividends,
            "realized_pnl": n.realized_pnl,
            "unrealized_pnl": n.unrealized_pnl,
        }
        for n in stmt.nav_rows
        if n.account_id and n.account_id != "-"  # skip IBKR summary rows
    ]
    if rows:
        upsert_replace(
            session, NavDaily, rows,
            update_cols=[
                "base_currency", "nav_total", "cash", "stock", "options",
                "bonds", "funds", "dividends_receivable", "interest_receivable",
                "accrued_dividends", "realized_pnl", "unrealized_pnl",
            ],
        )
    # Always (re-)derive external cashflow per NAV day — independent of NAV
    # presence — so TWR works even without NAV rows.
    _refresh_external_cashflows(session, stmt)
    return LoadResult(section="nav_daily", replaced=len(rows))


# Legacy `nav_daily.external_cashflow` derivation. NO analytics reads it any
# more: flows come from ChangeInNAV (`analytics/flows.py`). "Internal
# Transfers" was a dead type (0 rows ever) and is gone.
_EXTERNAL_CASHFLOW_TYPES = ("Deposits/Withdrawals",)


def _external_cashflows_by_settle_date(transactions) -> dict[tuple[date, str], Decimal]:
    """Canonical external-cashflow derivation — single source of truth shared
    by the per-statement refresh and the full-history reconcile.

    Works on both parsed (flex.parser.CashTransaction) and persisted
    (db.models.CashTransaction) rows. The daily NAV
    (EquitySummaryByReportDateInBase) books a flow on its SETTLEMENT date, so:
     - skip rows without a settle_date (event_datetime misaligns the flow from
       the NAV change → spurious Modified-Dietz returns: observed −85 % vs +50 %);
     - skip rows without fx_rate_to_base, mirroring SQL `SUM(amount*fx)` which
       drops NULL products. This matches the verified manual reconciliation.
    """
    by_day: dict[tuple[date, str], Decimal] = {}
    for ct in transactions:
        if ct.type not in _EXTERNAL_CASHFLOW_TYPES:
            continue
        if not ct.account_id or ct.account_id == "-":  # skip IBKR summary rows
            continue
        if ct.settle_date is None or ct.fx_rate_to_base is None:
            continue
        key = (ct.settle_date, ct.account_id)
        by_day[key] = by_day.get(key, Decimal(0)) + ct.amount * ct.fx_rate_to_base
    return by_day


def _refresh_external_cashflows(session: Session, stmt: ParsedStatement) -> None:
    """Sum Deposits/Withdrawals per (settle date, account) onto EXISTING nav_daily rows.

    Never inserts: a deposit settling on a day without a NAV (weekend) used to
    create a NAV-less `nav_daily` row (2023-11-18), which broke "first NAV day"
    logic (B50). Flows on such days are simply not mirrored here.
    """
    if not stmt.cash_transactions:
        return
    by_day = _external_cashflows_by_settle_date(stmt.cash_transactions)
    if not by_day:
        return
    for (d, acc), cf in by_day.items():
        nav = session.get(NavDaily, (d, acc))
        if nav is not None:
            nav.external_cashflow = cf


def reconcile_external_cashflows(session: Session) -> int:
    """Re-derive external_cashflow for every nav_daily row from the full cash_transactions table.

    Unlike _refresh_external_cashflows (which only processes the current statement),
    this function aggregates ALL cash_transactions with the matching types across the
    entire history and resets every nav_daily.external_cashflow to the derived value
    (or 0.0 if no matching transactions fall on that date). Residue rows — dates that
    once received a cashflow but no longer have matching transactions — are zeroed out.

    Returns the number of nav_daily rows whose external_cashflow value changed.
    """
    # Build the full derived map: (settle date, account_id) -> sum of
    # base-currency cashflows, via the same canonical rule the per-statement
    # refresh uses (_external_cashflows_by_settle_date).
    rows = (
        session.execute(
            select(CashTransaction).where(
                CashTransaction.type.in_(_EXTERNAL_CASHFLOW_TYPES)
            )
        )
        .scalars()
        .all()
    )
    derived = _external_cashflows_by_settle_date(rows)

    nav_rows = session.execute(select(NavDaily)).scalars().all()
    changed = 0
    for nav in nav_rows:
        expected = derived.get((nav.snapshot_date, nav.account_id), Decimal(0))
        current = nav.external_cashflow if nav.external_cashflow is not None else Decimal(0)
        if abs(float(current) - float(expected)) > 0.01:
            nav.external_cashflow = expected
            changed += 1
    return changed


_DIVIDEND_ACCRUAL_KEY = ["account_id", "conid", "ex_date", "pay_date", "gross_amount"]


def _load_dividend_accruals(session: Session, stmt: ParsedStatement) -> LoadResult:
    rows = [
        {
            "account_id": d.account_id,
            "conid": d.conid,
            "symbol": d.symbol,
            "ex_date": d.ex_date,
            "pay_date": d.pay_date,
            "quantity": d.quantity,
            "gross_rate": d.gross_rate,
            "gross_amount": d.gross_amount,
            "net_amount": d.net_amount,
            "tax": d.tax,
            "fee": d.fee,
            "currency": d.currency,
            "code": d.code,
        }
        for d in stmt.dividend_accruals
    ]
    if not rows:
        return LoadResult(section="dividend_accruals")

    # SQL NULLs never compare equal, so ON CONFLICT over uq_dividend_accrual
    # cannot catch rows where conid/ex_date/pay_date/gross_amount is NULL —
    # they would be re-inserted on every load. Route those through a manual
    # NULL-aware existence check; fully-keyed rows keep the fast upsert path.
    keyed_rows: list[dict] = []
    null_key_rows: list[dict] = []
    seen_null_keys: set[tuple] = set()
    inserted = skipped = 0
    for r in rows:
        if all(r[c] is not None for c in _DIVIDEND_ACCRUAL_KEY):
            keyed_rows.append(r)
            continue
        key = tuple(r[c] for c in _DIVIDEND_ACCRUAL_KEY)
        if key in seen_null_keys:  # in-batch duplicate
            skipped += 1
            continue
        seen_null_keys.add(key)
        null_key_rows.append(r)

    if keyed_rows:
        ins, skip = upsert_ignore(
            session, DividendAccrual, keyed_rows,
            pk_cols=_DIVIDEND_ACCRUAL_KEY,
        )
        inserted += ins
        skipped += skip

    for r in null_key_rows:
        conds = [
            getattr(DividendAccrual, c).is_(None)
            if r[c] is None
            else getattr(DividendAccrual, c) == r[c]
            for c in _DIVIDEND_ACCRUAL_KEY
        ]
        exists = session.execute(
            select(DividendAccrual.id).where(*conds).limit(1)
        ).first()
        if exists:
            skipped += 1
        else:
            session.add(DividendAccrual(**r))
            inserted += 1
    # session has autoflush=False: flush so the existence check above sees
    # these rows if another statement in the same scope repeats a NULL key.
    if inserted:
        session.flush()

    return LoadResult(section="dividend_accruals", inserted=inserted, skipped=skipped)


_INTEREST_ACCRUAL_KEY = ["account_id", "accrual_date", "currency"]


def _load_interest_accruals(session: Session, stmt: ParsedStatement) -> LoadResult:
    """Upsert one row per (account, day, currency).

    The key has no nullable column, so `ON CONFLICT` works and a re-pull fills
    `interest` / `raw_xml` on rows stored earlier with only `ending_balance`.
    """
    by_key: dict[tuple, dict] = {}
    for i in stmt.interest_accruals:
        by_key[(i.account_id, i.accrual_date, i.currency)] = {
            "account_id": i.account_id,
            "accrual_date": i.accrual_date,
            "currency": i.currency,
            "interest_type": i.interest_type,
            "interest": i.interest,
            "ending_balance": i.ending_balance,
            "raw_xml": i.raw or None,
        }
    rows = list(by_key.values())
    if not rows:
        return LoadResult(section="interest_accruals")
    n = upsert_replace(
        session, InterestAccrual, rows, pk_cols=_INTEREST_ACCRUAL_KEY,
        update_cols=["interest_type", "interest", "ending_balance", "raw_xml"],
    )
    return LoadResult(section="interest_accruals", replaced=n)


def _load_transaction_fees(session: Session, stmt: ParsedStatement) -> LoadResult:
    rows = []
    seen_keys: dict[str, int] = {}
    for t in stmt.transaction_fees:
        base_id = fee_id_for(t)
        occurrence = seen_keys.get(base_id, 0)
        seen_keys[base_id] = occurrence + 1
        fee_id = fee_id_for(t, occurrence) if occurrence else base_id
        rows.append({
            "fee_id": fee_id,
            "account_id": t.account_id,
            "trade_id": t.trade_id,
            "symbol": t.symbol,
            "conid": t.conid,
            "event_datetime": t.event_datetime,
            "report_date": t.report_date,
            "quantity": t.quantity,
            "tax_amount": t.tax_amount,
            "tax_description": t.tax_description,
            "currency": t.currency,
            "raw_xml": t.raw or None,
        })
    if not rows:
        return LoadResult(section="transaction_fees")
    # Replace the descriptive columns so a re-pull completes rows stored before
    # trade_id / event_datetime / raw_xml were captured. The key columns are
    # untouched.
    n = upsert_replace(
        session, TransactionFee, rows, pk_cols=["fee_id"],
        update_cols=["trade_id", "symbol", "event_datetime", "currency", "raw_xml"],
    )
    return LoadResult(section="transaction_fees", replaced=n)


def fee_id_for(t, occurrence: int = 0) -> str:
    """Deterministic `fee_` key for a parsed fee row (B35 formula).

    Built from account, report date, amount, description, conid and quantity,
    plus `occurrence` (0 for the first row with that key in a statement, 1 for
    the next identical one, ...). `trade_id` is deliberately NOT part of the
    key: whether Flex fills it must not change the id, or a re-pull would
    duplicate rows already stored. Two fills with the same amount and quantity
    on one day keep distinct ids through `occurrence`. Do not change this
    formula without a `rekey-fees` run: stored rows keep their old ids.
    """
    return _synthetic_fee_id(
        t.account_id, t.report_date, _canon_decimal(t.tax_amount),
        t.tax_description or "", t.conid or "", _canon_decimal(t.quantity), occurrence,
    )


def _canon_decimal(v) -> str:
    """Decimal text that is the same for `-0.4131`, `-0.41310000` and a float.

    Parsed rows hold Decimal('-0.4131'); rows read back from the database hold
    Decimal('-0.41310000'). Both must hash to the same fee id.
    """
    if v is None:
        return ""
    d = Decimal(str(v)).normalize()
    return "0" if d == 0 else format(d, "f")


def _synthetic_fee_id(*parts) -> str:
    import hashlib
    payload = "|".join(str(p or "") for p in parts).encode("utf-8")
    return "fee_" + hashlib.sha1(payload).hexdigest()[:24]


def _load_change_in_nav(session: Session, stmt: ParsedStatement) -> LoadResult:
    rows = [
        {
            "snapshot_date": r.snapshot_date,
            "account_id": r.account_id,
            "currency": r.currency,
            "starting_value": r.starting_value,
            "ending_value": r.ending_value,
            "mtm": r.mtm,
            "realized": r.realized,
            "change_in_unrealized": r.change_in_unrealized,
            "dividends": r.dividends,
            "withholding_tax": r.withholding_tax,
            "change_in_dividend_accruals": r.change_in_dividend_accruals,
            "interest": r.interest,
            "change_in_interest_accruals": r.change_in_interest_accruals,
            "commissions": r.commissions,
            "broker_fees": r.broker_fees,
            "other_fees": r.other_fees,
            "transaction_tax": r.transaction_tax,
            "deposits_withdrawals": r.deposits_withdrawals,
            "fx_translation": r.fx_translation,
            "twr": r.twr,
            "asset_transfers": r.asset_transfers,
            "sales_tax": r.sales_tax,
            "raw_xml": r.raw,
        }
        for r in stmt.change_in_nav
        if r.account_id and r.account_id != "-"
    ]
    if not rows:
        return LoadResult(section="change_in_nav")
    n = upsert_replace(
        session, ChangeInNavDaily, rows,
        pk_cols=["snapshot_date", "account_id"],
    )
    return LoadResult(section="change_in_nav", replaced=n)


def _load_fifo_performance(
    session: Session, stmt: ParsedStatement
) -> LoadResult:
    # Backfill instruments first so FK holds.
    conids_in_rows = {r.conid for r in stmt.fifo_performance}
    known = {i.conid for i in stmt.instruments}
    missing = conids_in_rows - known
    if missing:
        symbol_by_conid = {r.conid: r.symbol for r in stmt.fifo_performance}
        ac_by_conid = {r.conid: r.asset_class for r in stmt.fifo_performance}
        stub_rows = [
            _stub_instrument_row(cid, symbol_by_conid.get(cid) or "", ac_by_conid.get(cid))
            for cid in missing
        ]
        upsert_ignore(session, Instrument, stub_rows, pk_cols=["conid"])

    rows = [
        {
            "snapshot_date": r.snapshot_date,
            "account_id": r.account_id,
            "conid": r.conid,
            "symbol": r.symbol,
            "asset_class": _canon_asset_class(r.asset_class),
            "cost_adj": r.cost_adj,
            "realized_st_profit": r.realized_st_profit,
            "realized_st_loss": r.realized_st_loss,
            "realized_lt_profit": r.realized_lt_profit,
            "realized_lt_loss": r.realized_lt_loss,
            "total_realized_pnl": r.total_realized_pnl,
            "unrealized_profit": r.unrealized_profit,
            "unrealized_loss": r.unrealized_loss,
            "unrealized_st_profit": r.unrealized_st_profit,
            "unrealized_st_loss": r.unrealized_st_loss,
            "unrealized_lt_profit": r.unrealized_lt_profit,
            "unrealized_lt_loss": r.unrealized_lt_loss,
            "total_unrealized_pnl": r.total_unrealized_pnl,
            "total_fifo_pnl": r.total_fifo_pnl,
            "transferred_pnl": r.transferred_pnl,
            "raw_xml": r.raw,
        }
        for r in stmt.fifo_performance
        if r.account_id and r.account_id != "-"
    ]
    if not rows:
        return LoadResult(section="fifo_performance")
    n = upsert_replace(
        session, FifoPerformanceUnderlying, rows,
        pk_cols=["snapshot_date", "account_id", "conid"],
    )
    return LoadResult(section="fifo_performance", replaced=n)


def _load_mtm_performance(
    session: Session, stmt: ParsedStatement
) -> LoadResult:
    conids_in_rows = {r.conid for r in stmt.mtm_performance}
    known = {i.conid for i in stmt.instruments}
    missing = conids_in_rows - known
    if missing:
        symbol_by_conid = {r.conid: r.symbol for r in stmt.mtm_performance}
        ac_by_conid = {r.conid: r.asset_class for r in stmt.mtm_performance}
        stub_rows = [
            _stub_instrument_row(cid, symbol_by_conid.get(cid) or "", ac_by_conid.get(cid))
            for cid in missing
        ]
        upsert_ignore(session, Instrument, stub_rows, pk_cols=["conid"])

    rows = [
        {
            "snapshot_date": r.snapshot_date,
            "account_id": r.account_id,
            "conid": r.conid,
            "symbol": r.symbol,
            "asset_class": _canon_asset_class(r.asset_class),
            "prev_close_quantity": r.prev_close_quantity,
            "prev_close_price": r.prev_close_price,
            "close_quantity": r.close_quantity,
            "close_price": r.close_price,
            "transaction_mtm": r.transaction_mtm,
            "prior_open_mtm": r.prior_open_mtm,
            "commissions": r.commissions,
            "other": r.other,
            "other_with_accruals": r.other_with_accruals,
            "total": r.total,
            "total_with_accruals": r.total_with_accruals,
            "raw_xml": r.raw,
        }
        for r in stmt.mtm_performance
        if r.account_id and r.account_id != "-"
    ]
    if not rows:
        return LoadResult(section="mtm_performance")
    n = upsert_replace(
        session, MtmPerformanceUnderlying, rows,
        pk_cols=["snapshot_date", "account_id", "conid"],
    )
    return LoadResult(section="mtm_performance", replaced=n)


def _load_trade_executions(
    session: Session, stmt: ParsedStatement
) -> LoadResult:
    # Backfill instruments first so FK holds — TC may surface a conid that
    # Activity Flex hasn't seen yet (e.g. when TC is pulled before Activity).
    conids_in_rows = {r.conid for r in stmt.trade_confirms}
    known = {i.conid for i in stmt.instruments}
    missing = conids_in_rows - known
    if missing:
        symbol_by_conid = {r.conid: r.symbol for r in stmt.trade_confirms}
        ac_by_conid = {r.conid: r.asset_class for r in stmt.trade_confirms}
        stub_rows = [
            _stub_instrument_row(cid, symbol_by_conid.get(cid) or "", ac_by_conid.get(cid))
            for cid in missing
        ]
        upsert_ignore(session, Instrument, stub_rows, pk_cols=["conid"])

    rows = [
        {
            "exec_id": r.exec_id,
            "account_id": r.account_id,
            "conid": r.conid,
            "symbol": r.symbol,
            "asset_class": _canon_asset_class(r.asset_class),
            "trade_id": r.trade_id,
            "order_id": r.order_id,
            "brokerage_order_id": r.brokerage_order_id,
            "ext_exec_id": r.ext_exec_id,
            "block_id": r.block_id,
            "order_time": r.order_time,
            "execution_time": r.execution_time,
            "trade_date": r.trade_date,
            "settle_date": r.settle_date,
            "report_date": r.report_date,
            "buy_sell": r.buy_sell,
            "quantity": r.quantity,
            "price": r.price,
            "amount": r.amount,
            "proceeds": r.proceeds,
            "net_cash": r.net_cash,
            "net_cash_with_billable": r.net_cash_with_billable,
            "commission": r.commission,
            "broker_execution_commission": r.broker_execution_commission,
            "broker_clearing_commission": r.broker_clearing_commission,
            "third_party_execution_commission": r.third_party_execution_commission,
            "third_party_clearing_commission": r.third_party_clearing_commission,
            "third_party_regulatory_commission": r.third_party_regulatory_commission,
            "other_commission": r.other_commission,
            "commission_currency": r.commission_currency,
            "tax": r.tax,
            "sales_tax": r.sales_tax,
            "other_tax": r.other_tax,
            "trade_charge": r.trade_charge,
            "currency": r.currency,
            "multiplier": r.multiplier,
            "exchange": r.exchange,
            "transaction_type": r.transaction_type,
            "order_type": r.order_type,
            "open_close": r.open_close,
            "is_api_order": r.is_api_order,
            "trader_id": r.trader_id,
            "order_reference": r.order_reference,
            "orig_trade_id": r.orig_trade_id,
            "orig_trade_price": r.orig_trade_price,
            "orig_trade_date": r.orig_trade_date,
            "raw_xml": r.raw,
        }
        for r in stmt.trade_confirms
        if r.account_id and r.account_id != "-"
    ]
    if not rows:
        return LoadResult(section="trade_executions")
    inserted, skipped = upsert_ignore(
        session, TradeExecution, rows, pk_cols=["exec_id"]
    )
    return LoadResult(section="trade_executions", inserted=inserted, skipped=skipped)


def _load_option_eae(session: Session, stmt: ParsedStatement) -> LoadResult:
    rows = [
        {
            "eae_id": r.eae_id,
            "account_id": r.account_id,
            "conid": r.conid,
            "symbol": r.symbol,
            "underlying_conid": r.underlying_conid,
            "underlying_symbol": r.underlying_symbol,
            "transaction_type": r.transaction_type,
            "event_date": r.event_date,
            "quantity": r.quantity,
            "multiplier": r.multiplier,
            "trade_price": r.trade_price,
            "proceeds": r.proceeds,
            "commission": r.commission,
            "realized_pnl": r.realized_pnl,
            "currency": r.currency,
            "raw_xml": r.raw,
        }
        for r in stmt.option_eae
        if r.account_id and r.account_id != "-"
    ]
    if not rows:
        return LoadResult(section="option_eae")
    inserted, skipped = upsert_ignore(
        session, OptionExerciseAssignment, rows, pk_cols=["eae_id"]
    )
    return LoadResult(section="option_eae", inserted=inserted, skipped=skipped)


def _load_transfers(session: Session, stmt: ParsedStatement) -> LoadResult:
    rows = [
        {
            "transfer_id": r.transfer_id,
            "account_id": r.account_id,
            "conid": r.conid,
            "symbol": r.symbol,
            "direction": r.direction,
            "transfer_type": r.transfer_type,
            "event_date": r.event_date,
            "quantity": r.quantity,
            "position_value": r.position_value,
            "cash_value": r.cash_value,
            "cost_basis": r.cost_basis,
            "delivering_broker": r.delivering_broker,
            "currency": r.currency,
            "raw_xml": r.raw,
        }
        for r in stmt.transfers
        if r.account_id and r.account_id != "-"
    ]
    if not rows:
        return LoadResult(section="transfers")
    inserted, skipped = upsert_ignore(session, Transfer, rows, pk_cols=["transfer_id"])
    return LoadResult(section="transfers", inserted=inserted, skipped=skipped)


# ------------------------------------------------------------------- run log
def _log_run(
    session: Session, result: PipelineResult, *, note: str | None, status: str
) -> None:
    session.add(
        FlexRun(
            query_id=result.query_id,
            reference_code=result.reference_code,
            from_date=result.from_date,
            to_date=result.to_date,
            rows_inserted=result.total_inserted,
            rows_skipped_dup=result.total_skipped,
            rows_replaced=result.total_replaced,
            status=status,
            error=result.error,
            note=note,
        )
    )


# ------------------------------------------------------------- bootstrap util
def bootstrap_history(
    *, query_id: str | None = None, years: int = 4
) -> list[PipelineResult]:
    """Backfill multi-year history in 365-day windows (Flex's hard ceiling)."""
    settings = get_settings()
    qid = query_id or settings.ib_flex_query_id
    today = date.today()
    end = today
    results: list[PipelineResult] = []
    while end > today - timedelta(days=365 * years):
        start = end - timedelta(days=364)
        log.info("Bootstrap window %s → %s", start, end)
        try:
            r = run_full_pipeline(
                query_id=qid,
                from_date=start.strftime("%Y%m%d"),
                to_date=end.strftime("%Y%m%d"),
                note="bootstrap",
            )
            results.append(r)
        except Exception as e:  # noqa: BLE001
            log.warning("Bootstrap window %s-%s failed: %s", start, end, e)
        end = start - timedelta(days=1)
    return results


def bootstrap_history_if_empty(
    *, query_id: str | None = None, years: int = 4
) -> tuple[bool, list[PipelineResult]]:
    """Backfill history only when the dashboard DB has no usable data yet."""
    with session_scope() as session:
        if dashboard_has_data(session):
            return False, []
    return True, bootstrap_history(query_id=query_id, years=years)


# ----------------------------------------------------- offline / local file
def load_xml_file(path: str | "Path") -> list[LoadResult]:
    """Parse and load a Flex XML file from disk (for testing or backup imports)."""
    from pathlib import Path as _P

    raw = _P(path).read_bytes()
    statements = parse_flex_xml(raw)
    out: list[LoadResult] = []
    with session_scope() as session:
        for stmt in statements:
            out.extend(load_statement(session, stmt))
    return out
