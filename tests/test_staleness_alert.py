"""Staleness watchdog Telegram push — one alert per episode, re-armed on recovery."""

from __future__ import annotations

from ibkr_backend.config import Settings
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import FlexRun, NotificationState, _utcnow
from ibkr_backend.notify import staleness


SETTINGS = Settings(
    ib_flex_token="tok", ib_flex_query_id="q",
    telegram_bot_token="bt", telegram_chat_id="ch",
)


def _capture(monkeypatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(staleness.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        staleness.telegram, "send_message",
        lambda text, **k: sent.append(text) or True,
    )
    return sent


def test_alert_sent_once_then_deduped(monkeypatch):
    sent = _capture(monkeypatch)
    # Pin the expiry empty — the module-level SETTINGS may inherit it from .env.
    settings = Settings(
        ib_flex_token="tok", ib_flex_query_id="q",
        telegram_bot_token="bt", telegram_chat_id="ch",
        ib_flex_token_expiry="",
    )
    # No successful FlexRun on record → stale.
    with session_scope() as s:
        r1 = staleness.check_staleness_alert(s, settings=settings)
    assert r1["sent"] is True
    assert len(sent) == 1
    assert "Pipeline sin actualizar" in sent[0]
    assert "Último pull de Flex OK" in sent[0]
    assert "Siguiente paso: docker compose logs backend" in sent[0]
    # Plan B path with < > escaped for parse_mode=HTML
    assert "load-xml /app/Extractos/&lt;export&gt;.xml" in sent[0]
    # No token expiry configured → no token cross-check line.
    assert "Token Flex" not in sent[0] and "token Flex" not in sent[0]

    # Second tick while still stale → deduped, nothing re-sent.
    with session_scope() as s:
        r2 = staleness.check_staleness_alert(s, settings=SETTINGS)
    assert r2["sent"] is False
    assert r2["skipped"] == "already alerted"
    assert len(sent) == 1
    with session_scope() as s:
        flag = s.get(NotificationState, "stale_alert_active")
        assert flag.value == "1"


def test_alert_rearms_after_recovery(monkeypatch):
    sent = _capture(monkeypatch)
    with session_scope() as s:
        staleness.check_staleness_alert(s, settings=SETTINGS)  # arms
    assert len(sent) == 1

    # A fresh successful run → not stale → re-arm the flag.
    with session_scope() as s:
        s.add(FlexRun(query_id="q", status="success", fetched_at=_utcnow(), note="scheduled"))
    with session_scope() as s:
        r = staleness.check_staleness_alert(s, settings=SETTINGS)
    assert r["stale"] is False
    with session_scope() as s:
        flag = s.get(NotificationState, "stale_alert_active")
        assert flag.value == "0"
    assert len(sent) == 1  # no new alert on recovery


def test_alert_flags_expired_token_as_probable_cause(monkeypatch):
    sent = _capture(monkeypatch)
    settings = Settings(
        ib_flex_token="tok", ib_flex_query_id="q",
        telegram_bot_token="bt", telegram_chat_id="ch",
        ib_flex_token_expiry="2020-01-15",     # long past → probable cause
    )
    with session_scope() as s:
        r = staleness.check_staleness_alert(s, settings=settings)
    assert r["sent"] is True
    assert "Causa probable: el token Flex caducó el 15/01/2020. " in sent[0]


def test_alert_rules_out_valid_token(monkeypatch):
    sent = _capture(monkeypatch)
    settings = Settings(
        ib_flex_token="tok", ib_flex_query_id="q",
        telegram_bot_token="bt", telegram_chat_id="ch",
        ib_flex_token_expiry="2099-12-31",     # future → token ruled out
    )
    with session_scope() as s:
        r = staleness.check_staleness_alert(s, settings=settings)
    assert r["sent"] is True
    assert "Token Flex válido hasta el 31/12/2099" in sent[0]
    assert "revisa el scheduler o la query de Flex" in sent[0]


def test_alert_omits_token_line_on_garbage_expiry(monkeypatch):
    sent = _capture(monkeypatch)
    settings = Settings(
        ib_flex_token="tok", ib_flex_query_id="q",
        telegram_bot_token="bt", telegram_chat_id="ch",
        ib_flex_token_expiry="not-a-date",
    )
    with session_scope() as s:
        r = staleness.check_staleness_alert(s, settings=settings)
    assert r["sent"] is True
    assert "Token Flex" not in sent[0] and "token Flex" not in sent[0]


def test_no_alert_when_telegram_not_configured(monkeypatch):
    # is_configured False → never sends, never arms (fires once configured later).
    monkeypatch.setattr(staleness.telegram, "is_configured", lambda *a, **k: False)
    with session_scope() as s:
        r = staleness.check_staleness_alert(s, settings=SETTINGS)
    assert r["sent"] is False
    assert r["skipped"] == "telegram not configured"
    with session_scope() as s:
        assert s.get(NotificationState, "stale_alert_active") is None


# ── Data-age alert (2026-09-12): una vez por episodio, re-armado al recuperar ──

def test_data_age_alert_sent_once_then_rearms(monkeypatch):
    sent = _capture(monkeypatch)
    stale = {"last_data_date": "2026-08-17", "age_business_days": 19,
             "data_is_stale": True, "threshold_business_days": 3, "reason": None}
    with session_scope() as s:
        r1 = staleness.check_data_age_alert(s, settings=SETTINGS, freshness=stale)
    assert r1["sent"] is True and len(sent) == 1
    assert "Datos sin avanzar" in sent[0]
    assert "17/08/2026" in sent[0] and "19 días hábiles" in sent[0]
    assert "Client Portal" in sent[0] and "data_freshness" in sent[0]
    with session_scope() as s:
        r2 = staleness.check_data_age_alert(s, settings=SETTINGS, freshness=stale)
    assert r2["sent"] is False and r2["skipped"] == "already alerted" and len(sent) == 1
    with session_scope() as s:
        assert s.get(NotificationState, "data_stale_alert_active").value == "1"
    fresh = {**stale, "data_is_stale": False, "age_business_days": 1, "last_data_date": "2026-09-11"}
    with session_scope() as s:
        r3 = staleness.check_data_age_alert(s, settings=SETTINGS, freshness=fresh)
    assert r3 == {"stale": False, "sent": False}
    with session_scope() as s:
        assert s.get(NotificationState, "data_stale_alert_active").value == "0"
    with session_scope() as s:
        r4 = staleness.check_data_age_alert(s, settings=SETTINGS, freshness=stale)
    assert r4["sent"] is True and len(sent) == 2


def test_data_age_alert_failed_send_does_not_arm(monkeypatch):
    monkeypatch.setattr(staleness.telegram, "is_configured", lambda *a, **k: True)
    def boom(text, **k):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(staleness.telegram, "send_message", boom)
    stale = {"last_data_date": "2026-08-17", "age_business_days": 19,
             "data_is_stale": True, "threshold_business_days": 3, "reason": None}
    with session_scope() as s:
        r = staleness.check_data_age_alert(s, settings=SETTINGS, freshness=stale)
    assert r["sent"] is False and "error" in r
    with session_scope() as s:
        assert s.get(NotificationState, "data_stale_alert_active") is None


# ── B43: Telegram {ok:false} must leave the dedup marker unarmed ──

def test_staleness_marker_unarmed_when_telegram_not_ok(monkeypatch):
    monkeypatch.setattr(staleness.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(staleness.telegram, "send_message", lambda text, **k: False)
    with session_scope() as s:
        r = staleness.check_staleness_alert(s, settings=SETTINGS)
    assert r["sent"] is False
    with session_scope() as s:
        assert s.get(NotificationState, "stale_alert_active") is None  # retries next tick


def test_data_age_marker_unarmed_when_telegram_not_ok(monkeypatch):
    monkeypatch.setattr(staleness.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(staleness.telegram, "send_message", lambda text, **k: False)
    stale = {"last_data_date": "2026-08-17", "age_business_days": 19,
             "data_is_stale": True, "threshold_business_days": 3, "reason": None}
    with session_scope() as s:
        r = staleness.check_data_age_alert(s, settings=SETTINGS, freshness=stale)
    assert r["sent"] is False
    with session_scope() as s:
        assert s.get(NotificationState, "data_stale_alert_active") is None


def test_performance_pulse_marker_unarmed_when_telegram_not_ok(monkeypatch):
    from datetime import date

    from ibkr_backend.notify import reports

    monkeypatch.setattr(reports.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(reports, "performance_report", lambda *a, **k: "pulse text")
    monkeypatch.setattr(reports.telegram, "send_message", lambda text, **k: False)
    with session_scope() as s:
        r = reports.send_performance_pulse(s, settings=SETTINGS, today=date(2026, 10, 7))
    assert r["sent"] is False
    with session_scope() as s:
        assert s.get(NotificationState, reports._PERF_PULSE_KEY) is None


# ── B47: the alert quotes the last Activity FlexRun error ──

def test_alert_quotes_last_flex_error(monkeypatch):
    sent = _capture(monkeypatch)
    with session_scope() as s:
        s.add(FlexRun(query_id="q", status="error", fetched_at=_utcnow(),
                      error="FlexError: Flex 1025: Invalid token <x>"))
    with session_scope() as s:
        r = staleness.check_staleness_alert(s, settings=SETTINGS)
    assert r["sent"] is True
    assert "Flex 1025: Invalid token &lt;x&gt;" in sent[0]  # quoted + HTML-escaped
    assert "HTTP 604" not in sent[0]
