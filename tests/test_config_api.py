"""API tests for /config/notifications (GET config+schema, POST patch, test-send)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ibkr_backend import config as cfg
from ibkr_backend.api.main import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def _client_no_telegram(monkeypatch) -> TestClient:
    # Force Telegram off deterministically — the mounted .env may carry real
    # creds locally (absent in CI).
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    return TestClient(create_app())


def test_get_returns_config_schema_and_capabilities(monkeypatch):
    body = _client_no_telegram(monkeypatch).get("/config/notifications").json()
    assert body["config"]["messages"]["daily_digest"]["enabled"] is True
    ids = {m["id"] for m in body["schema"]["messages"]}
    assert "margin_usage" in ids
    assert body["capabilities"]["telegram_configured"] is False


def test_post_persists_and_roundtrips():
    c = _client()
    r = c.post(
        "/config/notifications",
        json={"messages": {"margin_fx": {"cadence": {"freq": "monthly", "day_of_month": 5, "time": "07:00"}}}},
    )
    assert r.status_code == 200
    assert r.json()["config"]["messages"]["margin_fx"]["cadence"]["freq"] == "monthly"
    g = c.get("/config/notifications").json()
    assert g["config"]["messages"]["margin_fx"]["cadence"]["day_of_month"] == 5


def test_post_invalid_freq_is_422():
    r = _client().post("/config/notifications", json={"messages": {"margin_fx": {"cadence": {"freq": "hourly"}}}})
    assert r.status_code == 422


def test_post_invalid_time_is_422():
    r = _client().post("/config/notifications", json={"messages": {"margin_fx": {"cadence": {"time": "25:00"}}}})
    assert r.status_code == 422


def test_post_unknown_message_id_is_422():
    r = _client().post("/config/notifications", json={"messages": {"bogus": {"enabled": False}}})
    assert r.status_code == 422


def test_post_extra_field_is_422():
    r = _client().post("/config/notifications", json={"messages": {"margin_fx": {"nope": 1}}})
    assert r.status_code == 422


def test_test_endpoint_400_without_telegram(monkeypatch):
    r = _client_no_telegram(monkeypatch).post("/config/notifications/test")
    assert r.status_code == 400


def test_test_endpoint_error_is_generic_and_token_free(monkeypatch):
    """B41: a Telegram failure must not echo exception text (it could carry the bot URL)."""
    from ibkr_backend.api.routes import config as config_route

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "999:LEAKY-TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]

    def boom(*a, **k):
        raise RuntimeError("https://api.telegram.org/bot999:LEAKY-TOKEN/sendMessage 401")

    monkeypatch.setattr(config_route, "send_message", boom)
    r = TestClient(create_app()).post("/config/notifications/test")
    assert r.status_code == 200
    assert r.json()["sent"] is False
    assert "LEAKY-TOKEN" not in r.text
