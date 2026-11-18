"""Margin-risk alerts use IBKR values, explicit policy bands and SQLite state."""

from __future__ import annotations

from datetime import datetime, timezone

from ibkr_backend.config import Settings
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import NotificationState
from ibkr_backend.notify import margin_alerts
from ibkr_backend.notify.margin_alerts import (
    build_eod_snapshot,
    build_live_snapshot,
    check_margin_alert,
    evaluate_margin,
)


def _tag(value: float | str, currency: str = "CHF") -> dict[str, str]:
    return {"value": str(value), "currency": currency}


def _live_summary(*, cushion: float, lookahead_cushion: float | None = None) -> dict:
    nav = 100_000.0
    maint = 25_000.0
    excess = nav * cushion
    summary = {
        "account": "must-not-leak",
        "NetLiquidation": _tag(nav),
        "EquityWithLoanValue": _tag(maint + excess),
        "MaintMarginReq": _tag(maint),
        "ExcessLiquidity": _tag(excess),
        "Cushion": _tag(cushion, ""),
        "SMA": _tag(12_000),
        "Leverage": _tag(1.2, ""),
    }
    if lookahead_cushion is not None:
        summary["LookAheadExcessLiquidity"] = _tag(nav * lookahead_cushion)
        summary["LookAheadMaintMarginReq"] = _tag(
            maint + excess - nav * lookahead_cushion
        )
    return summary


def test_build_live_snapshot_strips_account_and_uses_official_tags():
    captured = datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc)
    snapshot = build_live_snapshot(
        _live_summary(cushion=0.28, lookahead_cushion=0.08),
        captured_at=captured,
    )

    assert snapshot["status"] == "ready"
    assert snapshot["source"] == "ibkr_live"
    assert "account" not in snapshot
    assert snapshot["currency"] == "CHF"
    assert snapshot["cushion_pct"] == 28.0
    assert snapshot["lookahead_cushion_pct"] == 8.0
    assert snapshot["captured_at"] == "2026-08-02T08:00:00Z"


def test_lookahead_can_make_otherwise_safe_account_critical():
    settings = Settings()
    snapshot = build_live_snapshot(
        _live_summary(cushion=0.35, lookahead_cushion=0.08)
    )
    result = evaluate_margin(snapshot, settings=settings)

    assert result["level"] == "critical"
    assert result["official_warning"] is True
    assert "LOOKAHEAD_CUSHION_CRITICAL" in result["reasons"]


def test_negative_excess_is_breach():
    summary = _live_summary(cushion=-0.01)
    snapshot = build_live_snapshot(summary)
    result = evaluate_margin(snapshot, settings=Settings())

    assert result["level"] == "breach"
    assert "EXCESS_LIQUIDITY_NON_POSITIVE" in result["reasons"]


def test_policy_bands_are_explicit_and_configurable():
    settings = Settings(
        margin_cushion_warning_pct=35,
        margin_cushion_high_pct=25,
        margin_cushion_critical_pct=10,
    )
    assert evaluate_margin(
        build_live_snapshot(_live_summary(cushion=0.30)), settings=settings
    )["level"] == "warning"
    assert evaluate_margin(
        build_live_snapshot(_live_summary(cushion=0.20)), settings=settings
    )["level"] == "high"


def test_official_ten_percent_warning_cannot_be_weakened_by_config():
    settings = Settings(
        margin_cushion_warning_pct=30,
        margin_cushion_high_pct=20,
        margin_cushion_critical_pct=5,
    )
    result = evaluate_margin(
        build_live_snapshot(_live_summary(cushion=0.08)), settings=settings
    )

    assert result["level"] == "critical"
    assert result["official_warning"] is True
    assert "IBKR_OFFICIAL_10_PCT_WARNING" in result["reasons"]


def test_eod_snapshot_is_labelled_estimate():
    snapshot = build_eod_snapshot(
        {
            "snapshot_date": "2026-07-31",
            "base_currency": "CHF",
            "nav_total": 100_000,
            "leverage_x": 1.1,
            "reg_t_estimate": {
                "maint_margin_estimate": 25_000,
                "excess_liq_estimate": 75_000,
                "cushion_pct_estimate": 75,
            },
        }
    )
    assert snapshot["status"] == "estimated"
    assert snapshot["source"] == "flex_reg_t_estimate"
    assert snapshot["cushion_pct"] == 75.0


def test_alerts_on_worsening_only_then_sends_recovery(monkeypatch):
    sent: list[str] = []
    settings = Settings(telegram_bot_token="test", telegram_chat_id="1")
    monkeypatch.setattr(
        margin_alerts.telegram,
        "send_message",
        lambda text, **kwargs: sent.append(text) or True,
    )

    snapshots = iter(
        [
            build_live_snapshot(_live_summary(cushion=0.28)),  # warning
            build_live_snapshot(_live_summary(cushion=0.27)),  # same band
            build_live_snapshot(_live_summary(cushion=0.18)),  # high
            build_live_snapshot(_live_summary(cushion=0.24)),  # improves, no send
            build_live_snapshot(_live_summary(cushion=0.35)),  # recovered
        ]
    )
    for _ in range(5):
        with session_scope() as db:
            check_margin_alert(
                db,
                snapshot_fn=lambda: next(snapshots),
                settings=settings,
            )

    assert len(sent) == 3
    assert "VIGILANCIA" in sent[0]
    assert "ALTO" in sent[1]
    assert "RECUPERADO" in sent[2]
    with session_scope() as db:
        state = db.get(NotificationState, "margin_alert_level:ibkr_live")
        assert state and state.value == "safe"


def test_recovery_hysteresis_prevents_band_edge_flapping(monkeypatch):
    """Cushion hovering at the 30% warning edge must NOT flap VIGILANCIA↔RECUPERADO
    on every poll — recovery only fires once it clears warning+hysteresis (32.5%)."""
    sent: list[str] = []
    settings = Settings(telegram_bot_token="test", telegram_chat_id="1")
    monkeypatch.setattr(
        margin_alerts.telegram, "send_message",
        lambda text, **kwargs: sent.append(text) or True,
    )
    # warning(fire) → dead-band(hold) → warning(no send) → dead-band(hold) → clear(recover)
    seq = iter([0.28, 0.31, 0.29, 0.315, 0.35])
    for _ in range(5):
        with session_scope() as db:
            check_margin_alert(
                db,
                snapshot_fn=lambda: build_live_snapshot(_live_summary(cushion=next(seq))),
                settings=settings,
            )
    assert len(sent) == 2  # one VIGILANCIA + one RECUPERADO, not a flap per cycle
    assert "VIGILANCIA" in sent[0]
    assert "RECUPERADO" in sent[1]


def test_send_failure_does_not_consume_transition(monkeypatch):
    settings = Settings(telegram_bot_token="test", telegram_chat_id="1")
    calls = 0

    def fail_then_succeed(text, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("telegram unavailable")
        return True

    monkeypatch.setattr(margin_alerts.telegram, "send_message", fail_then_succeed)
    snapshot = build_live_snapshot(_live_summary(cushion=0.08))

    with session_scope() as db:
        first = check_margin_alert(
            db, snapshot_fn=lambda: snapshot, settings=settings
        )
    with session_scope() as db:
        second = check_margin_alert(
            db, snapshot_fn=lambda: snapshot, settings=settings
        )

    assert first["sent"] is False
    assert second["sent"] is True
    assert calls == 2


def test_dry_run_never_sends_or_mutates(monkeypatch):
    settings = Settings(telegram_bot_token="test", telegram_chat_id="1")
    monkeypatch.setattr(
        margin_alerts.telegram,
        "send_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not send")),
    )
    snapshot = build_live_snapshot(_live_summary(cushion=0.08))

    with session_scope() as db:
        result = check_margin_alert(
            db,
            snapshot_fn=lambda: snapshot,
            settings=settings,
            dry_run=True,
        )
    assert result["would_send"] is True
    with session_scope() as db:
        assert db.get(NotificationState, "margin_alert_level:ibkr_live") is None
