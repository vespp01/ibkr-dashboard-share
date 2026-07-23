"""Analytics layer — period filters, metrics, TWR/XIRR."""

from .commissions import (
    commissions_breakdown,
    commissions_summary,
    commissions_timeseries,
    transaction_fees_summary,
)
from .dividends_calc import (
    dividends_by_country,
    dividends_summary,
    dividends_timeseries,
    income_projection,
    withholding_tax_by_country,
)
from .executions import executions_list
from .filters import PeriodFilter, resolve_period
from .interest import interest_summary
from .margin import leverage_series, margin_interest_summary, margin_summary
from .metrics import symbol_attribution
from .overview import (
    cash_balances,
    change_in_nav_summary,
    change_in_nav_timeseries,
    portfolio_overview,
    total_return_on_cash,
)
from .positions import (
    open_shorts,
    pnl_by_asset_class,
    positions_snapshot,
    realized_pnl_timeseries,
    unrealized_pnl,
)
from .risk import alpha_vs_benchmark, risk_metrics, vs_benchmark
from .trades import trades_list
from .twr import twr_series
from .xirr import xirr_summary

__all__ = [
    "PeriodFilter",
    "resolve_period",
    "cash_balances",
    "change_in_nav_summary",
    "change_in_nav_timeseries",
    "commissions_breakdown",
    "commissions_summary",
    "commissions_timeseries",
    "dividends_by_country",
    "dividends_summary",
    "dividends_timeseries",
    "executions_list",
    "interest_summary",
    "income_projection",
    "alpha_vs_benchmark",
    "leverage_series",
    "margin_interest_summary",
    "margin_summary",
    "open_shorts",
    "risk_metrics",
    "pnl_by_asset_class",
    "portfolio_overview",
    "positions_snapshot",
    "realized_pnl_timeseries",
    "symbol_attribution",
    "total_return_on_cash",
    "trades_list",
    "transaction_fees_summary",
    "twr_series",
    "vs_benchmark",
    "unrealized_pnl",
    "withholding_tax_by_country",
    "xirr_summary",
]
