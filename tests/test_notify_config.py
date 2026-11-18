"""Effective notification-config resolver tests (real SQLite via the autouse fixture)."""

from __future__ import annotations

from ibkr_backend import config as cfg
from ibkr_backend.notify import config_store as store


def test_configurable_jobs_agree_with_message_specs():
    """The scheduler's _CONFIGURABLE_JOBS and config_store.MESSAGE_SPECS are two
    hand-kept lists; drift silently breaks live rescheduling. Pin their agreement."""
    from ibkr_backend.jobs import scheduler as sch

    for job in sch._CONFIGURABLE_JOBS:
        spec = store.spec_for(job["message_id"])
        assert spec is not None, f"no MESSAGE_SPEC for job {job['message_id']}"
        assert spec["kind"] == job["kind"], job["message_id"]
        assert spec.get("cron_attr") == job["cron_attr"], job["message_id"]


def test_default_config_has_all_messages():
    c = store.default_config_from_settings(cfg.get_settings())
    assert set(c["messages"]) == {s["id"] for s in store.MESSAGE_SPECS}
    assert c["messages"]["daily_digest"]["sections"] == [
        "dividends",
        "operations",
        "commissions",
    ]


def test_default_margin_fx_reverse_maps_env_cron():
    # env default margin_fx_cron="0 8 * * mon" → weekly, weekday 0 (Monday), 08:00.
    # (B45: the old "0 8 * * 1" default fired on Tuesday under APScheduler's 0=Mon.)
    cad = store.default_config_from_settings(cfg.get_settings())["messages"]["margin_fx"]["cadence"]
    assert cad["freq"] == "weekly"
    assert cad["weekday"] == 0
    assert cad["time"] == "08:00"


def test_margin_usage_default_is_weekly_with_alert_bands():
    m = store.default_config_from_settings(cfg.get_settings())["messages"]["margin_usage"]
    assert m["cadence"]["freq"] == "weekly"
    assert m["alert_bands"] is True


def test_resolve_reflects_saved_override():
    saved = store.save_notification_config(
        {"messages": {"margin_fx": {"cadence": {"freq": "monthly", "day_of_month": 3, "time": "09:00"}}}}
    )
    assert saved["messages"]["margin_fx"]["cadence"]["freq"] == "monthly"
    resolved = store.resolve_notification_config()
    assert resolved["messages"]["margin_fx"]["cadence"]["day_of_month"] == 3
    # unrelated messages keep defaults
    assert resolved["messages"]["daily_digest"]["enabled"] is True


def test_partial_patches_merge_not_replace():
    store.save_notification_config({"messages": {"daily_digest": {"enabled": False}}})
    store.save_notification_config({"messages": {"performance_pulse": {"enabled": False}}})
    r = store.resolve_notification_config()
    assert r["messages"]["daily_digest"]["enabled"] is False
    assert r["messages"]["performance_pulse"]["enabled"] is False


def test_sections_clamped_to_allowed_set():
    store.save_notification_config(
        {"messages": {"daily_digest": {"sections": ["dividends", "bogus"]}}}
    )
    r = store.resolve_notification_config()
    assert r["messages"]["daily_digest"]["sections"] == ["dividends"]


def test_malformed_json_falls_open_to_defaults():
    from ibkr_backend.db import session_scope
    from ibkr_backend.db.models import AppConfig

    with session_scope() as s:
        s.add(AppConfig(key=store.CONFIG_KEY, value="{not valid json"))
    r = store.resolve_notification_config()
    assert r["messages"]["daily_digest"]["enabled"] is True


def test_config_schema_marks_interval_and_overlay():
    schema = store.config_schema(cfg.get_settings())
    by_id = {m["id"]: m for m in schema["messages"]}
    assert by_id["price_alerts"]["schedule_kind"] == "interval"
    assert by_id["price_alerts"]["requires_live_overlay"] is True
    assert by_id["price_alerts"]["cadence_fields"] == []
    assert "interval_cron" in by_id["price_alerts"]
    assert by_id["margin_fx"]["schedule_kind"] == "cron"
    assert "time" in by_id["margin_fx"]["cadence_fields"]
    assert by_id["daily_digest"]["supports_sections"] is True
