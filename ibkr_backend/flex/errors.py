"""Flex Web Service error codes documented by IBKR.

When the GetStatement response is still a `<FlexStatementResponse>` envelope
instead of the actual statement, the error code in `<ErrorCode>` is one of:

* 1001  Statement could not be generated at this time (try again shortly)
* 1003  Statement not available
* 1004  Statement is incomplete (poll again)
* 1005  Settlement data not ready
* 1006  FIFO P/L not ready
* 1007  MTM P/L not ready
* 1008  MTM and FIFO P/L not ready
* 1009  Server is under heavy load
* 1010  Legacy queries no longer supported (terminal — do not retry)
* 1011  Service account is inactive (terminal)
* 1012  Token has expired (terminal)
* 1018  Too many requests (back off)
* 1019  Statement generation in progress (poll again)
* 1021  Statement could not be retrieved at this time (try again shortly)
* 1025  Invalid token (seen after the token's Activation Period ended, next to
        1012: 57x 1012 and 64x 1025 in the 2026-06 expiry; terminal)

SendRequest can additionally fail with:

* 604   Invalid request or unable to validate (terminal — token expired at
        the end of its Activation Period, query ID deactivated, output format
        is not XML, or a period override sent to a fixed-period query)
"""

from __future__ import annotations

# Codes that mean "not ready yet — keep polling GetStatement" (and, when
# SendRequest itself answers one, re-send it; see FlexClient).
RETRYABLE_CODES: frozenset[str] = frozenset(
    {"1001", "1003", "1004", "1005", "1006", "1007", "1008", "1009", "1018", "1019", "1021"}
)

# Codes that mean "stop trying — the request is broken or the token is dead".
TERMINAL_CODES: frozenset[str] = frozenset(
    {"604", "1010", "1011", "1012", "1020", "1025"}
)

# Codes that mean the TOKEN itself is dead. Retrying cannot help until the token
# value changes, so the scheduler suspends its pulls after one of these and
# resumes when `IB_FLEX_TOKEN` differs. 604 is excluded on purpose: it is also
# a deactivated query or a period override, which a fix in the portal resolves
# without any token change.
TOKEN_DEAD_CODES: frozenset[str] = frozenset({"1012", "1025"})

DESCRIPTIONS: dict[str, str] = {
    "604": (
        "Invalid request or unable to validate — token expired (Activation "
        "Period end, see IB_FLEX_TOKEN_EXPIRY), query ID deactivated, output "
        "format not XML, or a period override sent to a fixed-period query; "
        "check token_expiry in /pipeline/status"
    ),
    "1001": "Statement could not be generated at this time",
    "1003": "Statement not available",
    "1004": "Statement is incomplete",
    "1005": "Settlement data not ready",
    "1006": "FIFO P/L not ready",
    "1007": "MTM P/L not ready",
    "1008": "MTM and FIFO P/L not ready",
    "1009": "Server is under heavy load",
    "1010": "Legacy queries no longer supported",
    "1011": "Service account is inactive",
    "1012": "Token has expired",
    "1018": "Rate limited — too many requests",
    "1019": "Statement generation in progress",
    "1020": "Invalid request",
    "1021": "Statement could not be retrieved at this time",
    "1025": "Invalid token — expired or revoked; generate a new Flex token in the Client Portal",
}


def describe(code: str) -> str:
    return DESCRIPTIONS.get(code, f"Unknown Flex error code {code}")
