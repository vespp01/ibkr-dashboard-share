"""Friendly cadence model for notification scheduling.

A *cadence* says how often a notification fires, in a shape the dashboard
Settings UI can render with buttons only. Two consumers:

* Jobs with their own APScheduler cron (staleness, price alerts, live margin,
  margin-fx review) — the cadence compiles to a crontab string fed to
  ``CronTrigger.from_crontab`` and the job is rescheduled live.
* Messages chained after the daily Flex pull (digest, pulse, token-expiry, EOD
  margin, margin-usage) — they have no independent cron, so the cadence acts as
  a *send-filter* on the daily event via :func:`cadence_allows`.

Weekday convention: ``0=Monday .. 6=Sunday``. This matches BOTH
``date.weekday()`` and APScheduler 3.x ``CronTrigger.from_crontab`` — verified
empirically that from_crontab treats the day-of-week field as 0=Mon..6=Sun (NOT
the standard-cron 0=Sun; that off-by-one made the legacy ``0 8 * * 1`` default
fire on Tuesday and ``* * * * 1-5`` on Tue-Sat). Compiled crons therefore use
day NAMES (``mon``..``sun``), which are unambiguous in every cron dialect, and
:func:`cron_to_cadence` reads names as well as digits.

``Cadence`` is a plain dict::

    {"freq": "off"|"daily"|"weekly"|"biweekly"|"monthly",
     "weekday": 0-6|None,        # weekly / biweekly
     "day_of_month": 1-28|None,  # monthly (capped at 28 so it always occurs)
     "time": "HH:MM"|None}       # own-cron jobs; ignored by chained messages
"""

from __future__ import annotations

import re
from datetime import date

FREQUENCIES = ("off", "daily", "weekly", "biweekly", "monthly")

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# Index == canonical weekday (0=Monday), the names APScheduler/cron accept.
WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _coerce_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_int(token: str) -> bool:
    return bool(re.fullmatch(r"\d+", token or ""))


def normalize_cadence(raw) -> dict:
    """Coerce an arbitrary dict into a valid cadence, filling required fields."""
    raw = raw if isinstance(raw, dict) else {}
    freq = str(raw.get("freq", "")).strip().lower()
    if freq not in FREQUENCIES:
        freq = "daily"

    weekday = _coerce_int(raw.get("weekday"))
    if weekday is not None:
        weekday = max(0, min(6, weekday))

    dom = _coerce_int(raw.get("day_of_month"))
    if dom is not None:
        dom = max(1, min(28, dom))

    time_str = raw.get("time")
    if not (isinstance(time_str, str) and _TIME_RE.match(time_str)):
        time_str = None

    if freq in ("weekly", "biweekly") and weekday is None:
        weekday = 0
    if freq == "monthly" and dom is None:
        dom = 1

    return {"freq": freq, "weekday": weekday, "day_of_month": dom, "time": time_str}


def _time_fields(time_str: str | None, fallback_cron: str | None) -> tuple[str, str]:
    """Return (minute, hour) cron tokens from an HH:MM time or a fallback cron."""
    if time_str and _TIME_RE.match(time_str):
        hh, mm = time_str.split(":")
        return str(int(mm)), str(int(hh))
    if fallback_cron:
        parts = fallback_cron.split()
        if len(parts) >= 2 and _is_int(parts[0]) and _is_int(parts[1]):
            return parts[0], parts[1]
    return "0", "8"


def cadence_to_cron(cadence, fallback_cron: str | None = None) -> str | None:
    """Compile a cadence to a 5-field crontab string, or ``None`` when off.

    For own-cron jobs. ``biweekly`` compiles to the same weekly cron; the
    every-other-week parity is enforced by the caller via
    :func:`is_active_iso_week`.
    """
    c = normalize_cadence(cadence)
    if c["freq"] == "off":
        return None
    minute, hour = _time_fields(c["time"], fallback_cron)
    if c["freq"] == "daily":
        return f"{minute} {hour} * * *"
    if c["freq"] in ("weekly", "biweekly"):
        return f"{minute} {hour} * * {WEEKDAY_NAMES[c['weekday']]}"
    if c["freq"] == "monthly":
        return f"{minute} {hour} {c['day_of_month']} * *"
    return None


def is_active_iso_week(day: date) -> bool:
    """True on even ISO weeks — the parity gate for ``biweekly`` cadence."""
    return day.isocalendar()[1] % 2 == 0


def cadence_allows(cadence, today: date) -> bool:
    """Send-filter for chained messages: may this cadence fire on ``today``?"""
    c = normalize_cadence(cadence)
    freq = c["freq"]
    if freq == "off":
        return False
    if freq == "daily":
        return True
    if freq == "weekly":
        return today.weekday() == c["weekday"]
    if freq == "biweekly":
        return today.weekday() == c["weekday"] and is_active_iso_week(today)
    if freq == "monthly":
        return today.day == c["day_of_month"]
    return False


def _same_iso_week(a: date, b: date) -> bool:
    return a.isocalendar()[:2] == b.isocalendar()[:2]


def cadence_due(cadence, today: date, last_sent: date | None = None) -> bool:
    """Period-based send gate for chained messages (B44).

    ``cadence_allows`` matches the exact weekday / day-of-month, so a pull that
    failed on that day dropped the whole week's message (margin usage, week of
    2026-10-05). This gate asks instead "has this period's slot arrived and has
    the message not yet been sent in this ISO week / month?", so the next
    successful pull after a failed slot still delivers it. ``daily`` and ``off``
    behave as in ``cadence_allows``.
    """
    c = normalize_cadence(cadence)
    freq = c["freq"]
    if freq in ("off", "daily"):
        return freq == "daily"
    if freq in ("weekly", "biweekly"):
        if freq == "biweekly" and not is_active_iso_week(today):
            return False
        if today.weekday() < c["weekday"]:
            return False  # this week's slot has not arrived yet
        return last_sent is None or not _same_iso_week(last_sent, today)
    if freq == "monthly":
        if today.day < c["day_of_month"]:
            return False
        return last_sent is None or (last_sent.year, last_sent.month) != (today.year, today.month)
    return False


def cron_to_cadence(cron: str | None) -> dict:
    """Best-effort reverse map of a crontab string into a cadence.

    Used to seed the env-derived defaults. Interval-style crons (``*/15``,
    ``0 */6``) that can't be represented cleanly fall back to ``daily``; those
    messages are marked ``interval`` in the schema and their freq is ignored.
    """
    parts = str(cron or "").split()
    if len(parts) != 5:
        return normalize_cadence({"freq": "daily"})
    minute, hour, dom, _month, dow = parts
    time_str = None
    if _is_int(minute) and _is_int(hour):
        time_str = f"{int(hour):02d}:{int(minute):02d}"
    if dow.lower() in WEEKDAY_NAMES:
        return normalize_cadence(
            {"freq": "weekly", "weekday": WEEKDAY_NAMES.index(dow.lower()), "time": time_str}
        )
    if dow != "*" and _is_int(dow):
        return normalize_cadence({"freq": "weekly", "weekday": int(dow), "time": time_str})
    if dom != "*" and _is_int(dom):
        return normalize_cadence({"freq": "monthly", "day_of_month": int(dom), "time": time_str})
    return normalize_cadence({"freq": "daily", "time": time_str})
