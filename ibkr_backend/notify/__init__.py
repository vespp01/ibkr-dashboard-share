"""Telegram notifications — daily dividend / operations / commissions digest.

Migrated from a legacy spreadsheet notification workflow. Off unless
`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` are set in `.env`.
"""

from __future__ import annotations

from .reports import (
    commissions_report,
    daily_notify,
    dividends_report,
    operations_report,
)
from .telegram import is_configured, send_message

__all__ = [
    "commissions_report",
    "daily_notify",
    "dividends_report",
    "is_configured",
    "operations_report",
    "send_message",
]
