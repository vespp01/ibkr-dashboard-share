"""Telegram Bot API transport — a thin `sendMessage` wrapper.

Single-purpose: push a text message to the configured chat. Credentials
live in `.env` (`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`), never in the
repo. Failures are returned/raised, never swallowed here — the caller
(the daily notifier) decides whether a send error should abort anything
(it must not: a notification failure can never break the Flex pipeline).
"""

from __future__ import annotations

import logging

import httpx

from ..config import Settings, get_settings

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/sendMessage"


def is_configured(settings: Settings | None = None) -> bool:
    """True when both the bot token and the chat id are set."""
    s = settings or get_settings()
    return bool(s.telegram_bot_token and s.telegram_chat_id)


def send_message(
    text: str,
    *,
    settings: Settings | None = None,
    parse_mode: str = "HTML",
    timeout: float = 15.0,
) -> bool:
    """Send `text` to the configured chat. Returns True on Telegram `ok`.

    Raises `RuntimeError` if Telegram is not configured. HTTP/network
    errors propagate as `httpx` exceptions — the caller is expected to
    catch them so a notification never aborts the pipeline.
    """
    s = settings or get_settings()
    if not is_configured(s):
        raise RuntimeError(
            "Telegram not configured: set TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_CHAT_ID in .env."
        )
    resp = httpx.post(
        _API.format(token=s.telegram_bot_token),
        data={
            "chat_id": s.telegram_chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": "true",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("ok"):
        log.warning("Telegram sendMessage not ok: %s", payload.get("description"))
        return False
    return True


__all__ = ["is_configured", "send_message"]
