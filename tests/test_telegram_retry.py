"""send_message retry/backoff — transient failures retried, permanent not."""

from __future__ import annotations

import httpx
import pytest

from ibkr_backend.config import Settings
from ibkr_backend.notify import telegram


SETTINGS = Settings(telegram_bot_token="tok", telegram_chat_id="chat")


class _FakeResp:
    def __init__(self, status_code: int, *, ok: bool = True, description: str | None = None):
        self.status_code = status_code
        self._ok = ok
        self._description = description
        self.request = httpx.Request("POST", "https://api.telegram.org/botX/sendMessage")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=self.request, response=self
            )

    def json(self) -> dict:
        return {"ok": self._ok, "description": self._description}


def _install(monkeypatch, effects):
    """Make httpx.post consume `effects` (Exception → raise, else return)."""
    calls = {"n": 0}
    seq = list(effects)

    def fake_post(url, data=None, timeout=None):
        calls["n"] += 1
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(telegram.httpx, "post", fake_post)
    return calls


def test_success_first_try(monkeypatch):
    calls = _install(monkeypatch, [_FakeResp(200)])
    assert telegram.send_message("hi", settings=SETTINGS, backoff_base=0) is True
    assert calls["n"] == 1


def test_transient_network_then_success(monkeypatch):
    calls = _install(monkeypatch, [httpx.ConnectError("boom"), _FakeResp(200)])
    assert telegram.send_message("hi", settings=SETTINGS, backoff_base=0) is True
    assert calls["n"] == 2


def test_retryable_500_then_success(monkeypatch):
    calls = _install(monkeypatch, [_FakeResp(503), _FakeResp(200)])
    assert telegram.send_message("hi", settings=SETTINGS, backoff_base=0) is True
    assert calls["n"] == 2


def test_all_attempts_fail_raises(monkeypatch):
    calls = _install(
        monkeypatch,
        [httpx.ConnectError("a"), httpx.ConnectError("b"), httpx.ConnectError("c")],
    )
    with pytest.raises(telegram.TelegramError):
        telegram.send_message("hi", settings=SETTINGS, max_attempts=3, backoff_base=0)
    assert calls["n"] == 3


def test_permanent_4xx_not_retried(monkeypatch):
    calls = _install(monkeypatch, [_FakeResp(400)])
    with pytest.raises(telegram.TelegramError):
        telegram.send_message("hi", settings=SETTINGS, backoff_base=0)
    assert calls["n"] == 1  # no retry on a permanent client error


SECRET = "123456:SECRET-bot-token"  # pragma: allowlist secret


def test_token_never_in_exception_or_logs(monkeypatch, caplog):
    """B41: httpx errors embed the Bot API URL (`/bot<token>/...`); the raised
    error and every log line must stay token-free."""
    import logging

    secret_settings = Settings(telegram_bot_token=SECRET, telegram_chat_id="chat")
    url = f"https://api.telegram.org/bot{SECRET}/sendMessage"

    class _Resp(_FakeResp):
        def __init__(self, status):
            super().__init__(status)
            self.request = httpx.Request("POST", url)

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                f"Client error '{self.status_code}' for url '{url}'",
                request=self.request, response=self,
            )

    caplog.set_level(logging.DEBUG)
    # permanent 4xx
    _install(monkeypatch, [_Resp(401)])
    with pytest.raises(telegram.TelegramError) as ei:
        telegram.send_message("hi", settings=secret_settings, backoff_base=0)
    assert SECRET not in str(ei.value)
    assert str(ei.value) == "Telegram HTTP 401"
    assert ei.value.__suppress_context__  # raised `from None`: no httpx chain
    # retryable status exhausted
    _install(monkeypatch, [_Resp(503)] * 3)
    with pytest.raises(telegram.TelegramError) as ei:
        telegram.send_message("hi", settings=secret_settings, backoff_base=0)
    assert SECRET not in str(ei.value)
    # network error whose text carries the URL
    _install(monkeypatch, [httpx.ConnectError(f"boom {url}")] * 3)
    with pytest.raises(telegram.TelegramError) as ei:
        telegram.send_message("hi", settings=secret_settings, backoff_base=0)
    assert SECRET not in str(ei.value)
    assert SECRET not in caplog.text


# ---------------------------------------------------------- size-guard split
def test_oversize_message_split_on_block_boundaries(monkeypatch):
    """A >4096-char message is split on \\n\\n boundaries into multiple sends
    (each <=4096), preventing the permanent 400 that would wedge the digest."""
    calls = _install(monkeypatch, [_FakeResp(200), _FakeResp(200)])
    body = "\n\n".join(["X" * 1000] * 6)  # 6010 chars → 2 chunks (4006 + 2002)
    assert telegram.send_message(body, settings=SETTINGS, backoff_base=0) is True
    assert calls["n"] == 2


def test_split_returns_false_if_a_chunk_not_ok(monkeypatch):
    calls = _install(monkeypatch, [_FakeResp(200), _FakeResp(200, ok=False)])
    body = "\n\n".join(["X" * 1000] * 6)
    assert telegram.send_message(body, settings=SETTINGS, backoff_base=0) is False
    assert calls["n"] == 2  # stopped after the not-ok chunk


def test_429_honours_retry_after(monkeypatch):
    """A 429 sleeps the server's retry_after (capped) instead of the fixed backoff."""
    slept: list[float] = []
    monkeypatch.setattr(telegram.time, "sleep", lambda d: slept.append(d))

    class _Resp429:
        status_code = 429
        request = httpx.Request("POST", "https://api.telegram.org/botX/sendMessage")

        def raise_for_status(self) -> None:  # pragma: no cover — 429 is retryable
            pass

        def json(self) -> dict:
            return {"ok": True, "parameters": {"retry_after": 7}}

    seq = [_Resp429(), _FakeResp(200)]
    monkeypatch.setattr(telegram.httpx, "post", lambda *a, **k: seq.pop(0))
    assert telegram.send_message("hi", settings=SETTINGS) is True
    assert slept == [7.0]  # used retry_after, not backoff_base*2**n


def test_ok_false_returns_false_without_retry(monkeypatch):
    calls = _install(monkeypatch, [_FakeResp(200, ok=False, description="blocked")])
    assert telegram.send_message("hi", settings=SETTINGS, backoff_base=0) is False
    assert calls["n"] == 1
