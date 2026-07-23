"""API routers."""

from .commissions import router as commissions_router
from .dividends import router as dividends_router
from .interest import router as interest_router
from .options import router as options_router
from .performance import router as performance_router
from .pipeline import router as pipeline_router
from .portfolio import router as portfolio_router
from .trades import router as trades_router
from .watchlist import router as watchlist_router

__all__ = [
    "commissions_router",
    "dividends_router",
    "interest_router",
    "options_router",
    "performance_router",
    "pipeline_router",
    "portfolio_router",
    "trades_router",
    "watchlist_router",
]
