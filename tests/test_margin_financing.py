"""Margin financing compares official IBKR rates with an ECB CHF replay."""

from __future__ import annotations

from datetime import date

import pytest

from ibkr_backend.config import Settings
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import NotificationState
from ibkr_backend.notify import margin_financing
from ibkr_backend.notify.margin_financing import (
    analyze_margin_financing,
    blended_apr,
    check_margin_financing_notice,
    parse_ecb_chf_history,
    parse_ibkr_margin_rates,
)


RATES_HTML = """
<html><body><table>
  <thead><tr><th>Currency</th><th>Tier</th>
  <th>Rate Charged: IBKR Pro</th><th>Rate Charged: IBKR Lite</th></tr></thead>
  <tbody>
    <tr><td>CHF</td><td>0 ≤ 100</td><td>1.000% (BM + 1%)</td><td>2.0%</td></tr>
    <tr><td></td><td>&gt; 100</td><td>0.500% (BM + 0.5%)</td><td>2.0%</td></tr>
    <tr><td>EUR</td><td>0 ≤ 100</td><td>3.000%</td><td>4.0%</td></tr>
    <tr><td></td><td>&gt; 100</td><td>2.000%</td><td>4.0%</td></tr>
    <tr><td>USD</td><td>&gt; 0</td><td>1.000%</td><td>2.0%</td></tr>
  </tbody>
</table></body></html>
"""


ECB_CSV = """CURRENCY,TIME_PERIOD,OBS_VALUE
CHF,2025-08-01,0.95
USD,2025-08-01,1.10
CHF,2026-07-31,0.90
USD,2026-07-31,1.20
"""


def _flat(apr: float) -> list[dict]:
    return [{"up_to": None, "apr_pct": apr, "tier": "> 0"}]


def _fx(changes: dict[str, float]) -> dict:
    currencies = set(changes) | {"CHF"}
    return {
        "as_of": "2026-07-31",
        "historical_date": "2025-08-01",
        "current_chf_per_currency": {currency: 1.0 for currency in currencies},
        "historical_chf_per_currency": {
            currency: 1.0 / (1.0 + changes.get(currency, 0.0) / 100)
            for currency in currencies
        },
        "change_vs_chf_pct": {currency: changes.get(currency, 0.0) for currency in currencies},
        "source": "ECB",
    }


def test_parse_ibkr_rates_and_progressive_blend():
    tiers = parse_ibkr_margin_rates(RATES_HTML, {"CHF", "EUR", "USD"})

    assert tiers["CHF"] == [
        {"tier": "0 ≤ 100", "up_to": 100.0, "apr_pct": 1.0},
        {"tier": "> 100", "up_to": None, "apr_pct": 0.5},
    ]
    assert blended_apr(150, tiers["CHF"]) == pytest.approx(0.8333333333)
    assert tiers["USD"][0]["apr_pct"] == 1.0


def test_parse_ecb_series_builds_chf_cross_and_one_year_replay():
    result = parse_ecb_chf_history(
        ECB_CSV,
        {"CHF", "EUR", "USD"},
        today=date(2026, 8, 2),
        history_days=365,
        max_age_days=7,
    )

    assert result["as_of"] == "2026-07-31"
    assert result["current_chf_per_currency"]["USD"] == pytest.approx(0.75)
    assert result["historical_chf_per_currency"]["USD"] == pytest.approx(0.95 / 1.10)
    assert result["change_vs_chf_pct"]["USD"] < 0
    assert result["current_chf_per_currency"]["EUR"] == pytest.approx(0.90)


def test_robust_candidate_requires_interest_and_replay_to_agree():
    result = analyze_margin_financing(
        debts=[{"currency": "CHF", "ending_cash": -10_000}],
        rate_tiers={"CHF": _flat(1.5), "EUR": _flat(3.0), "USD": _flat(1.0)},
        fx_history=_fx({"CHF": 0, "EUR": 0, "USD": -10}),
        candidates=["CHF", "EUR", "USD"],
        min_debt_chf=100,
        min_saving_chf=50,
    )

    assert result["status"] == "ready"
    assert result["interest_winner"] == "USD"
    assert result["historical_replay_winner"] == "USD"
    assert result["robust_candidate"] == "USD"
    assert result["decision"] == "review_switch"


def test_disagreement_returns_no_clear_winner():
    result = analyze_margin_financing(
        debts=[{"currency": "CHF", "ending_cash": -10_000}],
        rate_tiers={"CHF": _flat(1.0), "USD": _flat(2.0)},
        fx_history=_fx({"CHF": 0, "USD": -5}),
        candidates=["CHF", "USD"],
        min_debt_chf=100,
        min_saving_chf=50,
    )

    assert result["interest_winner"] == "CHF"
    assert result["historical_replay_winner"] == "USD"
    assert result["robust_candidate"] is None
    assert result["decision"] == "no_clear_winner"


def test_notice_is_weekly_deduplicated(monkeypatch):
    sent: list[str] = []
    settings = Settings(
        telegram_bot_token="test",
        telegram_chat_id="1",
        margin_fx_enabled=True,
    )
    analysis = {
        "status": "ready",
        "as_of": "2026-07-31",
        "debt_total_chf": 10_000,
        "debt_by_currency": [{"currency": "CHF", "amount_native": 10_000, "amount_chf": 10_000}],
        "comparisons": [
            {"currency": "CHF", "apr_pct": 1.5, "fx_change_pct": 0.0, "replay_total_pct": 1.5},
            {"currency": "USD", "apr_pct": 1.0, "fx_change_pct": -10.0, "replay_total_pct": -9.1},
        ],
        "interest_winner": "USD",
        "historical_replay_winner": "USD",
        "robust_candidate": "USD",
        "decision": "review_switch",
        "estimated_interest_saving_chf": 50.0,
        "estimated_replay_saving_chf": 1_060.0,
        "sources": {
            "ibkr_rates_fetched_at": "2026-08-02T08:00:00Z",
            "ecb_as_of": "2026-07-31",
        },
    }
    monkeypatch.setattr(
        margin_financing.telegram,
        "send_message",
        lambda text, **kwargs: sent.append(text) or True,
    )

    for _ in range(2):
        with session_scope() as db:
            result = check_margin_financing_notice(
                db,
                analysis_fn=lambda: analysis,
                settings=settings,
                today=date(2026, 8, 2),
            )

    assert len(sent) == 1
    assert "USD" in sent[0]
    assert "no es una previsión" in sent[0]
    assert result["sent"] is False
    with session_scope() as db:
        state = db.get(NotificationState, "margin_fx_last_notice")
        assert state and state.value == "2026-W31|USD"


def _no_winner_analysis() -> dict:
    return {
        "status": "ready",
        "as_of": "2026-07-31",
        "historical_date": "2025-07-31",
        "debt_total_chf": 10_000,
        "debt_by_currency": [],
        "comparisons": [
            {"currency": "CHF", "apr_pct": 1.0, "fx_change_pct": 0.0, "replay_total_pct": 1.0},
            {"currency": "USD", "apr_pct": 2.0, "fx_change_pct": -5.0, "replay_total_pct": -3.1},
        ],
        "interest_winner": "CHF",
        "historical_replay_winner": "USD",
        "robust_candidate": None,
        "decision": "no_clear_winner",
        "estimated_interest_saving_chf": None,
        "estimated_replay_saving_chf": None,
    }


def test_notice_none_to_none_sends_nothing(monkeypatch):
    """No robust candidate and no prior signal → weekly noise suppressed."""
    settings = Settings(
        telegram_bot_token="test", telegram_chat_id="1", margin_fx_enabled=True
    )
    monkeypatch.setattr(
        margin_financing.telegram, "send_message",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not send")),
    )
    with session_scope() as db:
        result = check_margin_financing_notice(
            db, analysis_fn=_no_winner_analysis, settings=settings,
            today=date(2026, 8, 2),
        )
    assert result["would_send"] is False
    assert result["sent"] is False
    with session_scope() as db:
        assert db.get(NotificationState, "margin_fx_last_notice") is None


def test_notice_candidate_flip_to_none_sends_transition(monkeypatch):
    """A USD→none flip sends a short 'previous signal lapsed' notice."""
    sent: list[str] = []
    settings = Settings(
        telegram_bot_token="test", telegram_chat_id="1", margin_fx_enabled=True
    )
    monkeypatch.setattr(
        margin_financing.telegram, "send_message",
        lambda text, **k: sent.append(text) or True,
    )
    with session_scope() as db:
        db.add(NotificationState(key="margin_fx_last_notice", value="2026-W30|USD"))
    with session_scope() as db:
        result = check_margin_financing_notice(
            db, analysis_fn=_no_winner_analysis, settings=settings,
            today=date(2026, 8, 2),
        )
    assert result["sent"] is True
    assert len(sent) == 1
    assert "sin ganador claro" in sent[0]
    assert "La señal anterior (USD) ya no se sostiene." in sent[0]
    assert "31/07/2025 → 31/07/2026" in sent[0]      # replay dates in DD/MM/YYYY
    with session_scope() as db:
        state = db.get(NotificationState, "margin_fx_last_notice")
        assert state and state.value == "2026-W31|NONE"


def test_notice_unchanged_nonactionable_candidate_is_silent(monkeypatch):
    """Same candidate, non-actionable verdict → nothing sent week after week."""
    settings = Settings(
        telegram_bot_token="test", telegram_chat_id="1", margin_fx_enabled=True
    )
    analysis = {
        **_no_winner_analysis(),
        "robust_candidate": "CHF",
        "decision": "small_difference",
        "interest_winner": "CHF",
        "historical_replay_winner": "CHF",
    }
    monkeypatch.setattr(
        margin_financing.telegram, "send_message",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not send")),
    )
    with session_scope() as db:
        db.add(NotificationState(key="margin_fx_last_notice", value="2026-W30|CHF"))
    with session_scope() as db:
        result = check_margin_financing_notice(
            db, analysis_fn=lambda: analysis, settings=settings,
            today=date(2026, 8, 2),
        )
    assert result["would_send"] is False
    assert result["sent"] is False


def test_notice_send_failure_does_not_consume_week(monkeypatch):
    settings = Settings(
        telegram_bot_token="test",
        telegram_chat_id="1",
        margin_fx_enabled=True,
    )
    analysis = {
        "status": "ready",
        "as_of": "2026-07-31",
        "historical_date": "2025-07-31",
        "debt_total_chf": 1_000,
        "debt_by_currency": [],
        "comparisons": [
            {
                "currency": "CHF",
                "apr_pct": 1.5,
                "fx_change_pct": 0.0,
                "replay_total_pct": 1.5,
            }
        ],
        "interest_winner": "CHF",
        "historical_replay_winner": "CHF",
        "robust_candidate": "CHF",
        "decision": "already_aligned",
        "estimated_interest_saving_chf": 0.0,
        "estimated_replay_saving_chf": 0.0,
    }
    monkeypatch.setattr(
        margin_financing.telegram,
        "send_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    with session_scope() as db:
        result = check_margin_financing_notice(
            db,
            analysis_fn=lambda: analysis,
            settings=settings,
            today=date(2026, 8, 2),
        )

    assert result["sent"] is False
    with session_scope() as db:
        assert db.get(NotificationState, "margin_fx_last_notice") is None
