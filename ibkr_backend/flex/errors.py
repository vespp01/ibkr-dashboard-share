"""Flex Web Service error codes documented by IBKR.

When the GetStatement response is still a `<FlexStatementResponse>` envelope
instead of the actual statement, the error code in `<ErrorCode>` is one of:

* 1003  Statement not available
* 1004  Statement is incomplete (poll again)
* 1005  Settlement data not ready
* 1006  FIFO P/L not ready
* 1007  MTM P/L not ready
* 1008  MTM and FIFO P/L not ready
* 1009  Server is under heavy load
* 1010  Legacy queries no longer supported (terminal — do not retry)
* 1011  Service is unavailable (terminal in the short term)
* 1012  Token has expired (terminal)
* 1018  Too many requests (back off)
* 1019  Statement generation in progress (poll again)

SendRequest can additionally fail with:

* 604   Invalid request or unable to validate (terminal — token expired,
        query ID deactivated, or output format is not XML)
"""

from __future__ import annotations

# Codes that mean "not ready yet — keep polling GetStatement".
RETRYABLE_CODES: frozenset[str] = frozenset(
    {"1003", "1004", "1005", "1006", "1007", "1008", "1009", "1018", "1019"}
)

# Codes that mean "stop trying — the request is broken or the token is dead".
TERMINAL_CODES: frozenset[str] = frozenset(
    {"604", "1010", "1011", "1012", "1020", "1021"}
)

DESCRIPTIONS: dict[str, str] = {
    "604": (
        "Invalid request or unable to validate — token expired (90-day cycle), "
        "query ID deactivated, or output format is not XML; regenerate the "
        "token in IBKR Client Portal"
    ),
    "1003": "Statement not available",
    "1004": "Statement is incomplete",
    "1005": "Settlement data not ready",
    "1006": "FIFO P/L not ready",
    "1007": "MTM P/L not ready",
    "1008": "MTM and FIFO P/L not ready",
    "1009": "Server is under heavy load",
    "1010": "Legacy queries no longer supported",
    "1011": "Service is unavailable",
    "1012": "Token has expired",
    "1018": "Rate limited — too many requests",
    "1019": "Statement generation in progress",
    "1020": "Invalid request",
    "1021": "Statement generation failed",
}


def describe(code: str) -> str:
    return DESCRIPTIONS.get(code, f"Unknown Flex error code {code}")
