"""FlexClient polling-loop tests — retry/terminal/backoff/timeout (R1).

No network: `client._http` is swapped for an `httpx.Client` backed by
`httpx.MockTransport`, which is the client's real HTTP boundary. The clock
(`time.monotonic` / `time.sleep` as used inside the client module) is faked so
the tests run in milliseconds and the sleeps are observable.
"""

from __future__ import annotations

import httpx
import pytest

from ibkr_backend.flex import client as client_mod
from ibkr_backend.flex.client import FlexClient, FlexError

SEND_OK = (
    b'<FlexStatementResponse timestamp="t">'
    b"<Status>Success</Status>"
    b"<ReferenceCode>9876543210</ReferenceCode>"
    b"</FlexStatementResponse>"
)

STATEMENT = b'<FlexQueryResponse queryName="q" type="AF"><FlexStatements/></FlexQueryResponse>'


def _envelope(code: str, message: str = "") -> bytes:
    return (
        b"<FlexStatementResponse><Status>Fail</Status>"
        b"<ErrorCode>" + code.encode() + b"</ErrorCode>"
        b"<ErrorMessage>" + message.encode() + b"</ErrorMessage>"
        b"</FlexStatementResponse>"
    )


class FakeClock:
    """Deterministic monotonic clock; sleeping advances it and records the call."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture()
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(client_mod.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(client_mod.time, "sleep", fake.sleep)
    return fake


def _make_client(statement_responses: list[httpx.Response], **kwargs) -> tuple[FlexClient, dict]:
    """FlexClient whose HTTP boundary is a MockTransport.

    SendRequest always succeeds with SEND_OK; each GetStatement call pops the
    next response from `statement_responses` (the last one repeats forever).
    """
    seen = {"send": 0, "get": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/SendRequest"):
            seen["send"] += 1
            return httpx.Response(200, content=SEND_OK)
        seen["get"] += 1
        if len(statement_responses) > 1:
            return statement_responses.pop(0)
        return statement_responses[0]

    client = FlexClient(token="test-token", **kwargs)
    client._http.close()
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client, seen


def test_retryable_1019_polls_again_then_returns_statement(clock):
    responses = [
        httpx.Response(200, content=_envelope("1019", "Statement generation in progress")),
        httpx.Response(200, content=_envelope("1019", "Statement generation in progress")),
        httpx.Response(200, content=STATEMENT),
    ]
    client, seen = _make_client(responses, poll_interval_seconds=5, max_wait_seconds=180)
    with client:
        out = client.fetch(query_id="123456")

    assert out.xml == STATEMENT
    assert out.reference_code == "9876543210"
    assert seen == {"send": 1, "get": 3}
    assert clock.sleeps == [5, 5]  # fixed poll interval for plain retryables


def test_terminal_1012_raises_without_retry(clock):
    responses = [httpx.Response(200, content=_envelope("1012", "Token has expired"))]
    client, seen = _make_client(responses, poll_interval_seconds=5, max_wait_seconds=180)
    with client, pytest.raises(FlexError) as exc_info:
        client.fetch(query_id="123456")

    assert exc_info.value.code == "1012"
    assert "expired" in exc_info.value.message
    assert seen["get"] == 1  # no polling after a terminal code
    assert clock.sleeps == []


def test_send_response_604_without_message_has_token_diagnostic():
    envelope = (
        "<FlexStatementResponse><Status>Fail</Status>"
        "<ErrorCode>604</ErrorCode>"
        "</FlexStatementResponse>"
    )
    with pytest.raises(FlexError) as exc_info:
        FlexClient._parse_send_response(envelope)

    assert exc_info.value.code == "604"
    assert "token" in exc_info.value.message.lower()
    assert "Unknown" not in exc_info.value.message


def test_http_status_604_is_terminal_with_diagnostic(clock):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(604)

    client = FlexClient(token="test-token")
    client._http.close()
    client._http = httpx.Client(transport=httpx.MockTransport(handler))

    with client, pytest.raises(FlexError) as exc_info:
        client.fetch(query_id="123456")

    msg = str(exc_info.value)
    assert exc_info.value.code == "604"
    assert "HTTP 604 from Flex SendRequest" in msg
    assert "token" in msg.lower()
    assert "test-token" not in msg  # no credential leak


def test_rate_limited_1018_backs_off_with_growing_delay(clock):
    responses = [
        httpx.Response(200, content=_envelope("1018", "Too many requests")),
        httpx.Response(200, content=_envelope("1018", "Too many requests")),
        httpx.Response(200, content=_envelope("1018", "Too many requests")),
        httpx.Response(200, content=STATEMENT),
    ]
    client, seen = _make_client(responses, poll_interval_seconds=1, max_wait_seconds=300)
    with client:
        out = client.fetch(query_id="123456")

    assert out.xml == STATEMENT
    assert seen["get"] == 4
    assert len(clock.sleeps) == 3
    assert clock.sleeps[0] > 1  # larger than the plain poll interval
    assert clock.sleeps[0] < clock.sleeps[1] < clock.sleeps[2]  # grows
    assert max(clock.sleeps) <= client_mod.RATE_LIMIT_MAX_DELAY_SECONDS


def test_poll_deadline_exceeded_raises_not_ready(clock):
    # Single repeating 1019 response: the loop polls until max_wait elapses.
    responses = [httpx.Response(200, content=_envelope("1019", "In progress"))]
    client, seen = _make_client(responses, poll_interval_seconds=10, max_wait_seconds=30)
    with client, pytest.raises(FlexError) as exc_info:
        client.fetch(query_id="123456")

    assert exc_info.value.code is None
    assert "not ready after 30s" in exc_info.value.message
    assert seen["get"] == 3  # t=0, 10, 20; deadline hit at t=30
