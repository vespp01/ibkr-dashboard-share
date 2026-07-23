"""Watchlist enrichment via the ib_async live overlay.

If the IB Gateway is reachable, we add live last/bid/ask/close to each entry.
If not, we return whatever we have from the static YAML/DB. Callers should
not crash because of a missing Gateway.
"""

from __future__ import annotations

import logging
from typing import Any

from ..ibgw.live import LiveClient, LiveUnavailable
from .store import WatchlistEntry

log = logging.getLogger(__name__)


def enrich_with_live(entries: list[WatchlistEntry]) -> list[dict[str, Any]]:
    base = [
        {
            "list_name": e.list_name,
            "symbol": e.symbol,
            "conid": e.conid,
            "exchange": e.exchange,
            "currency": e.currency,
            "notes": e.notes,
            "last": None,
            "bid": None,
            "ask": None,
            "close": None,
            "live": False,
        }
        for e in entries
    ]
    try:
        with LiveClient() as live:
            quotes = live.snapshot_quotes(
                [
                    (e.symbol, e.exchange or "SMART", e.currency or "USD")
                    for e in entries
                ]
            )
    except LiveUnavailable as e:
        log.info("Live overlay unavailable: %s", e)
        return base

    by_symbol = {q.symbol: q for q in quotes}
    for row in base:
        q = by_symbol.get(row["symbol"])
        if q is None:
            continue
        row.update({
            "last": q.last,
            "bid": q.bid,
            "ask": q.ask,
            "close": q.close,
            "conid": row["conid"] or q.conid,
            "live": q.last is not None or q.bid is not None,
        })
    return base
