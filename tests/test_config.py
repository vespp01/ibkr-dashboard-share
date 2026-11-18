"""Settings + CORS configuration tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ibkr_backend import config as cfg


def _fresh_app(monkeypatch, cors_origins: str):
    monkeypatch.setenv("CORS_ORIGINS", cors_origins)
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    from ibkr_backend.api.main import create_app

    return create_app()


def test_empty_cors_origins_does_not_reflect_arbitrary_origin(monkeypatch):
    """CORS_ORIGINS="" must NOT fall back to "*" (wildcard + credentials
    reflects any Origin). It must fall back to the safe localhost list."""
    app = _fresh_app(monkeypatch, "")
    client = TestClient(app)

    evil = client.get("/health", headers={"Origin": "https://evil.example"})
    assert evil.status_code == 200
    assert evil.headers.get("access-control-allow-origin") != "https://evil.example"
    assert evil.headers.get("access-control-allow-origin") != "*"

    ok = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_explicit_cors_origins_respected(monkeypatch):
    app = _fresh_app(monkeypatch, "http://example.test:5173")
    client = TestClient(app)

    ok = client.get("/health", headers={"Origin": "http://example.test:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://example.test:5173"

    other = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert other.headers.get("access-control-allow-origin") is None


def test_base_currency_default_is_chf(monkeypatch):
    """The account's base currency is CHF — the dead 'EUR'
    default contradicted .env.example and reality."""
    monkeypatch.delenv("BASE_CURRENCY", raising=False)
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    assert cfg.get_settings().base_currency == "CHF"
