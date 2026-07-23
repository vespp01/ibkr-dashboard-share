"""Watchlist YAML round-trip."""

from __future__ import annotations

from ibkr_backend.db import session_scope
from ibkr_backend.watchlist.store import (
    WatchlistEntry,
    list_db_entries,
    load_yaml,
    save_yaml,
    sync_yaml_to_db,
)


def test_yaml_roundtrip(tmp_path):
    path = tmp_path / "wl.yaml"
    entries = [
        WatchlistEntry("core", "AAPL", exchange="SMART", currency="USD"),
        WatchlistEntry("core", "MSFT", exchange="SMART", currency="USD"),
        WatchlistEntry("eu", "VWCE", exchange="SMART", currency="EUR"),
    ]
    save_yaml(entries, path)
    loaded = load_yaml(path)
    assert {(e.list_name, e.symbol) for e in loaded} == {
        ("core", "AAPL"),
        ("core", "MSFT"),
        ("eu", "VWCE"),
    }


def test_sync_yaml_to_db(tmp_path):
    path = tmp_path / "wl.yaml"
    save_yaml(
        [
            WatchlistEntry("core", "AAPL"),
            WatchlistEntry("core", "MSFT"),
        ],
        path,
    )
    with session_scope() as s:
        n = sync_yaml_to_db(s, path)
        assert n == 2
        rows = list_db_entries(s, list_name="core")
        assert {r.symbol for r in rows} == {"AAPL", "MSFT"}
