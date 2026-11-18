"""Effective notification config = env defaults + DB overrides.

The env-derived :class:`~ibkr_backend.config.Settings` stays the source of
*defaults* (its ``@lru_cache`` is never fought). User edits from the dashboard
Settings page are stored as a single JSON document in ``app_config`` (key
``'notifications'``) and layered on top at read time.

Resolution is cheap (one small DB read + dict build) and called only a handful
of times per day, so there is deliberately no cache — that keeps it correct
across the per-test engine rebuilds and avoids any shared-state / thread-safety
concern.

Canonical document (see :func:`resolve_notification_config`)::

    {"version": 1,
     "messages": {
        "daily_digest": {"enabled": true,
                         "sections": ["dividends","operations","commissions"],
                         "cadence": {...}},
        ...}}
"""

from __future__ import annotations

import json

from ..config import Settings, get_settings
from ..db import session_scope
from ..db.models import AppConfig
from .cadence import cron_to_cadence, normalize_cadence

CONFIG_KEY = "notifications"
CONFIG_VERSION = 1

# Frequencies a user can pick in the UI for cadence-driven messages.
SELECTABLE_FREQUENCIES = ("off", "daily", "weekly", "biweekly", "monthly")

# One entry per configurable notification. ``kind``:
#   chained  — fires after the daily pull; cadence is a send-filter (no time).
#   interval — own APScheduler cron at a fixed interval; only on/off is editable.
#   cron     — own APScheduler cron with a full cadence (weekly/biweekly/monthly).
MESSAGE_SPECS: tuple[dict, ...] = (
    {"id": "daily_digest", "kind": "chained",
     "sections": ("dividends", "operations", "commissions"),
     "default_cadence": {"freq": "daily"}, "enabled_attr": None,
     "requires_live_overlay": False},
    {"id": "performance_pulse", "kind": "chained", "sections": (),
     "default_cadence": {"freq": "daily"}, "enabled_attr": None,
     "requires_live_overlay": False},
    {"id": "token_expiry", "kind": "chained", "sections": (),
     "default_cadence": {"freq": "daily"}, "enabled_attr": None,
     "requires_live_overlay": False},
    {"id": "margin_eod", "kind": "chained", "sections": (),
     "default_cadence": {"freq": "daily"}, "enabled_attr": "margin_alerts_enabled",
     "requires_live_overlay": False},
    {"id": "margin_usage", "kind": "chained", "sections": (),
     "default_cadence": {"freq": "weekly", "weekday": 0}, "enabled_attr": None,
     "alert_bands": True, "requires_live_overlay": False},
    {"id": "staleness", "kind": "interval", "sections": (),
     "cron_attr": "staleness_watchdog_cron", "job_id": "staleness_watchdog",
     "enabled_attr": None, "requires_live_overlay": False},
    {"id": "price_alerts", "kind": "interval", "sections": (),
     "cron_attr": "price_alert_cron", "job_id": "price_alerts",
     "enabled_attr": None, "requires_live_overlay": True},
    {"id": "margin_live", "kind": "interval", "sections": (),
     "cron_attr": "margin_alert_cron", "job_id": "margin_live_alerts",
     "enabled_attr": "margin_alerts_enabled", "requires_live_overlay": True},
    {"id": "margin_fx", "kind": "cron", "sections": (),
     "cron_attr": "margin_fx_cron", "job_id": "margin_financing_notice",
     "enabled_attr": "margin_fx_enabled", "requires_live_overlay": False},
)

_SPEC_BY_ID = {spec["id"]: spec for spec in MESSAGE_SPECS}


def spec_for(message_id: str) -> dict | None:
    return _SPEC_BY_ID.get(message_id)


def _default_enabled(spec: dict, settings: Settings) -> bool:
    attr = spec.get("enabled_attr")
    return bool(getattr(settings, attr)) if attr else True


def _default_cadence(spec: dict, settings: Settings) -> dict:
    if spec.get("default_cadence"):
        return dict(spec["default_cadence"])
    cron_attr = spec.get("cron_attr")
    if cron_attr:
        return cron_to_cadence(getattr(settings, cron_attr))
    return {"freq": "daily"}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _normalize_config(raw: dict, settings: Settings) -> dict:
    src = raw.get("messages", {}) if isinstance(raw, dict) else {}
    if not isinstance(src, dict):
        src = {}
    messages: dict[str, dict] = {}
    for spec in MESSAGE_SPECS:
        mid = spec["id"]
        entry_raw = src.get(mid) if isinstance(src.get(mid), dict) else {}
        entry_raw = entry_raw or {}
        entry: dict = {
            "enabled": bool(entry_raw.get("enabled", _default_enabled(spec, settings))),
        }
        if spec["sections"]:
            allowed = list(spec["sections"])
            selected = entry_raw.get("sections", allowed)
            selected_set = set(selected) if isinstance(selected, (list, tuple, set)) else set()
            entry["sections"] = [s for s in allowed if s in selected_set]
        cadence_raw = entry_raw.get("cadence", _default_cadence(spec, settings))
        entry["cadence"] = normalize_cadence(cadence_raw)
        if spec.get("alert_bands"):
            entry["alert_bands"] = bool(entry_raw.get("alert_bands", True))
        messages[mid] = entry
    return {"version": CONFIG_VERSION, "messages": messages}


def default_config_from_settings(settings: Settings | None = None) -> dict:
    """The canonical config with every field at its env-derived default."""
    return _normalize_config({}, settings or get_settings())


def _load_overrides(session=None) -> dict:
    if session is None:
        with session_scope() as s:
            return _load_overrides(s)
    row = session.get(AppConfig, CONFIG_KEY)
    if row is None or not row.value:
        return {}
    try:
        data = json.loads(row.value)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_overrides(session, doc: dict) -> None:
    payload = json.dumps(doc, ensure_ascii=False, sort_keys=True)
    row = session.get(AppConfig, CONFIG_KEY)
    if row is None:
        session.add(AppConfig(key=CONFIG_KEY, value=payload))
    else:
        row.value = payload
    session.flush()


def resolve_notification_config(session=None, settings: Settings | None = None) -> dict:
    """Effective config: DB overrides normalized over env defaults."""
    s = settings or get_settings()
    overrides = _load_overrides(session)
    return _normalize_config(overrides, s)


def save_notification_config(patch: dict, session=None) -> dict:
    """Merge a (possibly partial) patch into the stored doc; return the effective config."""
    patch = patch if isinstance(patch, dict) else {}
    if session is not None:
        merged = _deep_merge(_load_overrides(session), patch)
        _write_overrides(session, merged)
        return resolve_notification_config(session)
    with session_scope() as s:
        merged = _deep_merge(_load_overrides(s), patch)
        _write_overrides(s, merged)
        result = resolve_notification_config(s)
    return result


def config_schema(settings: Settings | None = None) -> dict:
    """Language-neutral UI metadata: message list + per-message editable fields."""
    s = settings or get_settings()
    messages = []
    for spec in MESSAGE_SPECS:
        editable = ["enabled"]
        if spec["sections"]:
            editable.append("sections")
        if spec["kind"] in ("chained", "cron"):
            editable.append("cadence")
        if spec["kind"] == "chained":
            cadence_fields = ["freq", "weekday", "day_of_month"]
        elif spec["kind"] == "cron":
            cadence_fields = ["freq", "weekday", "day_of_month", "time"]
        else:
            cadence_fields = []
        item = {
            "id": spec["id"],
            "schedule_kind": spec["kind"],
            "requires_live_overlay": spec.get("requires_live_overlay", False),
            "supports_sections": bool(spec["sections"]),
            "sections": list(spec["sections"]),
            "supports_alert_bands": bool(spec.get("alert_bands")),
            "editable": editable,
            "cadence_fields": cadence_fields,
        }
        if spec["kind"] == "interval":
            item["interval_cron"] = getattr(s, spec["cron_attr"])
        messages.append(item)
    return {
        "version": CONFIG_VERSION,
        "frequencies": list(SELECTABLE_FREQUENCIES),
        "messages": messages,
    }


def message_enabled(config: dict, message_id: str) -> bool:
    return bool(config.get("messages", {}).get(message_id, {}).get("enabled"))


def message_cadence(config: dict, message_id: str) -> dict:
    return config.get("messages", {}).get(message_id, {}).get("cadence", {"freq": "daily"})
