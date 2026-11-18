"""API routers."""

from .commissions import router as commissions_router
from .config import router as config_router
from .dividends import router as dividends_router
from .integrations import router as integrations_router
from .options import router as options_router
from .performance import router as performance_router
from .pipeline import router as pipeline_router
from .portfolio import router as portfolio_router
from .tax import router as tax_router
from .trades import router as trades_router

__all__ = [
    "commissions_router",
    "config_router",
    "dividends_router",
    "integrations_router",
    "options_router",
    "performance_router",
    "pipeline_router",
    "portfolio_router",
    "tax_router",
    "trades_router",
]
