"""Telegram daily digest — report builders + watermark. Real SQLite per test
(no DB mocking); only the Telegram HTTP send is monkeypatched."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from ibkr_backend.db import session_scope
from ibkr_backend.db.models import (
    Account,
    CashTransaction,
    Instrument,
    NotificationState,
    OptionExerciseAssignment,
    Trade,
)
from ibkr_backend.notify import reports
from ibkr_backend.notify.reports import (
    commissions_report,
    daily_notify,
    dividends_report,
    operations_report,
)

DAY = date(2026, 6, 11)


# ----------------------------------------------------------------- seeding
def _ensure(session, model, pk, obj):
    if session.get(model, pk) is None:
        session.add(obj)
        session.flush()


def _seed_dividend(session, *, tx_id, ttype, amount, ccy, desc, settle=DAY):
    _ensure(session, Account, "U1", Account(account_id="U1", base_currency="CHF"))
    session.add(
        CashTransaction(
            transaction_id=tx_id,
            account_id="U1",
            type=ttype,
            amount=Decimal(str(amount)),
            currency=ccy,
            fx_rate_to_base=Decimal("0.9"),
            event_datetime=datetime(2026, 6, 11, 10, 0),
            settle_date=settle,
            description=desc,
        )
    )


def _seed_trade(session, *, tid, buy_sell, qty, symbol, price, ac="STK", day=DAY, comm="-1.00"):
    conid = abs(hash(tid)) % 100000
    _ensure(session, Account, "U1", Account(account_id="U1", base_currency="CHF"))
    _ensure(session, Instrument, conid, Instrument(conid=conid, symbol=symbol, asset_class=ac))
    session.add(
        Trade(
            trade_id=tid,
            account_id="U1",
            conid=conid,
            symbol=symbol,
            trade_date=day,
            trade_datetime=datetime(day.year, day.month, day.day, 15, 30),
            quantity=Decimal(str(qty)),
            trade_price=Decimal(str(price)),
            proceeds=Decimal("0"),
            net_cash=Decimal("0"),
            ib_commission=Decimal(comm),
            fx_rate_to_base=Decimal("0.9"),
            currency="USD",
            asset_class=ac,
            buy_sell=buy_sell,
        )
    )


# ------------------------------------------------------------- parsers
def test_parsers():
    desc = "DEMO(US0000000000) Cash Dividend USD 1.67 per Share (Ordinary Dividend)"
    assert reports._parse_ticker(desc) == "DEMO"
    assert reports._parse_isin(desc) == "US0000000000"
    assert reports._parse_rate(desc) == 1.67
    # case-insensitive rate parse
    assert reports._parse_rate("SAMP(US0000000001) CASH DIVIDEND USD 0.256 PER SHARE") == 0.256


# ------------------------------------------------------------- dividends
def test_dividends_report_groups_and_nets():
    with session_scope() as s:
        _seed_dividend(
            s, tx_id="d1", ttype="Dividends", amount="6.68", ccy="USD",
            desc="DEMO(US0000000000) Cash Dividend USD 1.67 per Share (Ordinary Dividend)",
        )
        _seed_dividend(
            s, tx_id="w1", ttype="Withholding Tax", amount="-1.00", ccy="USD",
            desc="DEMO(US0000000000) Cash Dividend USD 1.67 per Share - US TAX",
        )
    with session_scope() as s:
        txt = dividends_report(s, DAY, "CHF")
    assert txt is not None
    assert "Dividendo" in txt and "DEMO" in txt
    assert "de 4 acc" in txt        # 6.68 / 1.67 = 4 shares
    assert "Importe retenido" in txt and "Importe neto" in txt
    assert "USD" in txt and "CHF" in txt   # native + base shown
    assert "Tipo cambio" in txt


def test_dividends_report_none_when_empty():
    with session_scope() as s:
        assert dividends_report(s, DAY, "CHF") is None


# ------------------------------------------------------------- operations
def test_operations_report_buy_sell_and_excludes_forex():
    with session_scope() as s:
        _seed_trade(s, tid="t1", buy_sell="BUY", qty="15", symbol="HIMS", price="30.00")
        _seed_trade(s, tid="t2", buy_sell="SELL", qty="-11", symbol="CNI", price="99.57")
        _seed_trade(s, tid="t3", buy_sell="BUY", qty="1000", symbol="EUR.USD", price="1.08", ac="CASH")
    with session_scope() as s:
        txt = operations_report(s, DAY)
    assert txt is not None
    assert "Compra" in txt and "Buy 15 HIMS" in txt
    assert "Venta" in txt and "Sell -11 CNI" in txt
    assert "Importe:" in txt and "Tipo cambio:" in txt
    assert "EUR.USD" not in txt    # forex excluded


def test_operations_report_includes_eae_and_cash():
    with session_scope() as s:
        _ensure(s, Account, "U1", Account(account_id="U1", base_currency="CHF"))
        s.add(
            OptionExerciseAssignment(
                eae_id="e1", account_id="U1", symbol="ACME  260116C00033000",
                transaction_type="Exercise", quantity=Decimal("-1"),
                event_date=DAY, currency="USD",
            )
        )
        s.add(
            CashTransaction(
                transaction_id="dep1", account_id="U1", type="Deposits/Withdrawals",
                amount=Decimal("100.00"), currency="EUR",
                event_datetime=datetime(2026, 6, 11, 9, 0), settle_date=DAY,
                description="Electronic Fund Transfer",
            )
        )
    with session_scope() as s:
        txt = operations_report(s, DAY)
    assert txt is not None
    assert "Ejercicio/Asignación" in txt and "Exercise" in txt
    assert "Ingreso" in txt and "100.00" in txt and "EUR" in txt


# ------------------------------------------------------------- commissions
def test_commissions_report_month_to_date():
    with session_scope() as s:
        _seed_trade(s, tid="t1", buy_sell="BUY", qty="15", symbol="HIMS", price="30", comm="-1.50")
        _seed_trade(s, tid="t2", buy_sell="SELL", qty="-11", symbol="CNI", price="99", comm="-2.50")
    with session_scope() as s:
        txt = commissions_report(s, DAY, "CHF")
    assert txt is not None
    assert "Comisiones mensuales utilizadas hasta fecha 11/06/2026" in txt
    assert "4.00$" in txt          # |−1.50 − 2.50| native sum


# ------------------------------------------------------------- orchestration
def test_daily_notify_sends_and_sets_watermark(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(reports.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        reports.telegram, "send_message",
        lambda text, **k: sent.append(text) or True,
    )
    with session_scope() as s:
        _seed_trade(s, tid="t1", buy_sell="BUY", qty="15", symbol="HIMS", price="30")

    with session_scope() as s:
        summary = daily_notify(s, target=DAY)
    assert "operations" in summary["sent"]
    assert "commissions" in summary["sent"]
    assert len(sent) == 2

    # watermark written → a second run for the same day is skipped
    with session_scope() as s:
        wm = s.get(NotificationState, "last_notified_date")
        assert wm.value == DAY.isoformat()
    with session_scope() as s:
        summary2 = daily_notify(s, target=DAY)
    assert summary2["skipped"] == "already notified"
    assert len(sent) == 2          # nothing re-sent


# ------------------------------------------------------- token expiry warning
def test_token_expiry_warns_within_window():
    msg = reports.token_expiry_message(date(2026, 10, 1), date(2026, 9, 27), 7)
    assert msg is not None
    assert "caduca pronto" in msg and "01/10/2026" in msg and "en 4 día/s" in msg


def test_token_expiry_warns_today():
    msg = reports.token_expiry_message(date(2026, 10, 1), date(2026, 10, 1), 7)
    assert msg is not None and "hoy" in msg


def test_token_expiry_flags_expired():
    msg = reports.token_expiry_message(date(2026, 10, 1), date(2026, 10, 5), 7)
    assert msg is not None and "CADUCADO" in msg and "hace 4 día/s" in msg


def test_token_expiry_silent_outside_window():
    assert reports.token_expiry_message(date(2026, 10, 1), date(2026, 9, 1), 7) is None


def test_daily_notify_force_bypasses_watermark(monkeypatch):
    monkeypatch.setattr(reports.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(reports.telegram, "send_message", lambda text, **k: True)
    with session_scope() as s:
        s.add(NotificationState(key="last_notified_date", value=DAY.isoformat()))
        _seed_trade(s, tid="t1", buy_sell="BUY", qty="15", symbol="HIMS", price="30")
    with session_scope() as s:
        summary = daily_notify(s, target=DAY, force=True)
    assert summary["sent"]         # sent despite watermark == target


def test_daily_notify_catches_up_gap_no_leapfrog(monkeypatch):
    """A failed-pull gap must NOT leapfrog days: every unnotified day between
    the watermark and `target` is reported, not just yesterday."""
    sent: list[str] = []
    monkeypatch.setattr(reports.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        reports.telegram, "send_message",
        lambda text, **k: sent.append(text) or True,
    )
    gap_div_day = date(2026, 6, 15)   # dividend sits inside the gap, not on "yesterday"
    new_target = date(2026, 6, 17)
    with session_scope() as s:
        # watermark 2 days behind target → 15, 16, 17 must all be processed
        s.add(NotificationState(key="last_notified_date", value=date(2026, 6, 14).isoformat()))
        _seed_dividend(
            s, tx_id="d1", ttype="Dividends", amount="6.68", ccy="USD",
            desc="DEMO(US0000000000) Cash Dividend USD 1.67 per Share",
            settle=gap_div_day,
        )
    with session_scope() as s:
        summary = daily_notify(s, target=new_target)
    # the gap day's dividend WAS sent even though it isn't "yesterday"
    assert any("Dividendo 15/06/2026" in m for m in sent)
    assert summary["days"] == ["2026-06-15", "2026-06-16", "2026-06-17"]
    with session_scope() as s:
        wm = s.get(NotificationState, "last_notified_date")
        assert wm.value == new_target.isoformat()


def test_daily_notify_catchup_capped(monkeypatch):
    """A very stale watermark is capped to `max_catchup_days` days back."""
    monkeypatch.setattr(reports.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(reports.telegram, "send_message", lambda text, **k: True)
    with session_scope() as s:
        s.add(NotificationState(key="last_notified_date", value=date(2026, 1, 1).isoformat()))
    with session_scope() as s:
        summary = daily_notify(s, target=date(2026, 6, 17), max_catchup_days=5)
    assert summary["days"] == [
        "2026-06-13", "2026-06-14", "2026-06-15", "2026-06-16", "2026-06-17",
    ]
