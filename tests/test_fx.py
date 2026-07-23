"""FX cache behaviour — network failures must not poison the in-memory cache.

Regression tests for overnight finding #02: a transient Frankfurter outage used
to cache rate=1.0 permanently, mixing converted and unconverted NAVs in the
display-currency TWR series. Network I/O is simulated by monkeypatching
``urllib.request.urlopen`` (the fetch boundary); no DB is involved.
"""

from __future__ import annotations

import json
import urllib.error
from datetime import date

import pytest

from ibkr_backend.analytics import fx


class _FakeResponse:
    """Minimal context-manager stand-in for urlopen's response."""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _raise_network_error(req, timeout=None):
    raise urllib.error.URLError("simulated network failure")


@pytest.fixture(autouse=True)
def _clean_fx_cache():
    """Each test starts and ends with an empty module-level FX cache."""
    fx._cache.clear()
    yield
    fx._cache.clear()


# ---------------------------------------------------------------------------
# get_fx_rate (single date)
# ---------------------------------------------------------------------------


def test_get_fx_rate_failure_not_cached_then_retry_succeeds(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _raise_network_error)
    assert fx.get_fx_rate("2026-01-05", "CHF", "EUR") == 1.0
    # The degraded 1.0 must NOT be cached — the next call has to retry.
    assert ("2026-01-05", "CHF", "EUR") not in fx._cache

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse({"rates": {"EUR": 1.0567}}),
    )
    assert fx.get_fx_rate("2026-01-05", "CHF", "EUR") == 1.0567
    assert fx._cache[("2026-01-05", "CHF", "EUR")] == 1.0567


def test_get_fx_rate_success_is_cached_and_skips_network(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse({"rates": {"EUR": 1.05}}),
    )
    assert fx.get_fx_rate("2026-01-05", "CHF", "EUR") == 1.05

    # Real rates are immutable: served from cache even if the network is down.
    monkeypatch.setattr("urllib.request.urlopen", _raise_network_error)
    assert fx.get_fx_rate("2026-01-05", "CHF", "EUR") == 1.05


def test_get_fx_rate_same_currency_never_hits_network(monkeypatch):
    def _boom(req, timeout=None):
        raise AssertionError("network must not be called for same-currency")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    assert fx.get_fx_rate("2026-01-05", "CHF", "chf") == 1.0


# ---------------------------------------------------------------------------
# get_fx_rates (date range, batch fetch)
# ---------------------------------------------------------------------------


def test_get_fx_rates_failure_not_cached_then_retry_corrects_series(monkeypatch):
    d1, d2 = date(2026, 1, 5), date(2026, 1, 6)

    # 1st call: network down → degrade to 1.0 for this call only.
    monkeypatch.setattr("urllib.request.urlopen", _raise_network_error)
    assert fx.get_fx_rates([d1, d2], "CHF", "EUR") == {d1: 1.0, d2: 1.0}
    # Nothing poisoned: the failed dates are absent from the cache.
    assert ("2026-01-05", "CHF", "EUR") not in fx._cache
    assert ("2026-01-06", "CHF", "EUR") not in fx._cache

    # 2nd call: network back → real rates, series corrected.
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(
            {"rates": {"2026-01-05": {"EUR": 1.05}, "2026-01-06": {"EUR": 1.06}}}
        ),
    )
    assert fx.get_fx_rates([d1, d2], "CHF", "EUR") == {d1: 1.05, d2: 1.06}
    assert fx._cache[("2026-01-05", "CHF", "EUR")] == 1.05
    assert fx._cache[("2026-01-06", "CHF", "EUR")] == 1.06


def test_get_fx_rates_success_is_cached_and_skips_network(monkeypatch):
    d1, d2 = date(2026, 1, 5), date(2026, 1, 6)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(
            {"rates": {"2026-01-05": {"EUR": 1.05}, "2026-01-06": {"EUR": 1.06}}}
        ),
    )
    assert fx.get_fx_rates([d1, d2], "CHF", "EUR") == {d1: 1.05, d2: 1.06}

    monkeypatch.setattr("urllib.request.urlopen", _raise_network_error)
    assert fx.get_fx_rates([d1, d2], "CHF", "EUR") == {d1: 1.05, d2: 1.06}


def test_get_fx_rates_leading_date_fetch_failure_not_cached(monkeypatch):
    """A date before the range's first ECB day falls back to a single-date
    fetch; if THAT fails, the date degrades to 1.0 without being cached."""
    d1, d2 = date(2026, 1, 4), date(2026, 1, 5)

    def _urlopen(req, timeout=None):
        url = req.full_url
        if ".." in url:  # range request: only d2 has a rate
            return _FakeResponse({"rates": {"2026-01-05": {"EUR": 1.05}}})
        raise urllib.error.URLError("simulated single-date failure")

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    assert fx.get_fx_rates([d1, d2], "CHF", "EUR") == {d1: 1.0, d2: 1.05}
    assert ("2026-01-04", "CHF", "EUR") not in fx._cache
    assert fx._cache[("2026-01-05", "CHF", "EUR")] == 1.05

    # Retry with the single-date endpoint healthy → real rate replaces the 1.0.
    def _urlopen_ok(req, timeout=None):
        url = req.full_url
        if ".." in url:
            return _FakeResponse({"rates": {"2026-01-05": {"EUR": 1.05}}})
        return _FakeResponse({"rates": {"EUR": 1.04}})

    monkeypatch.setattr("urllib.request.urlopen", _urlopen_ok)
    assert fx.get_fx_rates([d1, d2], "CHF", "EUR") == {d1: 1.04, d2: 1.05}
    assert fx._cache[("2026-01-04", "CHF", "EUR")] == 1.04
