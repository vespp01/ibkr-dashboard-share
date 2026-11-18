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
    assert "💰 <b>Dividendos 11/06/2026</b>" in txt and "DEMO" in txt
    assert "1.67 USD/acc × 4" in txt        # 6.68 / 1.67 = 4 shares
    assert "Neto: <b>5.68 USD" in txt       # net-first, bolded
    assert "Bruto (" in txt and "6.68" in txt
    assert "Retenido (14.97%):" in txt and "-1.00" in txt
    assert "USD" in txt and "CHF" in txt    # native + base shown
    assert "Cambio: 0.900000" in txt


def test_dividends_report_no_wht_collapses_and_totals():
    """Without withholding, the block collapses to one Importe line; with ≥2
    instruments a transparent base-currency total closes the message."""
    with session_scope() as s:
        _seed_dividend(
            s, tx_id="d1", ttype="Dividends", amount="6.68", ccy="USD",
            desc="DEMO(US0000000000) Cash Dividend USD 1.67 per Share",
        )
        _seed_dividend(
            s, tx_id="d2", ttype="Dividends", amount="2.00", ccy="USD",
            desc="OTHR(US0000000001) Cash Dividend USD 0.5 per Share",
        )
    with session_scope() as s:
        txt = dividends_report(s, DAY, "CHF")
    assert txt is not None
    assert txt.count("💰") == 1             # ONE dated header for the whole message
    assert "Importe: <b>6.68 USD" in txt    # collapsed single line
    assert "Retenido" not in txt and "Bruto" not in txt
    assert "Total neto del día: <b>7.81 CHF</b>" in txt   # (6.68+2.00)×0.9


def test_dividends_report_escapes_html_in_names():
    with session_scope() as s:
        _ensure(s, Account, "U1", Account(account_id="U1", base_currency="CHF"))
        _ensure(
            s, Instrument, 42,
            Instrument(conid=42, symbol="T", asset_class="STK", description="AT&T INC"),
        )
        s.add(
            CashTransaction(
                transaction_id="d1", account_id="U1", type="Dividends", conid=42,
                amount=Decimal("10.00"), currency="USD",
                fx_rate_to_base=Decimal("0.9"),
                event_datetime=datetime(2026, 6, 11, 10, 0), settle_date=DAY,
                description="T(US00206R1023) Cash Dividend USD 0.2775 per Share",
            )
        )
    with session_scope() as s:
        txt = dividends_report(s, DAY, "CHF")
    assert txt is not None
    assert "AT&amp;T INC" in txt            # parse_mode=HTML-safe
    assert "AT&T INC" not in txt


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
        txt = operations_report(s, DAY, "CHF")
    assert txt is not None
    assert "🧾 <b>Operaciones 11/06/2026</b>" in txt
    assert "<b>Compra</b> · 15 × HIMS a 30 USD" in txt      # raw fill price
    assert "<b>Venta</b> · -11 × CNI a 99.57 USD" in txt
    assert "Importe:" in txt and "Cambio:" in txt
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
        txt = operations_report(s, DAY, "CHF")
    assert txt is not None
    assert "<b>Ejercicio</b> · -1 × ACME" in txt      # Exercise → Ejercicio
    assert "<b>Ingreso</b> · Electronic Fund Transfer" in txt
    assert "100.00" in txt and "EUR" in txt


# ------------------------------------------------------------- commissions
def test_commissions_report_month_to_date():
    with session_scope() as s:
        _seed_trade(s, tid="t1", buy_sell="BUY", qty="15", symbol="HIMS", price="30", comm="-1.50")
        _seed_trade(s, tid="t2", buy_sell="SELL", qty="-11", symbol="CNI", price="99", comm="-2.50")
    with session_scope() as s:
        txt = commissions_report(s, DAY, "CHF")
    assert txt is not None
    assert "💸 Comisiones del mes a 11/06/2026" in txt
    assert "<b>3.60 CHF</b>" in txt        # |−1.50 − 2.50| USD x 0.9 fx
    assert "mes anterior" not in txt       # May empty → anchor omitted


def test_commissions_report_previous_month_anchor():
    with session_scope() as s:
        _seed_trade(s, tid="t1", buy_sell="BUY", qty="15", symbol="HIMS", price="30", comm="-1.50")
        _seed_trade(
            s, tid="t0", buy_sell="BUY", qty="5", symbol="CNI", price="90",
            comm="-3.00", day=date(2026, 5, 20),
        )
    with session_scope() as s:
        txt = commissions_report(s, DAY, "CHF")
    assert txt is not None
    assert "<b>1.35 CHF</b>" in txt                      # June MTD only (1.50 USD x 0.9)
    assert "mes anterior completo: 2.70 CHF" in txt      # full May sum (3.00 USD x 0.9)


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


def test_daily_notify_sections_filter_builds_only_selected(monkeypatch):
    """`sections` restricts which digest blocks are built and sent."""
    sent: list[str] = []
    monkeypatch.setattr(reports.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        reports.telegram, "send_message",
        lambda text, **k: sent.append(text) or True,
    )
    with session_scope() as s:
        _seed_trade(s, tid="t1", buy_sell="BUY", qty="15", symbol="HIMS", price="30")
    with session_scope() as s:
        summary = daily_notify(s, target=DAY, sections=["commissions"])
    assert summary["built"] == ["commissions"]
    assert "operations" not in summary["sent"]
    assert len(sent) == 1
    assert "Comisiones" in sent[0]


# ------------------------------------------------------- token expiry warning
def test_token_expiry_warns_within_window():
    msg = reports.token_expiry_message(date(2026, 10, 1), date(2026, 9, 27), 7)
    assert msg is not None
    assert "<b>Token Flex caduca en 4 días</b>" in msg and "01/10/2026" in msg
    # the 3-step checklist, including the step people miss
    assert "1. Regenera el token" in msg
    assert "Performance &amp; Reports" in msg
    assert "3. Aplica el cambio: docker compose up -d backend" in msg


def test_token_expiry_warns_today_and_tomorrow():
    msg = reports.token_expiry_message(date(2026, 10, 1), date(2026, 10, 1), 7)
    assert msg is not None and "caduca hoy" in msg
    msg = reports.token_expiry_message(date(2026, 10, 1), date(2026, 9, 30), 7)
    assert msg is not None and "caduca mañana" in msg


def test_token_expiry_flags_expired():
    msg = reports.token_expiry_message(date(2026, 10, 1), date(2026, 10, 5), 7)
    assert msg is not None
    assert "<b>Token Flex caducado hace 4 días</b>" in msg
    assert "HTTP 604" in msg and "docker compose up -d backend" in msg
    # singular pluralization
    msg1 = reports.token_expiry_message(date(2026, 10, 1), date(2026, 10, 2), 7)
    assert msg1 is not None and "caducado hace 1 día</b>" in msg1


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
    assert any("Dividendos 15/06/2026" in m for m in sent)
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


def test_daily_notify_send_failure_holds_watermark(monkeypatch):
    """A send failure must NOT advance the watermark past the failed day —
    the next run retries it (at-least-once), instead of losing it forever."""
    d1, d2 = date(2026, 6, 15), date(2026, 6, 16)
    monkeypatch.setattr(reports.telegram, "is_configured", lambda *a, **k: True)

    def flaky_send(text, **k):
        if "16/06/2026" in text:  # day-2 messages fail
            raise RuntimeError("telegram down")
        return True

    monkeypatch.setattr(reports.telegram, "send_message", flaky_send)
    with session_scope() as s:
        s.add(NotificationState(key="last_notified_date", value=date(2026, 6, 14).isoformat()))
        _seed_dividend(
            s, tx_id="d1", ttype="Dividends", amount="6.68", ccy="USD",
            desc="DEMO(US0000000000) Cash Dividend USD 1.67 per Share", settle=d1,
        )
        _seed_dividend(
            s, tx_id="d2", ttype="Dividends", amount="3.34", ccy="USD",
            desc="OTHR(US0000000001) Cash Dividend USD 0.5 per Share", settle=d2,
        )
    with session_scope() as s:
        summary = daily_notify(s, target=d2)
    assert summary["outage"] is True
    assert summary["delivered_through"] == d1.isoformat()
    with session_scope() as s:
        wm = s.get(NotificationState, "last_notified_date")
        assert wm.value == d1.isoformat()  # NOT d2 — the failed day is retried

    # Recovery: the next run retries day-2 only and now delivers it.
    monkeypatch.setattr(reports.telegram, "send_message", lambda text, **k: True)
    with session_scope() as s:
        summary2 = daily_notify(s, target=d2)
    assert summary2["days"] == ["2026-06-16"]
    with session_scope() as s:
        wm = s.get(NotificationState, "last_notified_date")
        assert wm.value == d2.isoformat()


def test_daily_notify_rescans_late_arrivals(monkeypatch):
    """A dividend that lands in Flex AFTER its day was reported is re-sent as a
    late movement; a row already present before the last run is not re-sent."""
    captured: list[str] = []
    monkeypatch.setattr(reports.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        reports.telegram, "send_message",
        lambda text, **k: captured.append(text) or True,
    )
    run_ts = datetime(2026, 6, 16, 7, 0)
    reported_day = date(2026, 6, 15)
    with session_scope() as s:
        _ensure(s, Account, "U1", Account(account_id="U1", base_currency="CHF"))
        s.add(NotificationState(key="last_notified_date", value=date(2026, 6, 16).isoformat()))
        s.add(NotificationState(key="last_notify_run_at", value=run_ts.isoformat()))
        # Late arrival: ingested AFTER the last run → must be re-sent.
        s.add(CashTransaction(
            transaction_id="late1", account_id="U1", type="Dividends",
            amount=Decimal("6.68"), currency="USD", fx_rate_to_base=Decimal("0.9"),
            event_datetime=datetime(2026, 6, 15, 10, 0), settle_date=reported_day,
            description="DEMO(US0000000000) Cash Dividend USD 1.67 per Share",
            inserted_at=datetime(2026, 6, 16, 8, 0),
        ))
        # Already present before the last run → must NOT be re-sent.
        s.add(CashTransaction(
            transaction_id="early1", account_id="U1", type="Dividends",
            amount=Decimal("2.00"), currency="USD", fx_rate_to_base=Decimal("0.9"),
            event_datetime=datetime(2026, 6, 15, 9, 0), settle_date=reported_day,
            description="OTHR(US0000000001) Cash Dividend USD 0.5 per Share",
            inserted_at=datetime(2026, 6, 15, 9, 30),
        ))
    with session_scope() as s:
        summary = daily_notify(s, target=date(2026, 6, 17))
    assert summary["late_sent"] == ["dividends@2026-06-15"]
    assert len(captured) == 1
    assert captured[0].startswith("⏰")
    assert "DEMO" in captured[0] and "OTHR(" not in captured[0]


# ------------------------------------------------- commissions dedup (MTD)
def test_daily_notify_commissions_once_across_catchup(monkeypatch):
    """MTD commissions is a cumulative snapshot: a multi-day catch-up must send
    it ONCE (for `target`), not once per caught-up day."""
    sent: list[str] = []
    monkeypatch.setattr(reports.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        reports.telegram, "send_message",
        lambda text, **k: sent.append(text) or True,
    )
    target = date(2026, 6, 17)
    with session_scope() as s:
        # watermark 3 days behind target → 15, 16, 17 caught up in one run
        s.add(NotificationState(key="last_notified_date", value=date(2026, 6, 14).isoformat()))
        _seed_trade(s, tid="t1", buy_sell="BUY", qty="15", symbol="HIMS", price="30", comm="-2.34")
    with session_scope() as s:
        summary = daily_notify(s, target=target)
    assert summary["days"] == ["2026-06-15", "2026-06-16", "2026-06-17"]
    comm_msgs = [m for m in sent if "Comisiones del mes" in m]
    assert len(comm_msgs) == 1
    assert "a 17/06/2026" in comm_msgs[0] and "2.11 CHF" in comm_msgs[0]
    assert summary["sent"].count("commissions") == 1


def test_daily_notify_commissions_skipped_when_unchanged(monkeypatch):
    """No new trades → MTD total unchanged → the next day's run must NOT
    re-send the commissions message (value-signature dedup)."""
    sent: list[str] = []
    monkeypatch.setattr(reports.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        reports.telegram, "send_message",
        lambda text, **k: sent.append(text) or True,
    )
    d1, d2 = date(2026, 6, 17), date(2026, 6, 18)
    with session_scope() as s:
        s.add(NotificationState(key="last_notified_date", value=date(2026, 6, 16).isoformat()))
        _seed_trade(s, tid="t1", buy_sell="BUY", qty="15", symbol="HIMS", price="30", comm="-1.50")
    with session_scope() as s:
        summary1 = daily_notify(s, target=d1)
    assert summary1["sent"].count("commissions") == 1
    assert sum("Comisiones del mes" in m for m in sent) == 1

    # Next day, no new trades: watermark is now d1, run for d2.
    sent.clear()
    with session_scope() as s:
        summary2 = daily_notify(s, target=d2)
    assert summary2["days"] == [d2.isoformat()]
    assert "commissions" not in summary2["sent"]
    assert sum("Comisiones del mes" in m for m in sent) == 0


def test_daily_notify_commissions_resent_when_value_changes(monkeypatch):
    """An additional trade changes the MTD total → the next run re-sends the
    commissions message exactly once."""
    sent: list[str] = []
    monkeypatch.setattr(reports.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        reports.telegram, "send_message",
        lambda text, **k: sent.append(text) or True,
    )
    d1, d2 = date(2026, 6, 17), date(2026, 6, 18)
    with session_scope() as s:
        s.add(NotificationState(key="last_notified_date", value=date(2026, 6, 16).isoformat()))
        _seed_trade(s, tid="t1", buy_sell="BUY", qty="15", symbol="HIMS", price="30", comm="-1.50")
    with session_scope() as s:
        daily_notify(s, target=d1)
    assert sum("Comisiones del mes" in m for m in sent) == 1

    # New trade this month (dated 11/06, outside the late-scan window) → total
    # moves from 1.50$ to 4.00$; the d2 run must re-send exactly once.
    with session_scope() as s:
        _seed_trade(s, tid="t2", buy_sell="SELL", qty="-11", symbol="CNI", price="99", comm="-2.50")
    sent.clear()
    with session_scope() as s:
        summary2 = daily_notify(s, target=d2)
    assert summary2["sent"].count("commissions") == 1
    comm_msgs = [m for m in sent if "Comisiones del mes" in m]
    assert len(comm_msgs) == 1
    assert "a 18/06/2026" in comm_msgs[0] and "3.60 CHF" in comm_msgs[0]


# ------------------------------------------------------- performance pulse
def test_performance_report_none_on_empty_db():
    with session_scope() as s:
        assert reports.performance_report(s, base_ccy="CHF", today=date(2026, 6, 17)) is None


def test_performance_pulse_dedup_and_force(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(reports.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(reports.telegram, "send_message", lambda t, **k: sent.append(t) or True)
    monkeypatch.setattr(reports, "performance_report", lambda *a, **k: "📊 pulse")
    day = date(2026, 6, 17)

    with session_scope() as s:
        r1 = reports.send_performance_pulse(s, today=day)
    assert r1["sent"] is True and len(sent) == 1

    # Same day again → deduped.
    with session_scope() as s:
        r2 = reports.send_performance_pulse(s, today=day)
    assert r2["sent"] is False and r2["skipped"] == "already sent today"
    assert len(sent) == 1

    # force bypasses the daily dedup.
    with session_scope() as s:
        r3 = reports.send_performance_pulse(s, today=day, force=True)
    assert r3["sent"] is True and len(sent) == 2


def test_performance_pulse_skips_when_no_data(monkeypatch):
    monkeypatch.setattr(reports.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(reports.telegram, "send_message", lambda t, **k: (_ for _ in ()).throw(AssertionError("should not send")))
    monkeypatch.setattr(reports, "performance_report", lambda *a, **k: None)
    with session_scope() as s:
        r = reports.send_performance_pulse(s, today=date(2026, 6, 17))
    assert r["sent"] is False and r["skipped"] == "no data"
