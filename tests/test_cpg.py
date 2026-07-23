"""CPG client + import_from_ibkr tests using respx to mock the CPG HTTP."""

from __future__ import annotations

import json

import httpx
import pytest

from ibkr_backend.watchlist import import_from_ibkr
from ibkr_backend.watchlist.cpg import CpgClient, CpgUnavailable


class StubTransport(httpx.MockTransport):
    """Tiny scripted transport — records requests, returns canned JSON."""

    def __init__(self, handlers):
        self.calls: list[httpx.Request] = []
        super().__init__(self._dispatch)
        self._handlers = handlers

    def _dispatch(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        for matcher, response in self._handlers:
            if matcher(request):
                return response
        return httpx.Response(404, json={"error": "no handler"})


def _resp(payload, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(payload),
                          headers={"content-type": "application/json"})


def _path(p: str):
    return lambda r: r.url.path.endswith(p)


def _make_client(handlers) -> CpgClient:
    transport = StubTransport(handlers)
    c = CpgClient()
    c._http = httpx.Client(
        base_url="https://localhost:5000/v1/api",
        transport=transport,
        headers={"User-Agent": "ibkr-dashboard/test", "Accept": "application/json"},
    )
    c._transport = transport  # type: ignore[attr-defined]
    return c


def test_authenticated_true():
    c = _make_client([
        (_path("/iserver/auth/status"), _resp({"authenticated": True})),
    ])
    assert c.authenticated() is True


def test_authenticated_false():
    c = _make_client([
        (_path("/iserver/auth/status"), _resp({"authenticated": False})),
    ])
    assert c.authenticated() is False


def test_list_watchlists_user_lists_shape():
    payload = {"data": {"user_lists": [
        {"id": "1234", "name": "Tech", "type": "wl"},
        {"id": "5678", "name": "Dividends", "type": "wl"},
    ]}}
    c = _make_client([
        (_path("/iserver/watchlists"), _resp(payload)),
    ])
    out = c.list_watchlists()
    assert {w.id for w in out} == {"1234", "5678"}
    assert {w.name for w in out} == {"Tech", "Dividends"}


def test_get_watchlist_rows_with_blank_marker():
    payload = {
        "id": "1234",
        "name": "Tech",
        "instruments": [
            {"C": "265598", "ticker": "AAPL", "name": "Apple", "assetClass": "STK"},
            {"H": "1"},
            {"C": "8314", "ticker": "IBM", "assetClass": "STK"},
        ],
    }
    c = _make_client([
        (_path("/iserver/watchlist"), _resp(payload)),
    ])
    header, rows = c.get_watchlist("1234")
    assert header.id == "1234"
    assert len(rows) == 3
    assert rows[0].conid == 265598 and rows[0].symbol == "AAPL"
    assert rows[1].is_blank is True
    assert rows[2].conid == 8314


def test_unauthenticated_request_raises():
    c = _make_client([
        (_path("/iserver/watchlists"), httpx.Response(401)),
    ])
    with pytest.raises(CpgUnavailable):
        c.list_watchlists()


def test_rate_limited_raises():
    c = _make_client([
        (_path("/iserver/watchlists"), httpx.Response(429)),
    ])
    with pytest.raises(CpgUnavailable):
        c.list_watchlists()


def test_import_from_ibkr_db_only(monkeypatch):
    """End-to-end import with mocked CPG returning two watchlists."""

    transport = StubTransport([
        (_path("/iserver/auth/status"), _resp({"authenticated": True})),
        (_path("/iserver/watchlists"),
         _resp({"data": {"user_lists": [
             {"id": "L1", "name": "Core Tech"},
             {"id": "L2", "name": "Dividends EU"},
         ]}})),
        (lambda r: r.url.path.endswith("/iserver/watchlist") and r.url.params.get("id") == "L1",
         _resp({"id": "L1", "name": "Core Tech",
                "instruments": [{"C": "265598", "ticker": "AAPL"},
                                {"C": "272093", "ticker": "MSFT"}]})),
        (lambda r: r.url.path.endswith("/iserver/watchlist") and r.url.params.get("id") == "L2",
         _resp({"id": "L2", "name": "Dividends EU",
                "instruments": [{"C": "100000", "ticker": "VWCE"}]})),
    ])

    # Patch CpgClient to use our stub transport.
    real_init = CpgClient.__init__

    def _patched_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        self._http.close()
        self._http = httpx.Client(
            base_url="https://localhost:5000/v1/api",
            transport=transport,
            headers={"User-Agent": "ibkr-dashboard/test"},
        )

    monkeypatch.setattr(CpgClient, "__init__", _patched_init)

    result = import_from_ibkr(merge_yaml=False)
    assert result.error is None
    assert result.lists_imported == 2
    assert result.rows_imported == 3
    assert any(name.startswith("ibkr:core_tech") for name in result.list_names)


def test_import_from_ibkr_handles_unauthenticated(monkeypatch):
    transport = StubTransport([
        (_path("/iserver/auth/status"), _resp({"authenticated": False})),
    ])
    real_init = CpgClient.__init__

    def _patched_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        self._http.close()
        self._http = httpx.Client(
            base_url="https://localhost:5000/v1/api", transport=transport,
        )

    monkeypatch.setattr(CpgClient, "__init__", _patched_init)
    result = import_from_ibkr(merge_yaml=False)
    assert result.error is not None
    assert "authenticated" in result.error.lower()
