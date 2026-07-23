"""Watchlist module — YAML source of truth + IBKR Client Portal Gateway sync.

Two complementary paths:

1. YAML (`watchlists.yaml`) is the version-controlled, declarative source of
   truth. Synced to the DB via :func:`sync_yaml_to_db`.
2. Client Portal Gateway (CPG) lets us import whatever the user has saved in
   TWS / Mobile / Web. See :mod:`ibkr_backend.watchlist.cpg` and
   :func:`import_from_ibkr`.

Optional: ib_async live overlay attaches last/bid/ask to each entry, see
:func:`enrich_with_live`.
"""

from .cpg import CpgClient, CpgUnavailable, IbkrWatchlist, IbkrWatchlistRow
from .enrich import enrich_with_live
from .ibkr_import import ImportResult, import_from_ibkr
from .store import WatchlistEntry, load_yaml, save_yaml, sync_yaml_to_db

__all__ = [
    "CpgClient",
    "CpgUnavailable",
    "IbkrWatchlist",
    "IbkrWatchlistRow",
    "ImportResult",
    "WatchlistEntry",
    "enrich_with_live",
    "import_from_ibkr",
    "load_yaml",
    "save_yaml",
    "sync_yaml_to_db",
]
