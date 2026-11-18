"""ORM models — star schema mapping the deep-research §5.3 spec.

Idempotency comes from IBKR-issued IDs (`tradeID`, `transactionID`, ...). Use
INSERT OR IGNORE / ON CONFLICT DO NOTHING in loaders.

All monetary columns are `NUMERIC(20, 8)`. Postgres stores NUMERIC natively
(exact decimals); SQLite's NUMERIC affinity stores them as REAL (float64),
exact only up to ~15 significant digits — no practical loss at current
portfolio magnitudes. Never declare FLOAT for money: the NUMERIC column type
is what keeps the Postgres path exact.
"""

from __future__ import annotations

from datetime import UTC, date, datetime


def _utcnow() -> datetime:
    """Timezone-aware UTC now — replaces deprecated datetime.utcnow()."""
    return datetime.now(UTC).replace(tzinfo=None)
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Date,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .session import Base

MONEY = Numeric(20, 8)
RATE = Numeric(20, 10)


# -----------------------------------------------------------------  Dimensions
class Account(Base):
    __tablename__ = "accounts"

    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    base_currency: Mapped[str | None] = mapped_column(String(8))
    alias: Mapped[str | None] = mapped_column(String(64))
    account_type: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class Instrument(Base):
    __tablename__ = "instruments"

    conid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str | None] = mapped_column(String(256))
    asset_class: Mapped[str | None] = mapped_column(String(16), index=True)
    sub_category: Mapped[str | None] = mapped_column(String(32))
    isin: Mapped[str | None] = mapped_column(String(16), index=True)
    cusip: Mapped[str | None] = mapped_column(String(16))
    figi: Mapped[str | None] = mapped_column(String(16))
    listing_exchange: Mapped[str | None] = mapped_column(String(32))
    issuer: Mapped[str | None] = mapped_column(String(128))
    issuer_country: Mapped[str | None] = mapped_column(String(2), index=True)
    currency: Mapped[str | None] = mapped_column(String(8))
    multiplier: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    option_underlying: Mapped[str | None] = mapped_column(String(32))
    option_expiry: Mapped[date | None] = mapped_column(Date)
    option_right: Mapped[str | None] = mapped_column(String(1))
    option_strike: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    sector: Mapped[str | None] = mapped_column(String(64))
    industry: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class FxRate(Base):
    __tablename__ = "fx_rates"

    rate_date: Mapped[date] = mapped_column(Date, primary_key=True)
    from_ccy: Mapped[str] = mapped_column(String(8), primary_key=True)
    to_ccy: Mapped[str] = mapped_column(String(8), primary_key=True)
    rate: Mapped[Decimal] = mapped_column(RATE)


class BenchmarkDaily(Base):
    """Daily-close cache for external benchmark series (Yahoo Finance).

    Persisted on demand by `benchmarks.sp500` so /performance/* keeps serving
    the S&P overlay from the DB when Yahoo is unreachable (R5). `symbol` is
    the Yahoo symbol (^GSPC, USDCHF=X, ...); `currency` is the quote currency
    of `close` (USD for ^GSPC, the target currency for USD<ccy>=X rates).
    """

    __tablename__ = "benchmark_daily"

    price_date: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    close: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(8))


# -----------------------------------------------------------------  Facts
class Trade(Base):
    __tablename__ = "trades"

    trade_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ib_order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.account_id"), index=True
    )
    conid: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.conid"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    trade_datetime: Mapped[datetime] = mapped_column(DateTime, index=True)
    settle_date: Mapped[date | None] = mapped_column(Date)
    buy_sell: Mapped[str | None] = mapped_column(String(8))
    quantity: Mapped[Decimal] = mapped_column(MONEY)
    trade_price: Mapped[Decimal] = mapped_column(MONEY)
    proceeds: Mapped[Decimal] = mapped_column(MONEY)
    taxes: Mapped[Decimal | None] = mapped_column(MONEY)
    ib_commission: Mapped[Decimal | None] = mapped_column(MONEY)
    ib_commission_ccy: Mapped[str | None] = mapped_column(String(8))
    net_cash: Mapped[Decimal] = mapped_column(MONEY)
    fifo_pnl_realized: Mapped[Decimal | None] = mapped_column(MONEY)
    mtm_pnl: Mapped[Decimal | None] = mapped_column(MONEY)
    open_close: Mapped[str | None] = mapped_column(String(8))
    currency: Mapped[str] = mapped_column(String(8), index=True)
    fx_rate_to_base: Mapped[Decimal | None] = mapped_column(RATE)
    transaction_type: Mapped[str | None] = mapped_column(String(16))
    exchange: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(String(256))
    asset_class: Mapped[str | None] = mapped_column(String(16), index=True)
    multiplier: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    raw_xml: Mapped[str | None] = mapped_column(Text)
    inserted_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    instrument: Mapped[Instrument] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_trades_conid_date", "conid", "trade_date"),
        Index("ix_trades_account_date", "account_id", "trade_date"),
    )


class CashTransaction(Base):
    __tablename__ = "cash_transactions"

    transaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.account_id"), index=True
    )
    conid: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("instruments.conid"), index=True
    )
    symbol: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(48), index=True)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(8))
    fx_rate_to_base: Mapped[Decimal | None] = mapped_column(RATE)
    event_datetime: Mapped[datetime] = mapped_column(DateTime, index=True)
    settle_date: Mapped[date | None] = mapped_column(Date, index=True)
    report_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(String(512))
    country: Mapped[str | None] = mapped_column(String(2), index=True)
    related_trade_id: Mapped[str | None] = mapped_column(String(64))
    code: Mapped[str | None] = mapped_column(String(32))
    asset_class: Mapped[str | None] = mapped_column(String(16))
    raw_xml: Mapped[str | None] = mapped_column(Text)
    inserted_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_cash_tx_type_date", "type", "settle_date"),
        Index("ix_cash_tx_conid_date", "conid", "settle_date"),
    )


class PositionEod(Base):
    __tablename__ = "positions_eod"

    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    conid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    symbol: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[Decimal] = mapped_column(MONEY)
    mark_price: Mapped[Decimal | None] = mapped_column(MONEY)
    position_value: Mapped[Decimal | None] = mapped_column(MONEY)
    cost_basis_money: Mapped[Decimal | None] = mapped_column(MONEY)
    cost_basis_price: Mapped[Decimal | None] = mapped_column(MONEY)
    fifo_pnl_unrealized: Mapped[Decimal | None] = mapped_column(MONEY)
    pct_of_nav: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    currency: Mapped[str | None] = mapped_column(String(8))
    fx_rate_to_base: Mapped[Decimal | None] = mapped_column(RATE)
    multiplier: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    asset_class: Mapped[str | None] = mapped_column(String(16))
    holding_period_dt: Mapped[datetime | None] = mapped_column(DateTime)
    open_dt: Mapped[datetime | None] = mapped_column(DateTime)
    raw_xml: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_positions_account_date", "account_id", "snapshot_date"),
        Index("ix_positions_conid_date", "conid", "snapshot_date"),
    )


class CorporateAction(Base):
    __tablename__ = "corporate_actions"

    ca_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    conid: Mapped[int | None] = mapped_column(Integer, index=True)
    symbol: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str | None] = mapped_column(String(8))
    description: Mapped[str | None] = mapped_column(String(512))
    event_datetime: Mapped[datetime] = mapped_column(DateTime, index=True)
    quantity: Mapped[Decimal | None] = mapped_column(MONEY)
    proceeds: Mapped[Decimal | None] = mapped_column(MONEY)
    value: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str | None] = mapped_column(String(8))
    raw_xml: Mapped[str | None] = mapped_column(Text)


class CashReportRow(Base):
    __tablename__ = "cash_report"

    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    currency: Mapped[str] = mapped_column(String(8), primary_key=True)
    ending_cash: Mapped[Decimal | None] = mapped_column(MONEY)
    ending_settled_cash: Mapped[Decimal | None] = mapped_column(MONEY)
    dividends: Mapped[Decimal | None] = mapped_column(MONEY)
    withholding_tax: Mapped[Decimal | None] = mapped_column(MONEY)
    broker_interest: Mapped[Decimal | None] = mapped_column(MONEY)
    commissions: Mapped[Decimal | None] = mapped_column(MONEY)
    fees_receivables: Mapped[Decimal | None] = mapped_column(MONEY)
    deposits_withdrawals: Mapped[Decimal | None] = mapped_column(MONEY)


class NavDaily(Base):
    __tablename__ = "nav_daily"

    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    base_currency: Mapped[str | None] = mapped_column(String(8))
    nav_total: Mapped[Decimal | None] = mapped_column(MONEY)
    cash: Mapped[Decimal | None] = mapped_column(MONEY)
    stock: Mapped[Decimal | None] = mapped_column(MONEY)
    options: Mapped[Decimal | None] = mapped_column(MONEY)
    bonds: Mapped[Decimal | None] = mapped_column(MONEY)
    funds: Mapped[Decimal | None] = mapped_column(MONEY)
    dividends_receivable: Mapped[Decimal | None] = mapped_column(MONEY)
    interest_receivable: Mapped[Decimal | None] = mapped_column(MONEY)
    accrued_dividends: Mapped[Decimal | None] = mapped_column(MONEY)
    realized_pnl: Mapped[Decimal | None] = mapped_column(MONEY)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(MONEY)
    external_cashflow: Mapped[Decimal | None] = mapped_column(MONEY, default=0)


class ChangeInNavDaily(Base):
    """`<ChangeInNAV>` rows — daily performance attribution.

    One row per (account, day) holding the full waterfall from startingValue
    to endingValue (MTM, realized, dividends, withholding, interest, fees,
    commissions, transactionTax, fxTranslation, deposits/withdrawals, TWR).
    """

    __tablename__ = "change_in_nav_daily"

    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    currency: Mapped[str] = mapped_column(String(8))
    starting_value: Mapped[Decimal | None] = mapped_column(MONEY)
    ending_value: Mapped[Decimal | None] = mapped_column(MONEY)
    mtm: Mapped[Decimal | None] = mapped_column(MONEY)
    realized: Mapped[Decimal | None] = mapped_column(MONEY)
    change_in_unrealized: Mapped[Decimal | None] = mapped_column(MONEY)
    dividends: Mapped[Decimal | None] = mapped_column(MONEY)
    withholding_tax: Mapped[Decimal | None] = mapped_column(MONEY)
    change_in_dividend_accruals: Mapped[Decimal | None] = mapped_column(MONEY)
    interest: Mapped[Decimal | None] = mapped_column(MONEY)
    change_in_interest_accruals: Mapped[Decimal | None] = mapped_column(MONEY)
    commissions: Mapped[Decimal | None] = mapped_column(MONEY)
    broker_fees: Mapped[Decimal | None] = mapped_column(MONEY)
    other_fees: Mapped[Decimal | None] = mapped_column(MONEY)
    transaction_tax: Mapped[Decimal | None] = mapped_column(MONEY)
    deposits_withdrawals: Mapped[Decimal | None] = mapped_column(MONEY)
    fx_translation: Mapped[Decimal | None] = mapped_column(MONEY)
    twr: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    raw_xml: Mapped[str | None] = mapped_column(Text)
    # Added 2026-10 (B02/B53): in-kind transfers are an external flow and
    # salesTax a waterfall component; both were dropped by the parser before.
    asset_transfers: Mapped[Decimal | None] = mapped_column(MONEY)
    sales_tax: Mapped[Decimal | None] = mapped_column(MONEY)


class FifoPerformanceUnderlying(Base):
    """`<FIFOPerformanceSummaryUnderlying>` rows — per-symbol FIFO P&L breakdown.

    One row per (snapshot_date, account, conid). `snapshot_date` is the row's
    own `reportDate`. Semantics (verified against the 2026-05-14 Q1 export):
    - ``total_realized_pnl`` / ``realized_st_*`` / ``realized_lt_*`` are
      **daily** values reflecting closes that day. Sum over a period for total
      realized in that period.
    - ``total_unrealized_pnl`` / ``unrealized_*`` are **snapshots** at
      snapshot_date. Latest row = current state. Differences across two dates
      give the period MTM move on the open position.
    """

    __tablename__ = "fifo_performance_underlying"

    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    conid: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.conid"), primary_key=True
    )
    symbol: Mapped[str | None] = mapped_column(String(64))
    asset_class: Mapped[str | None] = mapped_column(String(16))
    cost_adj: Mapped[Decimal | None] = mapped_column(MONEY)
    realized_st_profit: Mapped[Decimal | None] = mapped_column(MONEY)
    realized_st_loss: Mapped[Decimal | None] = mapped_column(MONEY)
    realized_lt_profit: Mapped[Decimal | None] = mapped_column(MONEY)
    realized_lt_loss: Mapped[Decimal | None] = mapped_column(MONEY)
    total_realized_pnl: Mapped[Decimal | None] = mapped_column(MONEY)
    unrealized_profit: Mapped[Decimal | None] = mapped_column(MONEY)
    unrealized_loss: Mapped[Decimal | None] = mapped_column(MONEY)
    unrealized_st_profit: Mapped[Decimal | None] = mapped_column(MONEY)
    unrealized_st_loss: Mapped[Decimal | None] = mapped_column(MONEY)
    unrealized_lt_profit: Mapped[Decimal | None] = mapped_column(MONEY)
    unrealized_lt_loss: Mapped[Decimal | None] = mapped_column(MONEY)
    total_unrealized_pnl: Mapped[Decimal | None] = mapped_column(MONEY)
    total_fifo_pnl: Mapped[Decimal | None] = mapped_column(MONEY)
    transferred_pnl: Mapped[Decimal | None] = mapped_column(MONEY)
    raw_xml: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_fifo_perf_account_date", "account_id", "snapshot_date"),
        Index("ix_fifo_perf_conid_date", "conid", "snapshot_date"),
    )


class MtmPerformanceUnderlying(Base):
    """`<MTMPerformanceSummaryUnderlying>` rows — per-symbol DAILY mark-to-
    market P&L. One row per (snapshot_date, account, conid). All P&L fields
    are daily increments — sum over period for the period total.
    """

    __tablename__ = "mtm_performance_underlying"

    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    conid: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.conid"), primary_key=True
    )
    symbol: Mapped[str | None] = mapped_column(String(64))
    asset_class: Mapped[str | None] = mapped_column(String(16))
    prev_close_quantity: Mapped[Decimal | None] = mapped_column(MONEY)
    prev_close_price: Mapped[Decimal | None] = mapped_column(MONEY)
    close_quantity: Mapped[Decimal | None] = mapped_column(MONEY)
    close_price: Mapped[Decimal | None] = mapped_column(MONEY)
    transaction_mtm: Mapped[Decimal | None] = mapped_column(MONEY)
    prior_open_mtm: Mapped[Decimal | None] = mapped_column(MONEY)
    commissions: Mapped[Decimal | None] = mapped_column(MONEY)
    other: Mapped[Decimal | None] = mapped_column(MONEY)
    other_with_accruals: Mapped[Decimal | None] = mapped_column(MONEY)
    total: Mapped[Decimal | None] = mapped_column(MONEY)
    total_with_accruals: Mapped[Decimal | None] = mapped_column(MONEY)
    raw_xml: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_mtm_perf_account_date", "account_id", "snapshot_date"),
        Index("ix_mtm_perf_conid_date", "conid", "snapshot_date"),
    )


class OptionExerciseAssignment(Base):
    """`<OptionEAE>` rows — Exercise / Assignment / Expiration events.

    Wired defensively for when the first option assignment lands; the table
    is small and easy to backfill from raw_xml if attributes change.
    """

    __tablename__ = "option_eae"

    eae_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    conid: Mapped[int | None] = mapped_column(Integer, index=True)
    symbol: Mapped[str | None] = mapped_column(String(64))
    underlying_conid: Mapped[int | None] = mapped_column(Integer)
    underlying_symbol: Mapped[str | None] = mapped_column(String(64))
    transaction_type: Mapped[str | None] = mapped_column(String(48))
    event_date: Mapped[date | None] = mapped_column(Date, index=True)
    quantity: Mapped[Decimal | None] = mapped_column(MONEY)
    multiplier: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    trade_price: Mapped[Decimal | None] = mapped_column(MONEY)
    proceeds: Mapped[Decimal | None] = mapped_column(MONEY)
    commission: Mapped[Decimal | None] = mapped_column(MONEY)
    realized_pnl: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str | None] = mapped_column(String(8))
    raw_xml: Mapped[str | None] = mapped_column(Text)


class Transfer(Base):
    """`<Transfers>` rows — ACAT / position transfers in or out.

    Defensive stub — empty in the current sample; wired so the first transfer
    event after this lands cleanly without a schema migration.
    """

    __tablename__ = "transfers"

    transfer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    conid: Mapped[int | None] = mapped_column(Integer, index=True)
    symbol: Mapped[str | None] = mapped_column(String(64))
    direction: Mapped[str | None] = mapped_column(String(8))
    transfer_type: Mapped[str | None] = mapped_column(String(48))
    event_date: Mapped[date | None] = mapped_column(Date, index=True)
    quantity: Mapped[Decimal | None] = mapped_column(MONEY)
    position_value: Mapped[Decimal | None] = mapped_column(MONEY)
    cash_value: Mapped[Decimal | None] = mapped_column(MONEY)
    cost_basis: Mapped[Decimal | None] = mapped_column(MONEY)
    delivering_broker: Mapped[str | None] = mapped_column(String(128))
    currency: Mapped[str | None] = mapped_column(String(8))
    raw_xml: Mapped[str | None] = mapped_column(Text)


class DividendAccrual(Base):
    __tablename__ = "dividend_accruals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    conid: Mapped[int | None] = mapped_column(Integer, index=True)
    symbol: Mapped[str | None] = mapped_column(String(64))
    ex_date: Mapped[date | None] = mapped_column(Date, index=True)
    pay_date: Mapped[date | None] = mapped_column(Date, index=True)
    quantity: Mapped[Decimal | None] = mapped_column(MONEY)
    gross_rate: Mapped[Decimal | None] = mapped_column(MONEY)
    gross_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    net_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    tax: Mapped[Decimal | None] = mapped_column(MONEY)
    fee: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str | None] = mapped_column(String(8))
    code: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "account_id", "conid", "ex_date", "pay_date", "gross_amount",
            name="uq_dividend_accrual",
        ),
    )


class InterestAccrual(Base):
    __tablename__ = "interest_accruals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    accrual_date: Mapped[date] = mapped_column(Date, index=True)
    currency: Mapped[str] = mapped_column(String(8))
    interest_type: Mapped[str | None] = mapped_column(String(48))
    interest: Mapped[Decimal | None] = mapped_column(MONEY)
    ending_balance: Mapped[Decimal | None] = mapped_column(MONEY)
    raw_xml: Mapped[str | None] = mapped_column(Text)

    # One row per (account, day, currency). `interest_type` is no longer part of
    # the key (Flex never sends it; a NULL in a unique key never conflicts).
    # Databases created before 2026-10 keep the old `uq_interest_accrual`
    # constraint; `_ensure_ingest_schema` adds this index to them.
    __table_args__ = (
        Index(
            "uq_interest_accrual_day", "account_id", "accrual_date", "currency",
            unique=True,
        ),
    )


class TransactionFee(Base):
    __tablename__ = "transaction_fees"

    # Synthetic deterministic key — IBKR doesn't expose a stable id for these
    # (TransactionFees / TransactionTaxes). Built in the loader from
    # (account_id, trade_id, report_date, tax_amount, tax_description, conid,
    # quantity) so reruns don't duplicate and two equal-amount fees on
    # different stocks stay distinct (B35). Changing this formula needs
    # `cli rebootstrap-fees` (synthetic-id rule).
    fee_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    trade_id: Mapped[str | None] = mapped_column(String(64), index=True)
    symbol: Mapped[str | None] = mapped_column(String(64))
    conid: Mapped[int | None] = mapped_column(Integer)
    event_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    report_date: Mapped[date | None] = mapped_column(Date, index=True)
    quantity: Mapped[Decimal | None] = mapped_column(MONEY)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY)
    tax_description: Mapped[str | None] = mapped_column(String(128))
    currency: Mapped[str | None] = mapped_column(String(8))
    raw_xml: Mapped[str | None] = mapped_column(Text)


class TradeExecution(Base):
    """`<TradeConfirm levelOfDetail="EXECUTION">` rows — per-fill execution detail.

    Sourced from the **Trade Confirmation Flex Query** (TCF), NOT Activity Flex.
    TC provides per-execution granularity that Activity collapses to one row
    per order. Idempotency key is ``exec_id`` (IBKR's per-fill unique ID).

    Joins:
      * `trades` via `trade_id` (1:N — one Activity Flex trade may map to many
        TC executions when an order filled in multiple slices).
      * `instruments` via `conid` (FK).

    Notes:
      * `fxRateToBase` is NOT exposed in TC — convert via `fx_rates` JOIN on
        `(trade_date, currency)`.
      * `open_close` is sourced from the `code` attribute (`O` / `C`), not
        from `openCloseIndicator` which TC doesn't emit.
    """

    __tablename__ = "trade_executions"

    exec_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.account_id"), index=True
    )
    conid: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.conid"), index=True
    )
    symbol: Mapped[str | None] = mapped_column(String(64), index=True)
    asset_class: Mapped[str | None] = mapped_column(String(16))

    trade_id: Mapped[str | None] = mapped_column(String(64), index=True)
    order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    brokerage_order_id: Mapped[str | None] = mapped_column(String(64))
    ext_exec_id: Mapped[str | None] = mapped_column(String(64))
    block_id: Mapped[str | None] = mapped_column(String(64))

    order_time: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    execution_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    settle_date: Mapped[date | None] = mapped_column(Date)
    report_date: Mapped[date | None] = mapped_column(Date)

    buy_sell: Mapped[str | None] = mapped_column(String(8))
    quantity: Mapped[Decimal] = mapped_column(MONEY)
    price: Mapped[Decimal] = mapped_column(MONEY)
    amount: Mapped[Decimal | None] = mapped_column(MONEY)
    proceeds: Mapped[Decimal | None] = mapped_column(MONEY)
    net_cash: Mapped[Decimal | None] = mapped_column(MONEY)
    net_cash_with_billable: Mapped[Decimal | None] = mapped_column(MONEY)

    # Commission breakdown — TC's unique value. Each is in commission_currency.
    commission: Mapped[Decimal | None] = mapped_column(MONEY)
    broker_execution_commission: Mapped[Decimal | None] = mapped_column(MONEY)
    broker_clearing_commission: Mapped[Decimal | None] = mapped_column(MONEY)
    third_party_execution_commission: Mapped[Decimal | None] = mapped_column(MONEY)
    third_party_clearing_commission: Mapped[Decimal | None] = mapped_column(MONEY)
    third_party_regulatory_commission: Mapped[Decimal | None] = mapped_column(MONEY)
    other_commission: Mapped[Decimal | None] = mapped_column(MONEY)
    commission_currency: Mapped[str | None] = mapped_column(String(8))

    tax: Mapped[Decimal | None] = mapped_column(MONEY)
    sales_tax: Mapped[Decimal | None] = mapped_column(MONEY)
    other_tax: Mapped[Decimal | None] = mapped_column(MONEY)
    trade_charge: Mapped[Decimal | None] = mapped_column(MONEY)

    currency: Mapped[str] = mapped_column(String(8), index=True)
    multiplier: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    exchange: Mapped[str | None] = mapped_column(String(32))
    transaction_type: Mapped[str | None] = mapped_column(String(32))
    order_type: Mapped[str | None] = mapped_column(String(16))
    open_close: Mapped[str | None] = mapped_column(String(4))  # from `code` (O/C)
    is_api_order: Mapped[str | None] = mapped_column(String(4))
    trader_id: Mapped[str | None] = mapped_column(String(32))
    order_reference: Mapped[str | None] = mapped_column(String(64))

    orig_trade_id: Mapped[str | None] = mapped_column(String(64))
    orig_trade_price: Mapped[Decimal | None] = mapped_column(MONEY)
    orig_trade_date: Mapped[date | None] = mapped_column(Date)

    raw_xml: Mapped[str | None] = mapped_column(Text)
    inserted_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    instrument: Mapped[Instrument] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_exec_trade_date_time", "execution_time"),
        Index("ix_exec_account_date", "account_id", "trade_date"),
        Index("ix_exec_conid_date", "conid", "trade_date"),
        Index("ix_exec_order_id", "order_id"),
        Index("ix_exec_trade_id", "trade_id"),
    )


# -------------------------------------------------------------- Pipeline meta
class FlexRun(Base):
    __tablename__ = "flex_runs"
    # staleness / catch-up / retention all filter on (status, fetched_at).
    __table_args__ = (Index("ix_flex_runs_status_fetched_at", "status", "fetched_at"),)

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_id: Mapped[str | None] = mapped_column(String(64))
    reference_code: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    from_date: Mapped[date | None] = mapped_column(Date)
    to_date: Mapped[date | None] = mapped_column(Date)
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0)
    rows_skipped_dup: Mapped[int] = mapped_column(Integer, default=0)
    # Rows overwritten by `upsert_replace` sections (a re-pull that changes
    # values inserts 0 rows but is not a no-op).
    rows_replaced: Mapped[int] = mapped_column(Integer, default=0)
    # success | error | stale (success, but the statement ends before the last
    # business day - 1: truncated query period).
    status: Mapped[str | None] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(String(64))


class NotificationState(Base):
    """Tiny key/value watermark for the Telegram notifier.

    Holds e.g. ``last_notified_date`` so the daily digest is not re-sent
    when the pipeline runs twice for the same day (mirrors the old Google
    Sheet's ``lastDivReport`` marker). Created automatically by
    ``init_db()`` — no migration framework in this project.
    """

    __tablename__ = "notification_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class AppConfig(Base):
    """Generic key/value application config — one JSON document per key.

    Holds user-editable overrides that layer on top of the env-derived
    ``Settings`` defaults. Today the only key is ``'notifications'`` — the
    sparse notification-config document edited from the dashboard Settings
    page (per-message enable / sections / cadence). ``value`` is a JSON string
    (``Text``, Postgres-portable). Created by ``init_db()`` — no migration
    framework in this project.
    """

    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class PriceAlertState(Base):
    """Re-arm state for a price-alert rule (keyed by symbol|op|price).

    ``triggered`` flips True when the rule crosses (and a Telegram alert is
    sent) and back to False when the price returns to the non-crossed side,
    so the same threshold is not re-notified on every poll. Created by
    ``init_db()`` — no migration framework in this project.
    """

    __tablename__ = "price_alert_state"

    alert_key: Mapped[str] = mapped_column(String(96), primary_key=True)
    triggered: Mapped[bool] = mapped_column(default=False)
    last_price: Mapped[float | None] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class Watchlist(Base):
    __tablename__ = "watchlist"

    list_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    conid: Mapped[int | None] = mapped_column(Integer)
    exchange: Mapped[str | None] = mapped_column(String(32))
    currency: Mapped[str | None] = mapped_column(String(8))
    notes: Mapped[str | None] = mapped_column(String(256))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )
