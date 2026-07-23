"""OCC symbol parsing + human-form fallback.

Pure parsing logic — no DB, no SQLAlchemy, no application state. Used to
turn IBKR Flex's option symbology into a normalised `OptionContract`.

OCC symbology (OSI standard, 21 chars):
    AAAAAA YYMMDD R SSSSSSSS
    └─ root, left-justified, space-padded
            └─ expiry YYMMDD (assume 20YY for all 2-digit years emitted by IB)
                   └─ right: C or P
                     └─ strike × 1000, zero-padded to 8 digits

Falls back to parsing IBKR's human description (`AAAA DDMMMYY strike P|C`)
when the OSI form is absent — IBKR Flex sometimes emits the human form
in `Trade.symbol` with an empty `Instrument.description`, so the fallback
handles both documented formats (see `enrich_option` 3-tier chain).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


_OSI = re.compile(
    r"^(?P<root>.{1,6})\s+(?P<expiry>\d{6})(?P<right>[CP])(?P<strike>\d{8})$"
)
_HUMAN = re.compile(
    r"^(?P<root>[A-Z][A-Z0-9.\-]*)\s+"
    r"(?P<d>\d{1,2})(?P<mon>[A-Z]{3})(?P<yy>\d{2})\s+"
    r"(?P<strike>\d+(?:\.\d+)?)\s+"
    r"(?P<right>[PC])\b",
    re.IGNORECASE,
)
_MON = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


@dataclass(slots=True)
class OptionContract:
    underlying: str | None
    expiry: date | None
    right: str | None  # 'C' | 'P'
    strike: float | None

    def to_dict(self) -> dict:
        return {
            "underlying": self.underlying,
            "expiry": self.expiry.isoformat() if self.expiry else None,
            "right": self.right,
            "strike": self.strike,
            "dte_today": (self.expiry - date.today()).days if self.expiry else None,
        }


_EMPTY = OptionContract(None, None, None, None)


def parse_occ_symbol(symbol: str | None) -> OptionContract:
    if not symbol:
        return _EMPTY
    m = _OSI.match(symbol.strip())
    if not m:
        return _EMPTY
    yy = int(m["expiry"][:2])
    mm = int(m["expiry"][2:4])
    dd = int(m["expiry"][4:6])
    try:
        exp = date(2000 + yy, mm, dd)
    except ValueError:
        exp = None
    return OptionContract(
        underlying=m["root"].strip().upper() or None,
        expiry=exp,
        right=m["right"].upper(),
        strike=int(m["strike"]) / 1000.0,
    )


def parse_option_description(desc: str | None) -> OptionContract:
    if not desc:
        return _EMPTY
    m = _HUMAN.match(desc.strip())
    if not m:
        return _EMPTY
    mon = _MON.get(m["mon"].upper())
    if not mon:
        return _EMPTY
    try:
        exp = date(2000 + int(m["yy"]), mon, int(m["d"]))
    except ValueError:
        exp = None
    try:
        strike = float(m["strike"])
    except ValueError:
        strike = None
    return OptionContract(
        underlying=m["root"].upper(),
        expiry=exp,
        right=m["right"].upper(),
        strike=strike,
    )


def enrich_option(symbol: str | None, description: str | None) -> OptionContract:
    """Try OSI on symbol first, then human-form on symbol (IBKR Flex
    occasionally emits the human description in the symbol field instead),
    finally human-form on description. Always returns a value.
    """
    c = parse_occ_symbol(symbol)
    if c.expiry and c.right and c.strike is not None:
        return c
    s_human = parse_option_description(symbol)
    h = parse_option_description(description)
    return OptionContract(
        underlying=c.underlying or s_human.underlying or h.underlying,
        expiry=c.expiry or s_human.expiry or h.expiry,
        right=c.right or s_human.right or h.right,
        strike=(
            c.strike if c.strike is not None
            else s_human.strike if s_human.strike is not None
            else h.strike
        ),
    )


# IBKR Flex emits dividend / withholding CashTransaction rows with both
# `symbol` and `conid` NULL — the ticker only appears as the leading token
# of `description` ("TICKER(ISIN) Cash Dividend ..."). Without parsing it,
# every per-symbol roll-up of dividends silently sums to zero on real
# Flex data. `_` allowed for IBKR's rights-instrument suffix (e.g.
# "VIS_r.MC"); `.` for class-share / exchange suffixes (e.g. "BRK.B").
# IGNORECASE so the lowercase rights suffix passes through.
_DIV_DESC_SYMBOL = re.compile(r"^([A-Z][A-Z0-9._\-]*)\s*\(", re.IGNORECASE)


def dividend_symbol_from_description(desc: str | None) -> str | None:
    if not desc:
        return None
    m = _DIV_DESC_SYMBOL.match(desc.strip())
    return m.group(1) if m else None


__all__ = [
    "OptionContract",
    "dividend_symbol_from_description",
    "enrich_option",
    "parse_occ_symbol",
    "parse_option_description",
]
