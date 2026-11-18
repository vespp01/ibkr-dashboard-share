"""Notification config — read + edit the dashboard-configurable Telegram messages.

`GET /config/notifications` returns the effective config (env defaults + DB
overrides) plus language-neutral UI metadata. `POST /config/notifications`
persists a (partial) patch and applies it live — rescheduling the own-cron jobs
via the APScheduler singleton. `POST /config/notifications/test` sends a Telegram
test message.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ...config import get_settings
from ...jobs import get_scheduler, sync_scheduler_jobs
from ...notify.config_store import (
    MESSAGE_SPECS,
    config_schema,
    resolve_notification_config,
    save_notification_config,
)
from ...notify.telegram import is_configured, send_message
from ..deps import SessionDep

log = logging.getLogger(__name__)

router = APIRouter()

_KNOWN_IDS = {spec["id"] for spec in MESSAGE_SPECS}


class CadenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    freq: Literal["off", "daily", "weekly", "biweekly", "monthly"] | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    sections: list[str] | None = None
    cadence: CadenceIn | None = None
    alert_bands: bool | None = None


class NotificationConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: dict[str, MessageIn] = Field(default_factory=dict)


@router.get("/notifications", summary="Effective notification config + UI schema")
def get_notifications(session: SessionDep):
    settings = get_settings()
    return {
        "config": resolve_notification_config(session),
        "schema": config_schema(settings),
        "capabilities": {
            "telegram_configured": is_configured(settings),
            "live_overlay_enabled": settings.enable_live_overlay,
            "scheduler_running": get_scheduler() is not None,
        },
    }


@router.post("/notifications", summary="Update notification config (persist + apply live)")
def update_notifications(patch: NotificationConfigPatch):
    unknown = set(patch.messages) - _KNOWN_IDS
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown message ids: {sorted(unknown)}")
    body = patch.model_dump(exclude_unset=True)
    config = save_notification_config(body)
    applied = sync_scheduler_jobs(config)
    return {"config": config, "applied": applied}


@router.post("/notifications/test", summary="Send a Telegram test message")
def test_notification():
    settings = get_settings()
    if not is_configured(settings):
        raise HTTPException(
            status_code=400,
            detail="Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).",
        )
    try:
        ok = send_message("✅ IBKR Dashboard — prueba de notificación.", settings=settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("Test notification failed: %s", exc)
        return {"sent": False, "error": "Telegram send failed (see backend log)"}
    return {"sent": bool(ok)}
