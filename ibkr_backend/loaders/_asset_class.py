"""Canonical asset_class normalization.

IBKR Flex emits two parallel naming conventions for the same asset class
depending on which section the row originated in:

  - Trades / OpenPositions          → 3-char Flex codes ("STK", "OPT", "CASH")
  - Statement summary / older feeds → descriptive labels ("Stocks",
                                       "Equity and Index Options")

Mixing them in the same column means `.where(asset_class == "OPT")` silently
excludes rows tagged with the descriptive label, understating P&L, position
counts, and breakdowns. Always pipe the raw Flex value through `canon()`
before writing to the DB so every row uses the short code.
"""

from __future__ import annotations

_ALIAS_TO_CODE: dict[str, str] = {
    "stocks": "STK",
    "stock": "STK",
    "equity and index options": "OPT",
    "equity or index options": "OPT",
    "options": "OPT",
    "futures": "FUT",
    "future": "FUT",
    "bonds": "BOND",
    "bond": "BOND",
    "forex": "CASH",
    "cash": "CASH",
    "funds": "FUND",
    "warrants": "WAR",
}


def canon(asset_class: str | None) -> str | None:
    """Return the canonical short code for an IBKR asset_class string.

    Pass-through for None/empty and for values already in short-code form.
    """
    if asset_class is None:
        return None
    raw = asset_class.strip()
    if not raw:
        return None
    return _ALIAS_TO_CODE.get(raw.lower(), raw)
