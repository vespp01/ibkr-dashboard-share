"""Telegram Bot API transport — a thin `sendMessage` wrapper.

Single-purpose: push a text message to the configured chat. Credentials
live in `.env` (`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`), never in the
repo. Failures are returned/raised, never swallowed here — the caller
(the daily notifier) decides whether a send error should abort anything
(it must not: a notification failure can never break the Flex pipeline).
"""

from __future__ import annotations

import logging
import time

import httpx

from ..config import Settings, get_settings

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/sendMessage"

# Transient statuses worth retrying — rate-limit + server errors. A 4xx other
# than 429 is a permanent client error (bad token, bad chat id) and must not be
# retried.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Telegram rejects a sendMessage body over 4096 chars with a NON-retryable 400.
# A single oversize digest would then fail forever (and, worse, re-send its
# smaller siblings every catch-up run) — so we split before sending.
_MAX_LEN = 4096
# Cap on how long we honour a 429 `retry_after` for a single attempt.
_MAX_RETRY_AFTER = 30.0


class TelegramError(RuntimeError):
    """Sanitised transport failure.

    The raw ``httpx`` exceptions carry the request URL, and the Bot API URL holds
    the bot token (``/bot<token>/sendMessage``). The bot is shared with two other
    projects, so nothing that reaches a log line, an API response or a Telegram
    alert may include it: transport errors are re-raised as this type with the
    status or the exception class name only.
    """


def is_configured(settings: Settings | None = None) -> bool:
    """True when both the bot token and the chat id are set."""
    s = settings or get_settings()
    return bool(s.telegram_bot_token and s.telegram_chat_id)


def _split_message(text: str, limit: int = _MAX_LEN) -> list[str]:
    """Split `text` into <=`limit`-char chunks on blank-line block boundaries.

    Blocks (``\\n\\n``-separated) are never broken across chunks unless a single
    block itself exceeds `limit`, in which case that block is hard-sliced.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        if len(block) > limit:  # pathological single block — hard-slice it
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(block), limit):
                chunks.append(block[i : i + limit])
            continue
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) > limit:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_message(
    text: str,
    *,
    settings: Settings | None = None,
    parse_mode: str = "HTML",
    timeout: float = 15.0,
    max_attempts: int = 3,
    backoff_base: float = 0.5,
) -> bool:
    """Send `text` to the configured chat. Returns True on Telegram `ok`.

    Messages over Telegram's 4096-char limit are split on block boundaries and
    sent sequentially (all chunks must succeed for a True result). Each chunk
    retries transient failures — network/timeout errors and HTTP 429/5xx — up to
    `max_attempts` times (honouring a 429 ``retry_after``, else exponential
    backoff). A permanent 4xx (bad token / chat id) raises immediately.

    Raises `RuntimeError` if Telegram is not configured. If a chunk exhausts its
    attempts (or hits a permanent 4xx) a token-free `TelegramError` is raised —
    the caller is expected to catch it so a notification never aborts the
    pipeline (and, for the digest, so the watermark is not advanced past an
    undelivered day).
    """
    s = settings or get_settings()
    if not is_configured(s):
        raise RuntimeError(
            "Telegram not configured: set TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_CHAT_ID in .env."
        )
    for chunk in _split_message(text):
        if not _send_one(
            chunk,
            settings=s,
            parse_mode=parse_mode,
            timeout=timeout,
            max_attempts=max_attempts,
            backoff_base=backoff_base,
        ):
            return False  # Telegram not-ok on a chunk — stop; caller retries
    return True


def _send_one(
    text: str,
    *,
    settings: Settings,
    parse_mode: str,
    timeout: float,
    max_attempts: int,
    backoff_base: float,
) -> bool:
    """Send one <=4096-char chunk with the retry loop. Raises on exhausted retries."""
    s = settings
    url = _API.format(token=s.telegram_bot_token)
    data = {
        "chat_id": s.telegram_chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": "true",
    }
    last_exc: str | None = None  # token-free description of the last failure
    for attempt in range(max_attempts):
        retry_after: float | None = None
        try:
            resp = httpx.post(url, data=data, timeout=timeout)
        except httpx.RequestError as exc:  # network / timeout — retryable
            last_exc = f"Telegram request failed: {type(exc).__name__}"
        else:
            if resp.status_code in _RETRYABLE_STATUS:
                last_exc = f"Telegram HTTP {resp.status_code}"
                if resp.status_code == 429:  # honour Telegram's own back-off hint
                    try:
                        retry_after = resp.json().get("parameters", {}).get("retry_after")
                    except (ValueError, AttributeError):
                        retry_after = None
            else:
                try:
                    resp.raise_for_status()  # permanent 4xx → raises, no retry
                except httpx.HTTPStatusError:
                    # The original exception text embeds the bot URL (token).
                    raise TelegramError(f"Telegram HTTP {resp.status_code}") from None
                payload = resp.json()
                if not payload.get("ok"):
                    log.warning(
                        "Telegram sendMessage not ok: %s", payload.get("description")
                    )
                    return False
                return True
        if attempt < max_attempts - 1:
            delay = (
                min(float(retry_after), _MAX_RETRY_AFTER)
                if retry_after
                else backoff_base * (2 ** attempt)
            )
            log.info(
                "Telegram send attempt %d/%d failed (%s); retrying in %.1fs.",
                attempt + 1, max_attempts, last_exc, delay,
            )
            time.sleep(delay)
    assert last_exc is not None  # loop ran at least once
    raise TelegramError(last_exc)


__all__ = ["TelegramError", "is_configured", "send_message"]
