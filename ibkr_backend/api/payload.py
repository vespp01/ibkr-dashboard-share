"""Response-shaping helpers for the HTTP layer.

The analytics functions stay complete (other jobs read their rows); the routes
drop the keys no consumer reads. Two groups:

* ``ACCOUNT_KEYS`` - the single-account app never needs the account identifier
  in a payload served on the tailnet.
* ``NULL_ONLY_KEYS`` - columns Flex never fills (they stay in the DB).
"""

from __future__ import annotations

from typing import Any

ACCOUNT_KEYS = frozenset({"account_id"})
NULL_ONLY_KEYS = frozenset({"sector", "industry", "issuer", "open_dt", "holding_period_dt"})


def strip_keys(payload: Any, keys: frozenset[str] | set[str]) -> Any:
    """Return ``payload`` with every dict key in ``keys`` removed, at any depth."""
    if isinstance(payload, dict):
        return {k: strip_keys(v, keys) for k, v in payload.items() if k not in keys}
    if isinstance(payload, list):
        return [strip_keys(v, keys) for v in payload]
    return payload
