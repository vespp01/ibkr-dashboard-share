"""Periodic margin-usage message — Flex EOD estimate, periodic dedup, alert escalation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ibkr_backend.config import Settings
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import NavDaily, NotificationState, PositionEod
from ibkr_backend.notify import margin_usage
from ibkr_backend.notify.margin_usage import (
    build_margin_usage_result,
    check_margin_usage,
    format_margin_usage_message,
)

SNAP = date(2026, 7, 31)


def _seed(session, *, nav=100_000, pos_value=50_000, asset_class="STK", cash=None):
    session.add(
        NavDaily(snapshot_date=SNAP, account_id="U1", base_currency="CHF", nav_total=Decimal(nav),
                 cash=None if cash is None else Decimal(cash))
    )
    session.add(
        PositionEod(
            snapshot_date=SNAP, account_id="U1", conid=1, symbol="AAA",
            quantity=Decimal("100"), position_value=Decimal(pos_value),
            fx_rate_to_base=Decimal("1"), asset_class=asset_class,
        )
    )


def _settings(**kw):
    return Settings(enable_live_overlay=False, **kw)


def test_build_result_from_flex_eod_estimate():
    with session_scope() as s:
        _seed(s)  # STK 50k → maint 12.5k on 100k NAV → cushion 87.5, usage 12.5
    with session_scope() as s:
        result = build_margin_usage_result(s, settings=_settings())
    assert result["source"] == "flex_reg_t_estimate"
    assert round(result["cushion_pct"], 1) == 87.5
    assert result["usage_pct"] == 12.5
    assert result["distance_to_recommended_pct"] == 57.5  # 87.5 − warning(30)
    assert result["level"] == "safe"
    msg = format_margin_usage_message(result)
    assert "Usado: <b>12.5%</b> del patrimonio · colchón <b>87.5%</b>" in msg
    assert "A <b>57.5 pp</b> del límite recomendado (colchón mín. 30%)" in msg
    assert "<i>Fuente: estimación Flex EOD" in msg      # caveat + disclaimer footer
    assert "no ejecuta órdenes" in msg


def test_message_reports_liquidity_as_cash_over_nav():
    with session_scope() as s:
        _seed(s, cash=20_000)  # 20k cash on 100k NAV
    with session_scope() as s:
        result = build_margin_usage_result(s, settings=_settings())
    assert result["cash_pct_nlv"] == 20.0
    msg = format_margin_usage_message(result)
    assert "Liquidez: <b>20.0%</b> del patrimonio en caja (20,000 CHF netos)" in msg


def test_periodic_status_deduped_same_day(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(margin_usage.telegram, "send_message", lambda text, **k: sent.append(text) or True)
    settings = _settings(telegram_bot_token="t", telegram_chat_id="1")
    today = date(2026, 8, 10)
    with session_scope() as s:
        _seed(s)
    with session_scope() as s:
        r1 = check_margin_usage(s, settings=settings, periodic_allowed=True, today=today)
    with session_scope() as s:
        r2 = check_margin_usage(s, settings=settings, periodic_allowed=True, today=today)
    assert r1["sent"] is True
    assert r2["sent"] is False  # same-day dedup
    assert len(sent) == 1


def test_alert_escalates_even_when_periodic_gate_closed(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(margin_usage.telegram, "send_message", lambda text, **k: sent.append(text) or True)
    settings = _settings(telegram_bot_token="t", telegram_chat_id="1")
    with session_scope() as s:
        _seed(s, pos_value=280_000)  # maint 70k → cushion 30 → warning band
    with session_scope() as s:
        r = check_margin_usage(
            s, settings=settings, periodic_allowed=False, alert_bands=True, today=date(2026, 8, 10)
        )
    assert r["level"] == "warning"
    assert r["alert_due"] is True
    assert r["sent"] is True
    assert "VIGILANCIA" in sent[0]


def test_alert_bands_off_suppresses_escalation(monkeypatch):
    monkeypatch.setattr(
        margin_usage.telegram, "send_message",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not send")),
    )
    settings = _settings(telegram_bot_token="t", telegram_chat_id="1")
    with session_scope() as s:
        _seed(s, pos_value=280_000)
    with session_scope() as s:
        r = check_margin_usage(
            s, settings=settings, periodic_allowed=False, alert_bands=False, today=date(2026, 8, 10)
        )
    assert r["sent"] is False
    assert r["would_send"] is False


def test_dry_run_never_sends_or_mutates(monkeypatch):
    monkeypatch.setattr(
        margin_usage.telegram, "send_message",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not send")),
    )
    with session_scope() as s:
        _seed(s)
    with session_scope() as s:
        r = check_margin_usage(s, settings=_settings(telegram_bot_token="t", telegram_chat_id="1"),
                               today=date(2026, 8, 10), dry_run=True)
    assert r["would_send"] is True
    with session_scope() as s:
        assert s.get(NotificationState, "margin_usage_last_sent") is None
