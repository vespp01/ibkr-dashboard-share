"""Read-only consumer of OptionsLab ``optionslab.management_signals.v1``.

OptionsLab owns the management rules (GTC capture, stop delta, premium stop)
and writes one local JSON file. This module validates that file and joins each
signal to the open option rows by underlying, right, expiry, strike and side
(a short row only takes a short-strike signal, a long row a long-strike one).
It never writes the file and never fails the caller: any problem yields no
signals and one log warning. An invalid position is skipped on its own.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError


log = logging.getLogger(__name__)

SCHEMA_VERSION = "optionslab.management_signals.v1"
STALE_AFTER = timedelta(hours=24)
STRIKE_TOLERANCE = 1e-6
MAX_SIGNALS_FILE_BYTES = 1024 * 1024

# Last failure reason logged, so an absent file warns once per state change
# instead of on every page load.
_last_logged_reason: str | None = None


class _Signal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    underlying: str
    right: Literal["P", "C"] | None = None
    expiry: date
    short_strike: float
    long_strike: float | None = None
    dte: int | None = None
    action: Literal["hold", "take_profit", "stop", "warning", "review", "unknown"]
    status_text: str | None = None
    capture_pct: float | None = None
    short_delta: float | None = None


class _SignalsFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    generated_at: datetime
    positions: list[_Signal] = []


def _unusable(reason: str, path: Path) -> None:
    global _last_logged_reason
    if reason != _last_logged_reason:
        log.warning("OptionsLab signals ignored (%s): %s", reason, path)
        _last_logged_reason = reason


def load_management_signals(path: Path) -> _SignalsFile | None:
    """Return the validated signals file, or None when it cannot be used."""

    global _last_logged_reason
    try:
        with path.open("rb") as signals_file:
            raw = signals_file.read(MAX_SIGNALS_FILE_BYTES + 1)
    except FileNotFoundError:
        _unusable("file_not_found", path)
        return None
    except OSError:
        _unusable("file_unreadable", path)
        return None
    if len(raw) > MAX_SIGNALS_FILE_BYTES:
        _unusable("file_too_large", path)
        return None
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        _unusable("invalid_json", path)
        return None
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        _unusable("wrong_schema_version", path)
        return None
    if data.get("status") != "ready":
        _unusable("status_not_ready", path)
        return None
    raw_positions = data.get("positions")
    try:
        signals = _SignalsFile.model_validate({**data, "positions": []})
    except ValidationError:
        _unusable("contract_validation_failed", path)
        return None
    skipped = 0
    for item in raw_positions if isinstance(raw_positions, list) else []:
        try:
            signals.positions.append(_Signal.model_validate(item))
        except ValidationError:
            skipped += 1
    if skipped:
        log.warning("OptionsLab signals: %d invalid position(s) skipped: %s", skipped, path)
    if signals.generated_at.tzinfo is None:
        _unusable("generated_at_without_timezone", path)
        return None
    _last_logged_reason = None
    return signals


def _match(row: dict, signals: _SignalsFile, generated_at: str, stale: bool) -> dict | None:
    underlying = (row.get("underlying") or "").strip().upper()
    strike = row.get("strike")
    if not underlying or strike is None:
        return None
    found: tuple[_Signal, str] | None = None
    for signal in signals.positions:
        if (
            signal.underlying.strip().upper() != underlying
            or signal.right is None
            or signal.right != row.get("right")
            or signal.expiry.isoformat() != row.get("expiry")
        ):
            continue
        if row.get("side") == "short" and abs(strike - signal.short_strike) <= STRIKE_TOLERANCE:
            found = (signal, "short")
            break
        if (
            found is None
            and row.get("side") == "long"
            and signal.long_strike is not None
            and abs(strike - signal.long_strike) <= STRIKE_TOLERANCE
        ):
            found = (signal, "long")
    if found is None:
        return None
    signal, leg = found
    return {
        "action": signal.action,
        "status_text": signal.status_text,
        "capture_pct": signal.capture_pct,
        "short_delta": signal.short_delta,
        "dte": signal.dte,
        "leg": leg,
        "generated_at": generated_at,
        "stale": stale,
    }


def attach_management_signals(
    rows: list[dict],
    path: Path,
    *,
    now: datetime | None = None,
) -> None:
    """Set ``row["optionslab"]`` on every open-option row; None when nothing matches.

    A short-strike match wins over a long-strike match. The file is read once.
    """

    signals = load_management_signals(path)
    if signals is None:
        for row in rows:
            row["optionslab"] = None
        return
    generated = signals.generated_at.astimezone(UTC)
    stale = (now or datetime.now(UTC)) - generated > STALE_AFTER
    generated_at = generated.isoformat().replace("+00:00", "Z")
    for row in rows:
        row["optionslab"] = _match(row, signals, generated_at, stale)
