"""HTTP client for the IBKR Flex Web Service.

Two-step flow per IBKR docs:
    1. GET /SendRequest?t=<token>&q=<queryId>&v=3   → returns ReferenceCode
    2. GET /GetStatement?t=<token>&q=<ref>&v=3       → returns the statement

Optional `fd` / `td` (yyyymmdd) override the saved query's `From Date`/`To Date`
(maximum 365 days per request). `p=` overrides the period; date parameters take
precedence if both are passed.

The Flex Web Service is read-only (cannot place orders), supports outbound
HTTPS only (no port forwarding), and is refreshed once per day for Activity
queries. Treat the token as a password.
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET  # Element types only
from defusedxml.ElementTree import fromstring as _safe_fromstring  # parsing entry
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .errors import DESCRIPTIONS, RETRYABLE_CODES, TERMINAL_CODES, describe

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = (
    "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
)

# Cap for the growing delay applied when IBKR answers 1018 (rate limited).
RATE_LIMIT_MAX_DELAY_SECONDS = 60


class FlexError(RuntimeError):
    """Raised when Flex returns a terminal error or polling exceeds max wait."""

    def __init__(self, code: str | None, message: str):
        super().__init__(f"Flex {code or '???'}: {message}")
        self.code = code
        self.message = message


class FlexNotReady(RuntimeError):
    """Internal — used to drive httpx-level retry while GetStatement is 1004 etc."""


@dataclass(slots=True)
class FlexResponse:
    xml: bytes
    reference_code: str
    query_id: str


class FlexClient:
    """Minimal, production-shaped Flex Web Service client.

    Usage:
        client = FlexClient(token=..., user_agent="my-app/1.0")
        xml = client.fetch(query_id="123456")
    """

    def __init__(
        self,
        token: str,
        *,
        user_agent: str = "ibkr-dashboard/0.1",
        base_url: str = DEFAULT_BASE_URL,
        max_wait_seconds: int = 180,
        poll_interval_seconds: int = 5,
        timeout_seconds: int = 60,
    ) -> None:
        if not token:
            raise ValueError("Flex token is required")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._max_wait = max_wait_seconds
        self._poll_interval = poll_interval_seconds
        self._http = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/xml,text/xml,*/*"},
            timeout=timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "FlexClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- public
    def fetch(
        self,
        query_id: str,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        period: str | None = None,
    ) -> FlexResponse:
        """Run the full two-step flow and return the statement XML bytes.

        Dates are `yyyymmdd`. If both period and dates are passed, dates win.
        Window cannot exceed 365 days per IBKR's Flex API.

        The whole call stays inside one `max_wait_seconds` window (plus one HTTP
        timeout). SendRequest re-sends may use only its first half, so an
        accepted request always leaves GetStatement at least the other half.
        """
        start = time.monotonic()
        deadline = start + self._max_wait
        ref = self._send_request_until_accepted(
            query_id,
            from_date=from_date,
            to_date=to_date,
            period=period,
            deadline=start + self._max_wait / 2,
        )
        log.info("Flex SendRequest q=%s ref=%s", query_id, ref)
        xml = self._poll_get_statement(ref, deadline=deadline)
        return FlexResponse(xml=xml, reference_code=ref, query_id=query_id)

    # --------------------------------------------------------------- step 1
    def _send_request_until_accepted(
        self,
        query_id: str,
        *,
        from_date: str | None,
        to_date: str | None,
        period: str | None,
        deadline: float,
    ) -> str:
        """SendRequest, re-sent while IBKR answers a retryable code.

        IBKR can answer SendRequest itself with a "not ready" code, e.g.
        `1004 Statement is incomplete at this time` at the 06:30 pull
        (2026-09-18/19). Retryable codes are re-sent with a growing delay
        (x2, x4 for 1018 rate limiting, capped) until `deadline`; then the last
        error is raised. Terminal and unknown codes raise at once.
        """
        delay = self._poll_interval
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._send_request(
                    query_id, from_date=from_date, to_date=to_date, period=period
                )
            except FlexError as exc:
                remaining = deadline - time.monotonic()
                if exc.code not in RETRYABLE_CODES or remaining <= 0:
                    raise
                # After a 1018 only a full backoff helps: a shortened one would
                # re-send inside IBKR's rate limit. Other codes use what is left.
                if exc.code == "1018" and remaining < delay:
                    raise
                sleep_for = min(delay, remaining)  # never sleep past the deadline
                log.warning(
                    "Flex SendRequest attempt %d not accepted (%s: %s) — retrying in %.0fs",
                    attempt, exc.code, exc.message, sleep_for,
                )
                time.sleep(sleep_for)
                growth = 4 if exc.code == "1018" else 2
                delay = min(delay * growth, RATE_LIMIT_MAX_DELAY_SECONDS)

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        reraise=True,
    )
    def _send_request(
        self,
        query_id: str,
        *,
        from_date: str | None,
        to_date: str | None,
        period: str | None,
    ) -> str:
        params: dict[str, str] = {"t": self._token, "q": str(query_id), "v": "3"}
        if from_date:
            params["fd"] = from_date
        if to_date:
            params["td"] = to_date
        if period and not (from_date or to_date):
            params["p"] = period
        r = self._http.get(f"{self._base_url}/SendRequest", params=params)
        _raise_for_status(r, "SendRequest")
        return self._parse_send_response(r.text)

    @staticmethod
    def _parse_send_response(text: str) -> str:
        try:
            root = _safe_fromstring(text)  # defusedxml
        except ET.ParseError as e:
            raise FlexError(None, f"Invalid SendRequest XML: {e}") from e
        status = (root.findtext(".//Status") or "").strip()
        if status != "Success":
            code = (root.findtext(".//ErrorCode") or "").strip()
            msg = (root.findtext(".//ErrorMessage") or describe(code)).strip()
            if code in TERMINAL_CODES:
                raise FlexError(code, msg)
            raise FlexError(code or "0", msg)
        ref = (root.findtext(".//ReferenceCode") or "").strip()
        if not ref:
            raise FlexError(None, "SendRequest succeeded but no ReferenceCode")
        return ref

    # --------------------------------------------------------------- step 2
    def _poll_get_statement(
        self, reference_code: str, *, deadline: float | None = None
    ) -> bytes:
        params = {"t": self._token, "q": reference_code, "v": "3"}
        if deadline is None:
            deadline = time.monotonic() + self._max_wait
        attempt = 0
        delay = self._poll_interval
        last: tuple[str | None, str] | None = None
        while True:
            attempt += 1
            r = self._http.get(f"{self._base_url}/GetStatement", params=params)
            _raise_for_status(r, "GetStatement")
            body = r.content
            # If the server returns a FlexStatementResponse envelope, it's still a
            # status, not the statement. Otherwise it's the report itself.
            stripped = body.lstrip()
            if stripped.startswith(b"<FlexStatementResponse"):
                code, msg = self._extract_error(stripped)
                if code in RETRYABLE_CODES or code is None:
                    last = (code, msg)
                    if code == "1018":
                        # IBKR asks us to back off — grow the delay instead of
                        # re-hitting the service at the fixed poll interval.
                        delay = min(delay * 4, RATE_LIMIT_MAX_DELAY_SECONDS)
                    else:
                        delay = self._poll_interval
                    # Never sleep past the deadline; after the last sleep the loop
                    # polls once more at the deadline, then stops.
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    delay = min(delay, remaining)
                    log.debug(
                        "Flex GetStatement attempt %d not ready (%s: %s) — sleeping %ds",
                        attempt, code, msg, delay,
                    )
                    time.sleep(delay)
                    continue
                if code in TERMINAL_CODES:
                    raise FlexError(code, msg)
                raise FlexError(code, msg)
            return body
        detail = f" (last IBKR answer: {last[0]} {last[1]})" if last and last[0] else ""
        raise FlexError(None, f"Flex statement not ready by the {self._max_wait}s deadline{detail}")

    @staticmethod
    def _extract_error(body: bytes) -> tuple[str | None, str]:
        try:
            root = _safe_fromstring(body)  # defusedxml
        except ET.ParseError:
            return None, "Unparseable FlexStatementResponse"
        code = (root.findtext(".//ErrorCode") or "").strip() or None
        msg = (root.findtext(".//ErrorMessage") or "").strip() or describe(code or "")
        return code, msg


def _raise_for_status(response: httpx.Response, operation: str) -> None:
    """Raise a FlexError without leaking token/query params from the URL."""
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        request = response.request
        parsed = urlparse(str(request.url)) if request else None
        path = parsed.path.rsplit("/", 1)[-1] if parsed and parsed.path else operation
        # IBKR surfaces some Flex error codes (e.g. 604) as the HTTP status —
        # attach the known diagnostic so the failure is actionable from logs.
        code = str(response.status_code)
        detail = DESCRIPTIONS.get(code)
        raise FlexError(
            code if detail else None,
            f"HTTP {code} from Flex {path}" + (f": {detail}" if detail else ""),
        ) from None
