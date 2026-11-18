"""P1 surface cuts: removed routes stay removed, payloads carry no dead keys."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ibkr_backend.api.main import create_app
from ibkr_backend.api.payload import strip_keys
from ibkr_backend.db import session_scope
from ibkr_backend.flex.parser import parse_flex_xml
from ibkr_backend.loaders.pipeline import load_statement

REMOVED = [
    ("GET", "/portfolio/unrealized"),
    ("GET", "/portfolio/cash"),
    ("GET", "/portfolio/pnl-by-asset-class"),
    ("GET", "/commissions/transaction-fees"),
    ("GET", "/interest/summary"),
    ("GET", "/watchlist"),
    ("POST", "/watchlist/sync"),
    ("POST", "/watchlist/import-from-ibkr"),
]


@pytest.fixture()
def client(sample_flex_xml):
    with session_scope() as session:
        for stmt in parse_flex_xml(sample_flex_xml):
            load_statement(session, stmt)
    return TestClient(create_app())


def _keys(obj) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _keys(v)
    return out


@pytest.mark.parametrize(("method", "path"), REMOVED)
def test_removed_routes_are_gone(client, method, path):
    assert client.request(method, path).status_code in (404, 405)


def test_openapi_has_no_removed_paths(client):
    paths = client.get("/openapi.json").json()["paths"]
    for _, path in REMOVED:
        assert path not in paths


@pytest.mark.parametrize("path", [
    "/portfolio/positions",
    "/portfolio/open-shorts",
    "/portfolio/symbol-attribution?period=all",
    "/trades?period=all",
    "/trades/executions?period=all",
])
def test_payloads_carry_no_account_or_null_only_keys(client, path):
    res = client.get(path)
    assert res.status_code == 200, res.text
    banned = {"account_id", "sector", "industry", "issuer", "open_dt", "holding_period_dt"}
    assert not (_keys(res.json()) & banned)


def test_positions_payload_is_not_empty_after_strip(client):
    rows = client.get("/portfolio/positions").json()
    assert rows and "symbol" in rows[0] and "position_value_base" in rows[0]


def test_strip_keys_recurses_without_mutating_input():
    src = {"account_id": "U1", "rows": [{"account_id": "U1", "symbol": "A"}], "n": 1}
    out = strip_keys(src, {"account_id"})
    assert out == {"rows": [{"symbol": "A"}], "n": 1}
    assert src["account_id"] == "U1"
