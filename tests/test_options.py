"""Tests for the options analytics module + /options endpoints.

Covers OCC parsing, /options/trades filtering, /options/eae aggregation,
premium time-series, P&L by underlying, and assignment-risk surface.
All IDs are synthetic.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from ibkr_backend.analytics.filters import resolve_period
from ibkr_backend.analytics.options import (
    enrich_option,
    options_assignment_rate,
    options_assignment_risk,
    options_concentration,
    options_dte_buckets,
    options_eae_list,
    options_open_positions,
    options_pnl_attribution,
    options_pnl_by_underlying,
    options_premium_timeseries,
    options_summary,
    options_trades_list,
    parse_occ_symbol,
    parse_option_description,
)
from ibkr_backend.api.main import create_app
from ibkr_backend.db.models import (
    Account,
    CashTransaction,
    Instrument,
    MtmPerformanceUnderlying,
    OptionExerciseAssignment,
    PositionEod,
    Trade,
)
from ibkr_backend.db.session import SessionLocal


# ---------------------------------------------------------------- parsing


def test_parse_occ_canonical():
    c = parse_occ_symbol("ACME  260116C00033000")
    assert c.underlying == "ACME"
    assert c.expiry == date(2026, 1, 16)
    assert c.right == "C"
    assert c.strike == 33.0


def test_parse_occ_six_char_root_padded():
    c = parse_occ_symbol("SCHD  260417P00025000")
    assert c.underlying == "SCHD"
    assert c.right == "P"
    assert c.strike == 25.0
    assert c.expiry == date(2026, 4, 17)


def test_parse_occ_decimal_strike():
    # 170.5 strike = 00170500
    c = parse_occ_symbol("AAPL  240419P00170500")
    assert c.strike == 170.5


def test_parse_occ_garbage_returns_empty():
    c = parse_occ_symbol("AAPL")
    assert c.underlying is None and c.expiry is None


def test_parse_description_fallback():
    c = parse_option_description("ACME 16JAN26 33 C")
    assert c.underlying == "ACME"
    assert c.expiry == date(2026, 1, 16)
    assert c.right == "C"
    assert c.strike == 33.0


def test_parse_description_with_decimal():
    c = parse_option_description("NOVA 18DEC26 50.5 P")
    assert c.right == "P"
    assert c.strike == 50.5


def test_enrich_prefers_osi_over_description():
    c = enrich_option("ACME  260116C00033000", "WRONG 01JAN20 1 P")
    assert c.right == "C" and c.strike == 33.0


def test_enrich_falls_back_to_description_when_symbol_unparseable():
    c = enrich_option("HUMAN-ONLY", "ACME 16JAN26 33 C")
    assert c.underlying == "ACME"
    assert c.right == "C"


# ---------------------------------------------------------------- fixtures


@pytest.fixture()
def seed_options():
    """Insert one account, three OPT trades (1 open, 2 closed win+loss),
    one EAE row + the resulting STK buy, and a stock+option EOD snapshot.
    """
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.add_all([
            Instrument(conid=1001, symbol="ACME  260116C00033000", description="ACME 16JAN26 33 C", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=1002, symbol="ACME  260417P00080000", description="ACME 17APR26 80 P", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=1003, symbol="NOVA  261218C00050000", description="NOVA 18DEC26 50 C", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=2001, symbol="ACME", description="ACME Inc", asset_class="STK"),
        ])
        # 1) Short call closed at +500 P&L
        s.add(Trade(
            trade_id="T-1", ib_order_id="ORD-A", account_id="U99", conid=1001,
            symbol="ACME  260116C00033000", asset_class="OPT",
            currency="USD", multiplier=Decimal("100"),
            trade_date=date(2026, 1, 10), trade_datetime=datetime(2026, 1, 10, 14, 0),
            buy_sell="SELL", open_close="C", quantity=Decimal("-1"),
            trade_price=Decimal("5"), proceeds=Decimal("500"),
            ib_commission=Decimal("-1"), net_cash=Decimal("499"),
            fifo_pnl_realized=Decimal("500"), fx_rate_to_base=Decimal("1"),
        ))
        # 2) Long put closed at -200 P&L
        s.add(Trade(
            trade_id="T-2", ib_order_id="ORD-B", account_id="U99", conid=1002,
            symbol="ACME  260417P00080000", asset_class="OPT",
            currency="USD", multiplier=Decimal("100"),
            trade_date=date(2026, 2, 15), trade_datetime=datetime(2026, 2, 15, 10, 0),
            buy_sell="SELL", open_close="C", quantity=Decimal("-1"),
            trade_price=Decimal("3"), proceeds=Decimal("300"),
            ib_commission=Decimal("-1"), net_cash=Decimal("299"),
            fifo_pnl_realized=Decimal("-200"), fx_rate_to_base=Decimal("1"),
        ))
        # 3) Short call OPEN (no P&L yet)
        s.add(Trade(
            trade_id="T-3", ib_order_id="ORD-C", account_id="U99", conid=1003,
            symbol="NOVA  261218C00050000", asset_class="OPT",
            currency="USD", multiplier=Decimal("100"),
            trade_date=date(2026, 5, 1), trade_datetime=datetime(2026, 5, 1, 10, 0),
            buy_sell="SELL", open_close="O", quantity=Decimal("-1"),
            trade_price=Decimal("2"), proceeds=Decimal("200"),
            ib_commission=Decimal("-1"), net_cash=Decimal("199"),
            fifo_pnl_realized=None, fx_rate_to_base=Decimal("1"),
        ))
        # EAE: exercise + resulting stock buy
        s.add(OptionExerciseAssignment(
            eae_id="EAE-1", account_id="U99", conid=1001,
            symbol="ACME  260116C00033000",
            transaction_type="Exercise", event_date=date(2026, 1, 16),
            quantity=Decimal("-1"), multiplier=Decimal("100"),
            trade_price=Decimal("0"), proceeds=Decimal("0"),
            commission=None, realized_pnl=None, currency="USD",
        ))
        s.add(OptionExerciseAssignment(
            eae_id="EAE-2", account_id="U99", conid=2001, symbol="ACME",
            underlying_symbol="ACME",
            transaction_type="Buy", event_date=date(2026, 1, 16),
            quantity=Decimal("100"), trade_price=Decimal("33"),
            proceeds=Decimal("-3300"), currency="USD",
        ))
        # EOD snapshot: one open short call + the underlying stock close
        s.add(PositionEod(
            snapshot_date=date(2026, 5, 15), account_id="U99", conid=1003,
            symbol="NOVA  261218C00050000", asset_class="OPT",
            quantity=Decimal("-1"), multiplier=Decimal("100"),
            mark_price=Decimal("3"), position_value=Decimal("-300"),
            currency="USD", fx_rate_to_base=Decimal("1"),
        ))
        s.add(PositionEod(
            snapshot_date=date(2026, 5, 15), account_id="U99", conid=2002,
            symbol="NOVA", asset_class="STK",
            quantity=Decimal("0"), mark_price=Decimal("52"),  # underlying ITM vs 50C
            position_value=Decimal("0"), currency="USD",
            fx_rate_to_base=Decimal("1"),
        ))
        s.add(Instrument(conid=2002, symbol="NOVA", description="Sample Corp", asset_class="STK"))
        s.commit()


# ---------------------------------------------------------------- query layer


def test_trades_list_filters_to_OPT_only(seed_options):
    with SessionLocal() as s:
        out = options_trades_list(s)
    assert out["summary"]["total"] == 3
    assert out["summary"]["win"] == 1
    assert out["summary"]["loss"] == 1
    assert out["summary"]["win_rate"] == 0.5
    underlyings = {r["underlying"] for r in out["rows"]}
    assert underlyings == {"ACME", "NOVA"}


def test_trades_list_filters_by_underlying(seed_options):
    with SessionLocal() as s:
        out = options_trades_list(s, underlying="ACME")
    assert out["summary"]["total"] == 2
    assert all(r["underlying"] == "ACME" for r in out["rows"])


def test_trades_list_status_open(seed_options):
    with SessionLocal() as s:
        out = options_trades_list(s, status="open")
    assert out["summary"]["total"] == 1
    assert out["rows"][0]["is_open"] is True


def test_eae_list_categorizes_events(seed_options):
    with SessionLocal() as s:
        out = options_eae_list(s)
    counts = out["summary"]["counts"]
    assert counts["exercise"] == 1
    assert counts["buy"] == 1
    assert out["summary"]["total"] == 2


def test_premium_timeseries_monthly(seed_options):
    with SessionLocal() as s:
        out = options_premium_timeseries(s, bucket="month")
    by_bucket = {r["bucket"]: r for r in out["series"]}
    assert by_bucket["2026-01"]["net_premium_base"] == pytest.approx(499)
    assert by_bucket["2026-02"]["net_premium_base"] == pytest.approx(299)
    # Cumulative should monotonically grow with positive premium inflow
    last = out["series"][-1]
    assert last["cumulative_net_base"] == pytest.approx(499 + 299 + 199)


def test_pnl_by_underlying_orders_by_abs_pnl(seed_options):
    with SessionLocal() as s:
        out = options_pnl_by_underlying(s)
    by_u = {r["underlying"]: r for r in out["rows"]}
    assert by_u["ACME"]["realized_pnl_base"] == pytest.approx(300)
    assert by_u["ACME"]["win"] == 1 and by_u["ACME"]["loss"] == 1
    assert by_u["NOVA"]["realized_pnl_base"] == pytest.approx(0)


def test_assignment_risk_flags_itm_short_call(seed_options):
    with SessionLocal() as s:
        out = options_assignment_risk(s)
    assert out["snapshot_date"] == "2026-05-15"
    assert out["summary"]["shorts"] == 1
    r = out["rows"][0]
    assert r["underlying"] == "NOVA" and r["right"] == "C"
    assert r["strike"] == 50.0 and r["last_underlying_close"] == 52.0
    assert r["itm"] is True
    assert r["intrinsic_per_contract"] == pytest.approx(2.0)
    assert r["intrinsic_total_base"] == pytest.approx(200.0)  # 2 * 1 * 100


@pytest.fixture()
def seed_concentration():
    """Latest OPT snapshot with two underlyings; older snapshots and zero
    market-value rows must not affect HHI."""
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.add(Account(account_id="UOTHER", base_currency="USD"))
        s.add_all([
            Instrument(conid=3001, symbol="ACME  260116C00033000", description="", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=3002, symbol="ACME  260417P00080000", description="", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=3003, symbol="NOVA  261218C00050000", description="", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=3004, symbol="ZERO  261218C00050000", description="", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=3005, symbol="OLD   261218C00050000", description="", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=3006, symbol="OTHR  261218C00050000", description="", asset_class="OPT", multiplier=Decimal("100")),
        ])
        latest = date(2026, 5, 20)
        old = date(2026, 5, 19)
        rows = [
            ("U99", latest, 3001, "ACME  260116C00033000", Decimal("-1"), Decimal("-600")),
            ("U99", latest, 3002, "ACME  260417P00080000", Decimal("-1"), Decimal("-400")),
            ("U99", latest, 3003, "NOVA  261218C00050000", Decimal("-1"), Decimal("-300")),
            ("U99", latest, 3004, "ZERO  261218C00050000", Decimal("-1"), Decimal("0")),
            ("U99", old, 3005, "OLD   261218C00050000", Decimal("-1"), Decimal("-9999")),
            ("UOTHER", latest, 3006, "OTHR  261218C00050000", Decimal("-1"), Decimal("-9999")),
        ]
        for account_id, snapshot_date, conid, symbol, quantity, value in rows:
            s.add(PositionEod(
                snapshot_date=snapshot_date,
                account_id=account_id,
                conid=conid,
                symbol=symbol,
                asset_class="OPT",
                quantity=quantity,
                multiplier=Decimal("100"),
                mark_price=Decimal("1"),
                position_value=value,
                currency="USD",
                fx_rate_to_base=Decimal("1"),
            ))
        s.commit()


def test_options_concentration_hhi_uses_latest_snapshot_by_underlying(seed_concentration):
    with SessionLocal() as s:
        out = options_concentration(s, account_id="U99")

    assert out["snapshot_date"] == "2026-05-20"
    assert out["summary"]["positions"] == 3
    assert out["summary"]["underlyings"] == 2
    assert out["summary"]["total_abs_exposure_base"] == pytest.approx(1300.0)
    assert out["summary"]["net_position_value_base"] == pytest.approx(-1300.0)
    assert out["summary"]["top_underlying"] == "ACME"
    assert out["summary"]["top_share"] == pytest.approx(1000 / 1300)
    assert out["summary"]["hhi"] == pytest.approx((1000 / 1300) ** 2 + (300 / 1300) ** 2)
    assert out["summary"]["effective_names"] == pytest.approx(
        1 / ((1000 / 1300) ** 2 + (300 / 1300) ** 2)
    )

    by_underlying = {row["underlying"]: row for row in out["rows"]}
    assert set(by_underlying) == {"ACME", "NOVA"}
    assert by_underlying["ACME"]["positions"] == 2
    assert by_underlying["ACME"]["contracts"] == 2
    assert by_underlying["ACME"]["abs_exposure_base"] == pytest.approx(1000.0)
    assert by_underlying["NOVA"]["abs_exposure_base"] == pytest.approx(300.0)


def test_options_concentration_empty_when_no_snapshot():
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.commit()
        out = options_concentration(s, account_id="U99")

    assert out["snapshot_date"] is None
    assert out["rows"] == []
    assert out["summary"]["hhi"] is None
    assert out["summary"]["effective_names"] is None


def test_summary_aggregates(seed_options):
    with SessionLocal() as s:
        out = options_summary(s)
    assert out["trades"]["total"] == 3
    assert out["open_risk"]["shorts"] == 1
    assert out["open_risk"]["itm"] == 1


# ---------------------------------------------------------------- dte buckets


def _occ_for_expiry(expiry: date, root: str = "XYZ", right: str = "P", strike: int = 40) -> str:
    """Build a valid OSI symbol for an arbitrary expiry. Strike encoded × 1000."""
    return f"{root:<6}{expiry.strftime('%y%m%d')}{right}{strike * 1000:08d}"


def test_options_open_positions_includes_long_and_short_books():
    today = date.today()
    long_expiry = today + timedelta(days=180)
    short_expiry = today + timedelta(days=20)
    long_sym = _occ_for_expiry(long_expiry, root="FUND", right="C", strike=30)
    short_sym = _occ_for_expiry(short_expiry, root="SMR", right="P", strike=20)

    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.add_all([
            Instrument(conid=5001, symbol=long_sym, description="FUND call", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=5002, symbol=short_sym, description="SMR put", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=5003, symbol="FUND", description="Sample Fund", asset_class="STK"),
        ])
        s.add_all([
            PositionEod(
                snapshot_date=today,
                account_id="U99",
                conid=5001,
                symbol=long_sym,
                asset_class="OPT",
                quantity=Decimal("2"),
                multiplier=Decimal("100"),
                mark_price=Decimal("4"),
                position_value=Decimal("800"),
                cost_basis_money=Decimal("600"),
                fifo_pnl_unrealized=Decimal("200"),
                currency="USD",
                fx_rate_to_base=Decimal("1"),
            ),
            PositionEod(
                snapshot_date=today,
                account_id="U99",
                conid=5002,
                symbol=short_sym,
                asset_class="OPT",
                quantity=Decimal("-1"),
                multiplier=Decimal("100"),
                mark_price=Decimal("1.5"),
                position_value=Decimal("-150"),
                cost_basis_money=Decimal("-120"),
                fifo_pnl_unrealized=Decimal("-30"),
                currency="USD",
                fx_rate_to_base=Decimal("1"),
            ),
            PositionEod(
                snapshot_date=today,
                account_id="U99",
                conid=5003,
                symbol="FUND",
                asset_class="STK",
                quantity=Decimal("1"),
                mark_price=Decimal("35"),
                position_value=Decimal("35"),
                currency="USD",
                fx_rate_to_base=Decimal("1"),
            ),
        ])
        s.commit()

        out = options_open_positions(s, account_id="U99")

    assert out["snapshot_date"] == today.isoformat()
    assert out["summary"]["positions"] == 2
    assert out["summary"]["long_positions"] == 1
    assert out["summary"]["short_positions"] == 1
    assert out["summary"]["long_contracts"] == pytest.approx(2)
    assert out["summary"]["short_contracts"] == pytest.approx(1)
    assert out["summary"]["total_abs_exposure_base"] == pytest.approx(950)
    assert out["summary"]["net_position_value_base"] == pytest.approx(650)
    assert out["summary"]["unrealized_pnl_base"] == pytest.approx(170)
    assert out["summary"]["expiring_within_30d"] == 1

    by_underlying = {row["underlying"]: row for row in out["rows"]}
    assert by_underlying["FUND"]["side"] == "long"
    assert by_underlying["FUND"]["dte"] == 180
    assert by_underlying["FUND"]["moneyness_pct"] == pytest.approx((35 / 30) - 1)
    assert by_underlying["SMR"]["side"] == "short"


@pytest.fixture()
def seed_dte_buckets():
    """Four open SHORT positions, one per DTE bucket, plus one LONG (qty > 0)
    that must NOT count, plus one expired (DTE < 0) that must be skipped."""
    from datetime import timedelta
    today = date.today()
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        seed = [
            # (label, conid, dte_offset, qty)
            ("safe", 6001, 200, Decimal("-1")),
            ("watch", 6002, 30, Decimal("-1")),
            ("manage", 6003, 15, Decimal("-1")),
            ("critical", 6004, 5, Decimal("-1")),
            ("long_skip", 6005, 30, Decimal("+1")),   # long → must be excluded
            ("expired", 6006, -3, Decimal("-1")),     # already expired → skipped
        ]
        for label, conid, offset, qty in seed:
            exp = today + timedelta(days=offset)
            sym = _occ_for_expiry(exp, root=f"X{label[:1].upper()}", strike=40)
            s.add(Instrument(conid=conid, symbol=sym, description="", asset_class="OPT", multiplier=Decimal("100")))
            s.add(PositionEod(
                snapshot_date=today, account_id="U99", conid=conid, symbol=sym,
                asset_class="OPT", quantity=qty, multiplier=Decimal("100"),
                mark_price=Decimal("1"), position_value=Decimal("100"),
                currency="USD", fx_rate_to_base=Decimal("1"),
            ))
        s.commit()


def test_options_dte_buckets_classifies_shorts_only(seed_dte_buckets):
    """One short per bucket; long is excluded; expired (negative DTE) is
    skipped and counted in `expired_skipped`."""
    with SessionLocal() as s:
        out = options_dte_buckets(s)
    counts = {b["label"]: b["count"] for b in out["buckets"]}
    assert counts == {"safe": 1, "watch": 1, "manage": 1, "critical": 1}
    assert out["summary"]["shorts_open"] == 5  # 4 active + 1 expired (all qty < 0)
    assert out["summary"]["manage_count"] == 2  # manage + critical
    assert out["summary"]["expired_skipped"] == 1


def test_options_dte_buckets_empty_when_no_snapshot():
    """With no PositionEod rows, the endpoint returns zero counts and a null
    snapshot_date instead of erroring."""
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.commit()
        out = options_dte_buckets(s)
    assert out["snapshot_date"] is None
    assert out["summary"]["shorts_open"] == 0
    assert out["summary"]["manage_count"] == 0
    assert all(b["count"] == 0 for b in out["buckets"])


# ---------------------------------------------------------------- attribution


@pytest.fixture()
def seed_attribution():
    """Wheel-cycle plus an isolated equity play plus net interest. Validates
    the Sinclair/Hull decomposition before any 'skill' claim — see the
    `options_pnl_attribution` docstring."""
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.flush()
        s.add_all([
            Instrument(conid=7001, symbol="WHL   260918P00040000", description="", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=7002, symbol="WHL", description="Wheel Stock", asset_class="STK"),
            Instrument(conid=7003, symbol="ZZZ", description="Pure Equity Play", asset_class="STK"),
        ])
        s.flush()
        # Wheel cycle: short put on WHL closed at +200 (the short-premium edge)
        s.add(Trade(
            trade_id="T-WHL-OPT", ib_order_id="ORD-1", account_id="U99", conid=7001,
            symbol="WHL   260918P00040000", asset_class="OPT",
            currency="USD", multiplier=Decimal("100"),
            trade_date=date(2026, 3, 1), trade_datetime=datetime(2026, 3, 1, 14, 0),
            buy_sell="BUY", open_close="C", quantity=Decimal("1"),
            trade_price=Decimal("0.5"), proceeds=Decimal("-50"),
            ib_commission=Decimal("-1"), net_cash=Decimal("-51"),
            fifo_pnl_realized=Decimal("200"), fx_rate_to_base=Decimal("1"),
        ))
        # Shares received through assignment of the short put (EAE stock leg).
        s.add(OptionExerciseAssignment(
            eae_id="EAE-WHL", account_id="U99", conid=7002, symbol="WHL",
            underlying_symbol="WHL", transaction_type="Buy",
            event_date=date(2026, 3, 5), quantity=Decimal("100"), multiplier=Decimal("1"),
        ))
        # WHL stock sold from assigned shares: +50 capital gain
        s.add(Trade(
            trade_id="T-WHL-STK", ib_order_id="ORD-2", account_id="U99", conid=7002,
            symbol="WHL", asset_class="STK",
            currency="USD", trade_date=date(2026, 4, 1),
            trade_datetime=datetime(2026, 4, 1, 14, 0),
            buy_sell="SELL", open_close="C", quantity=Decimal("-100"),
            trade_price=Decimal("42"), proceeds=Decimal("4200"),
            ib_commission=Decimal("-1"), net_cash=Decimal("4199"),
            fifo_pnl_realized=Decimal("50"), fx_rate_to_base=Decimal("1"),
        ))
        # Pure equity play (no options activity on ZZZ): +30
        s.add(Trade(
            trade_id="T-ZZZ-STK", ib_order_id="ORD-3", account_id="U99", conid=7003,
            symbol="ZZZ", asset_class="STK",
            currency="USD", trade_date=date(2026, 4, 15),
            trade_datetime=datetime(2026, 4, 15, 14, 0),
            buy_sell="SELL", open_close="C", quantity=Decimal("-10"),
            trade_price=Decimal("110"), proceeds=Decimal("1100"),
            ib_commission=Decimal("-1"), net_cash=Decimal("1099"),
            fifo_pnl_realized=Decimal("30"), fx_rate_to_base=Decimal("1"),
        ))
        # Dividend on WHL: gross +10, withholding -2 → net +8 to attribution
        s.add_all([
            CashTransaction(
                transaction_id="DIV-WHL", account_id="U99", conid=7002,
                symbol="WHL", type="Dividends", amount=Decimal("10"),
                currency="USD", fx_rate_to_base=Decimal("1"),
                event_datetime=datetime(2026, 3, 20, 0, 0, 0),
                settle_date=date(2026, 3, 22),
            ),
            CashTransaction(
                transaction_id="WHT-WHL", account_id="U99", conid=7002,
                symbol="WHL", type="Withholding Tax", amount=Decimal("-2"),
                currency="USD", fx_rate_to_base=Decimal("1"),
                event_datetime=datetime(2026, 3, 20, 0, 0, 0),
                settle_date=date(2026, 3, 22),
            ),
            # Dividend on ZZZ (non-options): +5
            CashTransaction(
                transaction_id="DIV-ZZZ", account_id="U99", conid=7003,
                symbol="ZZZ", type="Dividends", amount=Decimal("5"),
                currency="USD", fx_rate_to_base=Decimal("1"),
                event_datetime=datetime(2026, 4, 5, 0, 0, 0),
                settle_date=date(2026, 4, 7),
            ),
            # Net interest: received 12 - paid 4 = +8
            CashTransaction(
                transaction_id="INT-RECV", account_id="U99", conid=None, symbol=None,
                type="Broker Interest Received", amount=Decimal("12"),
                currency="USD", fx_rate_to_base=Decimal("1"),
                event_datetime=datetime(2026, 2, 1, 0, 0, 0),
                settle_date=date(2026, 2, 1),
            ),
            CashTransaction(
                transaction_id="INT-PAID", account_id="U99", conid=None, symbol=None,
                type="Broker Interest Paid", amount=Decimal("-4"),
                currency="USD", fx_rate_to_base=Decimal("1"),
                event_datetime=datetime(2026, 2, 15, 0, 0, 0),
                settle_date=date(2026, 2, 15),
            ),
        ])
        s.commit()


def test_attribution_separates_wheel_edge_from_collateral(seed_attribution):
    """The Sinclair/Hull decomposition: don't conflate 'I made money on
    options' with 'the stock I got assigned went up'. Endpoint must split
    short-premium edge from wheel-stock realized P&L from wheel-dividend
    income from non-options stock P&L from non-options dividends from
    interest."""
    with SessionLocal() as s:
        out = options_pnl_attribution(s)
    t = out["totals"]
    assert t["short_premium_base"] == pytest.approx(200.0)
    assert t["wheel_stock_base"] == pytest.approx(50.0)
    # Dividends net of withholding tax — what actually hit the account.
    assert t["wheel_dividends_base"] == pytest.approx(8.0)  # 10 gross - 2 wht
    assert t["wheel_withholding_base"] == pytest.approx(-2.0)
    assert t["non_options_stock_base"] == pytest.approx(30.0)
    assert t["non_options_dividends_base"] == pytest.approx(5.0)
    assert t["interest_base"] == pytest.approx(8.0)  # 12 received - 4 paid
    # Grand total = sum of all components
    expected = 200.0 + 50.0 + 8.0 + 30.0 + 5.0 + 8.0
    assert t["grand_total_base"] == pytest.approx(expected)


def test_attribution_by_underlying_only_includes_options_traded_names(seed_attribution):
    """`by_underlying` rolls up wheel-related stocks (those with OPT
    activity). Non-options equity rolls into the totals block but not the
    per-underlying breakdown."""
    with SessionLocal() as s:
        out = options_pnl_attribution(s)
    by = {r["underlying"]: r for r in out["by_underlying"]}
    assert set(by) == {"WHL"}  # ZZZ is pure equity → not in this list
    whl = by["WHL"]
    assert whl["opt_pnl_base"] == pytest.approx(200.0)
    assert whl["stk_pnl_base"] == pytest.approx(50.0)
    assert whl["dividends_net_base"] == pytest.approx(8.0)
    assert whl["total_base"] == pytest.approx(258.0)


def test_assignment_rate_categorises_disposals_per_underlying():
    """Per-underlying OPT disposal breakdown: bought_to_close from Trade
    rows with `open_close LIKE 'C%' and buy_sell='BUY'`; exercise /
    assignment / expiration from EAE rows by `transaction_type`
    substring. The wheel-tracker's centerpiece metric."""
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.flush()
        s.add_all([
            Instrument(conid=8101, symbol="WHL   260918P00040000", description="", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=8102, symbol="WHL   261218P00045000", description="", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=8103, symbol="WHL   270117C00060000", description="", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=8104, symbol="OTH   260918C00050000", description="", asset_class="OPT", multiplier=Decimal("100")),
        ])
        s.flush()
        # 1 bought-to-close + 1 expiration + 1 assignment on WHL
        s.add(Trade(
            trade_id="T-BTC", ib_order_id="O1", account_id="U99", conid=8101,
            symbol="WHL   260918P00040000", asset_class="OPT",
            currency="USD", multiplier=Decimal("100"),
            trade_date=date(2026, 3, 1), trade_datetime=datetime(2026, 3, 1, 14, 0),
            buy_sell="BUY", open_close="C", quantity=Decimal("1"),
            trade_price=Decimal("1"), proceeds=Decimal("-100"),
            ib_commission=Decimal("0"), net_cash=Decimal("-100"),
            fifo_pnl_realized=Decimal("400"), fx_rate_to_base=Decimal("1"),
        ))
        s.add(OptionExerciseAssignment(
            eae_id="E-EXP", account_id="U99", conid=8102,
            symbol="WHL   261218P00045000", underlying_symbol="WHL",
            transaction_type="Expiration", event_date=date(2026, 12, 18),
            quantity=Decimal("-1"), multiplier=Decimal("100"),
            trade_price=Decimal("0"), proceeds=Decimal("0"),
            currency="USD",
        ))
        s.add(OptionExerciseAssignment(
            eae_id="E-ASS", account_id="U99", conid=8103,
            symbol="WHL   270117C00060000", underlying_symbol="WHL",
            transaction_type="Assignment", event_date=date(2027, 1, 17),
            quantity=Decimal("-1"), multiplier=Decimal("100"),
            trade_price=Decimal("60"), proceeds=Decimal("6000"),
            currency="USD",
        ))
        # 1 sold-to-close on OTH (long position)
        s.add(Trade(
            trade_id="T-STC", ib_order_id="O2", account_id="U99", conid=8104,
            symbol="OTH   260918C00050000", asset_class="OPT",
            currency="USD", multiplier=Decimal("100"),
            trade_date=date(2026, 4, 1), trade_datetime=datetime(2026, 4, 1, 14, 0),
            buy_sell="SELL", open_close="C", quantity=Decimal("-1"),
            trade_price=Decimal("2"), proceeds=Decimal("200"),
            ib_commission=Decimal("0"), net_cash=Decimal("200"),
            fifo_pnl_realized=Decimal("100"), fx_rate_to_base=Decimal("1"),
        ))
        # "Buy" EAE row (resulting stock leg) must be ignored.
        s.add(OptionExerciseAssignment(
            eae_id="E-STKBUY", account_id="U99", conid=8101,
            symbol="WHL", underlying_symbol="WHL",
            transaction_type="Buy", event_date=date(2026, 3, 2),
            quantity=Decimal("100"), trade_price=Decimal("40"),
            proceeds=Decimal("-4000"), currency="USD",
        ))
        s.commit()

        out = options_assignment_rate(s)

    by = {r["underlying"]: r for r in out["by_underlying"]}
    assert set(by) == {"WHL", "OTH"}

    whl = by["WHL"]
    assert whl["bought_to_close"] == 1
    assert whl["expiration"] == 1
    assert whl["assignment"] == 1
    assert whl["exercise"] == 0
    assert whl["sold_to_close"] == 0
    assert whl["total_disposals"] == 3
    assert whl["stock_transfer_rate"] == pytest.approx(1/3)
    assert whl["expiration_rate"] == pytest.approx(1/3)

    oth = by["OTH"]
    assert oth["sold_to_close"] == 1
    assert oth["total_disposals"] == 1

    t = out["totals"]
    assert t["bought_to_close"] == 1
    assert t["sold_to_close"] == 1
    assert t["expiration"] == 1
    assert t["assignment"] == 1
    assert t["total_disposals"] == 4
    assert t["stock_transfer_rate"] == pytest.approx(0.25)


def test_attribution_empty_db_returns_zeros():
    """No trades, no cash tx → all zeros, no errors. Idempotent shape."""
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.commit()
        out = options_pnl_attribution(s)
    assert out["by_underlying"] == []
    t = out["totals"]
    assert t["short_premium_base"] == 0
    assert t["wheel_stock_base"] == 0
    assert t["grand_total_base"] == 0


def test_attribution_counts_compound_close_codes():
    """IBKR Flex emits compound close codes like `C;IA`, `C;P`, `C;FPA;P`
    when a close coincides with an internal adjustment / partial fill /
    other annotation. The attribution and trade-list logic must treat ANY
    code starting with 'C' as closed; using strict equality drops valid
    realized-P&L rows."""
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.flush()
        s.add(Instrument(conid=8001, symbol="CMP", description="Compound Co", asset_class="STK"))
        s.flush()
        for tid, code, pnl in (
            ("T-C", "C", 100),
            ("T-CIA", "C;IA", 50),
            ("T-CP", "C;P", -30),
            ("T-CFPAP", "C;FPA;P", 10),
            ("T-O", "O", 0),  # open — must be excluded
        ):
            s.add(Trade(
                trade_id=tid, ib_order_id=f"ORD-{tid}", account_id="U99",
                conid=8001, symbol="CMP", asset_class="STK",
                currency="USD", trade_date=date(2026, 4, 1),
                trade_datetime=datetime(2026, 4, 1, 14, 0),
                buy_sell="SELL", open_close=code, quantity=Decimal("-1"),
                trade_price=Decimal("10"), proceeds=Decimal("10"),
                ib_commission=Decimal("0"), net_cash=Decimal("10"),
                fifo_pnl_realized=Decimal(str(pnl)), fx_rate_to_base=Decimal("1"),
            ))
        s.commit()

        out = options_pnl_attribution(s)

    # No OPT activity → no wheel underlyings; CMP counts as non-options stock.
    # Sum of closes (any C-prefixed code): 100 + 50 - 30 + 10 = 130
    assert out["totals"]["non_options_stock_base"] == pytest.approx(130.0)


def test_attribution_parses_dividend_symbol_from_description():
    """IBKR Flex emits dividend CashTransaction rows with symbol=NULL and
    conid=NULL; the underlying is embedded in the description field
    ('TICKER(ISIN) Cash Dividend ...'). The attribution must parse the
    symbol from the description and match it to OPT activity for
    wheel-vs-non-wheel split. Without this, valid dividend rows silently
    sum to zero."""
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.flush()
        # SCHD has options activity (wheel underlying); ADP does not.
        s.add_all([
            Instrument(conid=9101, symbol="SCHD  260417P00025000", description="", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=9102, symbol="SCHD", description="Schwab US Dividend", asset_class="STK"),
        ])
        s.flush()
        s.add(Trade(
            trade_id="T-OPT", ib_order_id="ORD-1", account_id="U99", conid=9101,
            symbol="SCHD  260417P00025000", asset_class="OPT",
            currency="USD", multiplier=Decimal("100"),
            trade_date=date(2026, 2, 1), trade_datetime=datetime(2026, 2, 1, 14, 0),
            buy_sell="BUY", open_close="C", quantity=Decimal("1"),
            trade_price=Decimal("1"), proceeds=Decimal("-100"),
            ib_commission=Decimal("0"), net_cash=Decimal("-100"),
            fifo_pnl_realized=Decimal("75"), fx_rate_to_base=Decimal("1"),
        ))
        s.add_all([
            # SCHD dividend: symbol NULL, ticker in description (wheel)
            CashTransaction(
                transaction_id="D-SCHD", account_id="U99", conid=None, symbol=None,
                type="Dividends", amount=Decimal("12"),
                currency="USD", fx_rate_to_base=Decimal("1"),
                event_datetime=datetime(2026, 3, 1, 0, 0, 0),
                settle_date=date(2026, 3, 3),
                description="SCHD(US8085247495) Cash Dividend USD 0.75 per Share (Ordinary Dividend)",
            ),
            # ADP dividend: symbol NULL, ticker in description (non-wheel)
            CashTransaction(
                transaction_id="D-ADP", account_id="U99", conid=None, symbol=None,
                type="Dividends", amount=Decimal("4"),
                currency="USD", fx_rate_to_base=Decimal("1"),
                event_datetime=datetime(2026, 3, 5, 0, 0, 0),
                settle_date=date(2026, 3, 7),
                description="ADP(US0530151036) Cash Dividend USD 1.40 per Share (Ordinary Dividend)",
            ),
        ])
        s.commit()

        out = options_pnl_attribution(s)

    # SCHD is wheel-related (OPT activity) → dividend in wheel bucket.
    # ADP is not (no OPT) → dividend in non-options bucket.
    assert out["totals"]["wheel_dividends_base"] == pytest.approx(12.0)
    assert out["totals"]["non_options_dividends_base"] == pytest.approx(4.0)
    schd = next((r for r in out["by_underlying"] if r["underlying"] == "SCHD"), None)
    assert schd is not None
    assert schd["dividends_net_base"] == pytest.approx(12.0)


def test_options_trades_list_emits_capture_pct_for_short_close():
    """capture_pct = realized_pnl / initial_credit, computed from the close
    row alone using capture = pnl / (pnl + |close_proceeds_base|). Only
    meaningful for BUY-TO-CLOSE of a short premium position; SELL-TO-CLOSE
    of a long must return None. Validates the tastytrade 'manage at 50%'
    doctrine against the user's own trades."""
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        # Cash-secured put short, bought to close at +60% of credit kept.
        # Open: SELL TO OPEN at $5 → credit $500.
        # Close: BUY TO CLOSE at $2 → debit $200. PnL = $300 = 60% of $500.
        s.add(Instrument(conid=9001, symbol="XYZ   260918P00040000", description="", asset_class="OPT", multiplier=Decimal("100")))
        s.add(Trade(
            trade_id="T-CSP-CLOSE", ib_order_id="ORD-1", account_id="U99", conid=9001,
            symbol="XYZ   260918P00040000", asset_class="OPT",
            currency="USD", multiplier=Decimal("100"),
            trade_date=date(2026, 4, 15), trade_datetime=datetime(2026, 4, 15, 14, 0),
            buy_sell="BUY", open_close="C", quantity=Decimal("1"),
            trade_price=Decimal("2"), proceeds=Decimal("-200"),
            ib_commission=Decimal("-1"), net_cash=Decimal("-201"),
            fifo_pnl_realized=Decimal("300"), fx_rate_to_base=Decimal("1"),
        ))
        # Long call sold to close — capture_pct undefined for this direction.
        s.add(Instrument(conid=9002, symbol="ABC   260918C00050000", description="", asset_class="OPT", multiplier=Decimal("100")))
        s.add(Trade(
            trade_id="T-LONG-CLOSE", ib_order_id="ORD-2", account_id="U99", conid=9002,
            symbol="ABC   260918C00050000", asset_class="OPT",
            currency="USD", multiplier=Decimal("100"),
            trade_date=date(2026, 4, 15), trade_datetime=datetime(2026, 4, 15, 14, 0),
            buy_sell="SELL", open_close="C", quantity=Decimal("-1"),
            trade_price=Decimal("4"), proceeds=Decimal("400"),
            ib_commission=Decimal("-1"), net_cash=Decimal("399"),
            fifo_pnl_realized=Decimal("100"), fx_rate_to_base=Decimal("1"),
        ))
        s.commit()

        out = options_trades_list(s)

    by_id = {r["trade_id"]: r for r in out["rows"]}
    csp = by_id["T-CSP-CLOSE"]
    assert csp["capture_pct"] == pytest.approx(0.60)  # 300 / (300 + 200) = 0.60
    long_close = by_id["T-LONG-CLOSE"]
    assert long_close["capture_pct"] is None  # SELL-to-close → undefined


def test_options_trades_list_emits_capital_at_risk_and_roc(seed_options):
    """Each row should expose capital_at_risk_base: strike × multiplier ×
    |qty| × fx for short premium (CSPs/CCs) and the opening debit for long
    options closed by a SELL. roc_pct = pnl_base / capital_at_risk_base × 100
    for closed rows; None for opens."""
    with SessionLocal() as s:
        out = options_trades_list(s)
    by_id = {r["trade_id"]: r for r in out["rows"]}

    # T-1 / T-2 close with SELL (long options): capital at risk is the debit
    # paid to open = close proceeds - realized P&L.
    t1 = by_id["T-1"]  # proceeds 500, pnl +500 -> opening debit 0 -> undefined
    assert t1["capital_at_risk_base"] is None
    assert t1["roc_pct"] is None

    t2 = by_id["T-2"]  # proceeds 300, pnl -200 -> opening debit 500
    assert t2["capital_at_risk_base"] == pytest.approx(500.0)
    assert t2["roc_pct"] == pytest.approx(-40.0)

    t3 = by_id["T-3"]  # open short call NOVA, strike 50, qty -1
    assert t3["capital_at_risk_base"] == pytest.approx(5000.0)
    assert t3["roc_pct"] is None  # no realized P&L yet


def test_summary_emits_expectancy_profit_factor_avg(seed_options):
    """seed_options has 1 win (+500), 1 loss (-200), 1 open. Expectancy +
    derived stats must follow the Sinclair/Hull/tastytrade formulas so the
    Opciones header can show edge per trade, not just win rate."""
    with SessionLocal() as s:
        out = options_summary(s)
    sumry = out["trades"]
    assert sumry["win"] == 1 and sumry["loss"] == 1
    assert sumry["avg_winner_base"] == pytest.approx(500.0)
    assert sumry["avg_loser_base"] == pytest.approx(-200.0)
    # profit_factor = win_sum / |loss_sum| = 500 / 200
    assert sumry["profit_factor"] == pytest.approx(2.5)
    # expectancy = P(win)·avg_win + P(loss)·avg_loss = 0.5·500 + 0.5·(-200)
    assert sumry["expectancy_base"] == pytest.approx(150.0)


# ---------------------------------------------------------------- multi-leg


@pytest.fixture()
def seed_multi_leg_spread():
    """Seed two OPT trades that share the same `ib_order_id` + `trade_date`,
    forming a bull call spread (BUY 40C + SELL 45C, same expiry), plus one
    unrelated single-leg OPT trade with its own order_id."""
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.add_all([
            Instrument(conid=3001, symbol="XYZ   260918C00040000", description="XYZ 18SEP26 40 C", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=3002, symbol="XYZ   260918C00045000", description="XYZ 18SEP26 45 C", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=3003, symbol="ACME  260116C00033000", description="ACME 16JAN26 33 C", asset_class="OPT", multiplier=Decimal("100")),
        ])
        # Leg 1 of vertical: BUY 40C (long lower strike)
        s.add(Trade(
            trade_id="T-V1", ib_order_id="ORD-VS", account_id="U99", conid=3001,
            symbol="XYZ   260918C00040000", asset_class="OPT",
            currency="USD", multiplier=Decimal("100"),
            trade_date=date(2026, 4, 1), trade_datetime=datetime(2026, 4, 1, 14, 0),
            buy_sell="BUY", open_close="O", quantity=Decimal("1"),
            trade_price=Decimal("3"), proceeds=Decimal("-300"),
            ib_commission=Decimal("-1"), net_cash=Decimal("-301"),
            fifo_pnl_realized=None, fx_rate_to_base=Decimal("1"),
        ))
        # Leg 2 of vertical: SELL 45C (short upper strike)
        s.add(Trade(
            trade_id="T-V2", ib_order_id="ORD-VS", account_id="U99", conid=3002,
            symbol="XYZ   260918C00045000", asset_class="OPT",
            currency="USD", multiplier=Decimal("100"),
            trade_date=date(2026, 4, 1), trade_datetime=datetime(2026, 4, 1, 14, 0),
            buy_sell="SELL", open_close="O", quantity=Decimal("-1"),
            trade_price=Decimal("1"), proceeds=Decimal("100"),
            ib_commission=Decimal("-1"), net_cash=Decimal("99"),
            fifo_pnl_realized=None, fx_rate_to_base=Decimal("1"),
        ))
        # Single-leg unrelated short call
        s.add(Trade(
            trade_id="T-SOLO", ib_order_id="ORD-SOLO", account_id="U99", conid=3003,
            symbol="ACME  260116C00033000", asset_class="OPT",
            currency="USD", multiplier=Decimal("100"),
            trade_date=date(2026, 1, 10), trade_datetime=datetime(2026, 1, 10, 14, 0),
            buy_sell="SELL", open_close="O", quantity=Decimal("-1"),
            trade_price=Decimal("5"), proceeds=Decimal("500"),
            ib_commission=Decimal("-1"), net_cash=Decimal("499"),
            fifo_pnl_realized=None, fx_rate_to_base=Decimal("1"),
        ))
        s.commit()


def test_options_trades_list_labels_multi_leg_vertical(seed_multi_leg_spread):
    """Two OPT trades sharing `ib_order_id` + `trade_date` form a Vertical
    spread; the endpoint should annotate both rows with strategy="Vertical
    spread", strategy_legs=2, is_multi_leg=True. The unrelated solo trade
    must keep strategy=None and is_multi_leg=False."""
    with SessionLocal() as s:
        out = options_trades_list(s)
    by_id = {r["trade_id"]: r for r in out["rows"]}
    assert set(by_id) == {"T-V1", "T-V2", "T-SOLO"}

    for tid in ("T-V1", "T-V2"):
        r = by_id[tid]
        assert r["is_multi_leg"] is True
        assert r["strategy"] == "Vertical spread"
        assert r["strategy_legs"] == 2

    solo = by_id["T-SOLO"]
    assert solo["is_multi_leg"] is False
    assert solo["strategy"] is None
    assert solo["strategy_legs"] == 1


def test_options_trades_list_falls_back_to_generic_multi_leg_label():
    """Three same-side OPT legs sharing an order_id don't match any named
    pattern → endpoint annotates as the generic 'Multi-leg (N)' label, not
    None, so the UI still shows it grouped."""
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.add_all([
            Instrument(conid=4001, symbol="XYZ   260918C00040000", description="", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=4002, symbol="XYZ   260918C00045000", description="", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=4003, symbol="XYZ   260918C00050000", description="", asset_class="OPT", multiplier=Decimal("100")),
        ])
        for tid, conid, sym in (
            ("T-M1", 4001, "XYZ   260918C00040000"),
            ("T-M2", 4002, "XYZ   260918C00045000"),
            ("T-M3", 4003, "XYZ   260918C00050000"),
        ):
            s.add(Trade(
                trade_id=tid, ib_order_id="ORD-3LEG", account_id="U99", conid=conid,
                symbol=sym, asset_class="OPT",
                currency="USD", multiplier=Decimal("100"),
                trade_date=date(2026, 4, 1), trade_datetime=datetime(2026, 4, 1, 14, 0),
                buy_sell="BUY", open_close="O", quantity=Decimal("1"),
                trade_price=Decimal("1"), proceeds=Decimal("-100"),
                ib_commission=Decimal("-1"), net_cash=Decimal("-101"),
                fifo_pnl_realized=None, fx_rate_to_base=Decimal("1"),
            ))
        s.commit()

        out = options_trades_list(s)

    for r in out["rows"]:
        assert r["is_multi_leg"] is True
        assert r["strategy"] == "Multi-leg (3)"
        assert r["strategy_legs"] == 3


def test_options_trades_list_groups_across_page_boundary():
    """A 4-leg iron condor must be classified even if some legs fall outside
    the requested page — sibling fetch is keyed by `ib_order_id`, not by the
    page slice."""
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        legs = [
            ("T-IC1", 5001, "XYZ   260918P00035000", "BUY", Decimal("1")),
            ("T-IC2", 5002, "XYZ   260918P00040000", "SELL", Decimal("-1")),
            ("T-IC3", 5003, "XYZ   260918C00050000", "SELL", Decimal("-1")),
            ("T-IC4", 5004, "XYZ   260918C00055000", "BUY", Decimal("1")),
        ]
        for tid, conid, sym, side, qty in legs:
            s.add(Instrument(conid=conid, symbol=sym, description="", asset_class="OPT", multiplier=Decimal("100")))
            s.add(Trade(
                trade_id=tid, ib_order_id="ORD-IC", account_id="U99", conid=conid,
                symbol=sym, asset_class="OPT",
                currency="USD", multiplier=Decimal("100"),
                trade_date=date(2026, 4, 1), trade_datetime=datetime(2026, 4, 1, 14, 0),
                buy_sell=side, open_close="O", quantity=qty,
                trade_price=Decimal("1"), proceeds=Decimal("100"),
                ib_commission=Decimal("-1"), net_cash=Decimal("99"),
                fifo_pnl_realized=None, fx_rate_to_base=Decimal("1"),
            ))
        s.commit()

        # Request only 2 rows on the first page — the classifier still needs
        # all 4 sibling legs to detect "Iron condor".
        out = options_trades_list(s, limit=2, offset=0)
    assert len(out["rows"]) == 2
    for r in out["rows"]:
        assert r["strategy"] == "Iron condor"
        assert r["strategy_legs"] == 4
        assert r["is_multi_leg"] is True


# ---------------------------------------------------------------- HTTP


def test_options_endpoints_smoke(seed_options):
    client = TestClient(create_app())
    assert client.get("/options/summary").status_code == 200
    assert client.get("/options/trades").status_code == 200
    assert client.get("/options/trades?underlying=ACME&right=C").status_code == 200
    assert client.get("/options/eae").status_code == 200
    assert client.get("/options/premium-timeseries?bucket=month").status_code == 200
    assert client.get("/options/by-underlying").status_code == 200
    assert client.get("/options/open-positions").status_code == 200
    assert client.get("/options/assignment-risk").status_code == 200
    assert client.get("/options/concentration").status_code == 200
    assert client.get("/options/trades?right=X").status_code == 422  # validation
    assert client.get("/options/trades?status=foo").status_code == 422
    assert client.get("/options/premium-timeseries?bucket=hourly").status_code == 422
    assert client.get("/options/trades?limit=0").status_code == 422  # ge=1


# ============================================================== EDGE CASES

# ---- OCC parser edge cases ----------------------------------------------------


def test_parse_occ_year_2099_boundary():
    c = parse_occ_symbol("SPY   991231C00500000")
    assert c.expiry == date(2099, 12, 31)


def test_parse_occ_year_2000_boundary():
    c = parse_occ_symbol("SPY   000115P00100000")
    assert c.expiry == date(2000, 1, 15)


def test_parse_occ_fractional_strike():
    # 4.5 strike → 00004500; 0.50 strike → 00000500
    half = parse_occ_symbol("XYZ   260619C00004500")
    cents = parse_occ_symbol("XYZ   260619P00000500")
    assert half.strike == 4.5
    assert cents.strike == 0.5


def test_parse_occ_high_strike():
    # 9999.999 max strike representable
    c = parse_occ_symbol("BRK.A 260619C09999999")
    assert c.strike == pytest.approx(9999.999)


def test_parse_occ_root_with_dot():
    # BRK.B is a real ticker with a dot in the root
    c = parse_occ_symbol("BRK.B 260619C00400000")
    assert c.underlying == "BRK.B"


def test_parse_occ_weekly_root_with_w_suffix():
    # SPXW is the standard 4-char weekly root
    c = parse_occ_symbol("SPXW  260619P05500000")
    assert c.underlying == "SPXW"


def test_parse_occ_invalid_date_returns_none_expiry():
    # 261332 = month 13 day 32 (impossible) → expiry=None but other fields parse
    c = parse_occ_symbol("XYZ   261332C00050000")
    assert c.expiry is None
    assert c.right == "C"
    assert c.strike == 50.0


def test_parse_occ_short_or_long_strike_field_rejected():
    # 7-digit strike is malformed
    c = parse_occ_symbol("XYZ   260619C0005000")
    assert c.expiry is None and c.right is None


def test_parse_occ_none_input():
    assert parse_occ_symbol(None).underlying is None
    assert parse_option_description(None).underlying is None
    c = enrich_option(None, None)
    assert c.underlying is None and c.expiry is None


def test_parse_description_three_letter_month_case_insensitive():
    # IBKR sometimes emits lowercase or mixed-case month abbreviations
    c = parse_option_description("ACME 16jan26 33 P")
    assert c.expiry == date(2026, 1, 16)


def test_parse_description_with_ticker_containing_dot():
    c = parse_option_description("BRK.B 19APR24 400 C")
    assert c.underlying == "BRK.B"


# ---- Premium accounting edge cases -------------------------------------------


def test_kpi_net_premium_matches_timeseries_total(seed_options):
    """Regression for the bug where summary used proceeds (raw) and the
    chart used net_cash. Both must agree to within FP error so the same
    label "Prima neta" shows the same value on both."""
    with SessionLocal() as s:
        kpi = options_trades_list(s)
        series = options_premium_timeseries(s)
    assert kpi["summary"]["net_premium_base"] == pytest.approx(series["summary"]["total_net"])


def test_premium_timeseries_empty_returns_empty_series():
    with SessionLocal() as s:
        out = options_premium_timeseries(s)
    assert out["series"] == []
    assert out["summary"]["total_net"] == 0


def test_premium_timeseries_respects_fx_rate(seed_options):
    """A trade with fx_rate=2.0 should contribute net_cash*2 to base."""
    with SessionLocal() as s:
        s.add_all([
            Instrument(conid=9001, symbol="EUR   260619C00100000", description="EUR 19JUN26 100 C", asset_class="OPT", multiplier=Decimal("100")),
        ])
        s.add(Trade(
            trade_id="T-FX", ib_order_id="ORD-FX", account_id="U99", conid=9001,
            symbol="EUR   260619C00100000", asset_class="OPT",
            currency="EUR", multiplier=Decimal("100"),
            trade_date=date(2026, 4, 1), trade_datetime=datetime(2026, 4, 1, 10, 0),
            buy_sell="SELL", open_close="O", quantity=Decimal("-1"),
            trade_price=Decimal("1"), proceeds=Decimal("100"),
            ib_commission=Decimal("0"), net_cash=Decimal("100"),
            fx_rate_to_base=Decimal("2"),
        ))
        s.commit()
        out = options_premium_timeseries(s)
    apr = [r for r in out["series"] if r["bucket"] == "2026-04"]
    assert apr and apr[0]["net_premium_base"] == pytest.approx(200.0)


def test_premium_timeseries_yearly_bucket(seed_options):
    with SessionLocal() as s:
        out = options_premium_timeseries(s, bucket="year")
    assert out["bucket"] == "year"
    assert all(len(r["bucket"]) == 4 for r in out["series"])


def test_premium_cumulative_is_monotone_with_signed_data(seed_options):
    """Cumulative is a running sum — can go down if a bucket is net negative."""
    with SessionLocal() as s:
        # Inject a big negative-net month
        s.add(Trade(
            trade_id="T-LOSS", ib_order_id="ORD-LOSS", account_id="U99", conid=1001,
            symbol="ACME  260116C00033000", asset_class="OPT",
            currency="USD", multiplier=Decimal("100"),
            trade_date=date(2026, 6, 1), trade_datetime=datetime(2026, 6, 1, 10, 0),
            buy_sell="BUY", open_close="O", quantity=Decimal("1"),
            trade_price=Decimal("10"), proceeds=Decimal("-1000"),
            ib_commission=Decimal("0"), net_cash=Decimal("-1000"),
            fx_rate_to_base=Decimal("1"),
        ))
        s.commit()
        out = options_premium_timeseries(s)
    jun = next(r for r in out["series"] if r["bucket"] == "2026-06")
    assert jun["net_premium_base"] == pytest.approx(-1000)
    assert jun["premium_out_base"] == pytest.approx(-1000)
    # Cumulative must drop in June vs May
    may_or_before = [r for r in out["series"] if r["bucket"] < "2026-06"]
    if may_or_before:
        assert jun["cumulative_net_base"] < may_or_before[-1]["cumulative_net_base"]


# ---- Assignment-risk edge cases ----------------------------------------------


def test_assignment_risk_no_snapshot_returns_empty():
    with SessionLocal() as s:
        out = options_assignment_risk(s)
    assert out["snapshot_date"] is None
    assert out["rows"] == []
    assert out["summary"]["shorts"] == 0


def test_assignment_risk_uses_mtm_fallback_when_stock_not_held():
    """User is short an option but doesn't own the stock — assignment risk
    must still surface the underlying close via MtmPerformanceUnderlying."""
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.add_all([
            Instrument(conid=5001, symbol="TSLA  260619P00200000", description="TSLA 19JUN26 200 P", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=5002, symbol="TSLA", description="Tesla", asset_class="STK"),
        ])
        # Short put position, but no STK PositionEod row.
        s.add(PositionEod(
            snapshot_date=date(2026, 5, 15), account_id="U99", conid=5001,
            symbol="TSLA  260619P00200000", asset_class="OPT",
            quantity=Decimal("-1"), multiplier=Decimal("100"),
            mark_price=Decimal("2"), position_value=Decimal("-200"),
            currency="USD", fx_rate_to_base=Decimal("1"),
        ))
        # MTM table carries the underlying close even with no holding
        s.add(MtmPerformanceUnderlying(
            snapshot_date=date(2026, 5, 15), account_id="U99", conid=5002,
            symbol="TSLA", asset_class="STK",
            close_price=Decimal("180"), close_quantity=Decimal("0"),
        ))
        s.commit()
        out = options_assignment_risk(s)
    assert len(out["rows"]) == 1
    r = out["rows"][0]
    assert r["last_underlying_close"] == 180.0
    # Strike 200 put vs close 180 → ITM by 20
    assert r["itm"] is True
    assert r["intrinsic_per_contract"] == pytest.approx(20.0)


def test_assignment_risk_handles_missing_underlying_close():
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.add_all([
            Instrument(conid=6001, symbol="UNKWN 260619C00050000", description="UNKWN 19JUN26 50 C", asset_class="OPT", multiplier=Decimal("100")),
        ])
        s.add(PositionEod(
            snapshot_date=date(2026, 5, 15), account_id="U99", conid=6001,
            symbol="UNKWN 260619C00050000", asset_class="OPT",
            quantity=Decimal("-1"), multiplier=Decimal("100"),
            mark_price=Decimal("1"), position_value=Decimal("-100"),
            currency="USD", fx_rate_to_base=Decimal("1"),
        ))
        s.commit()
        out = options_assignment_risk(s)
    r = out["rows"][0]
    assert r["last_underlying_close"] is None
    assert r["itm"] is None
    assert r["intrinsic_per_contract"] is None


def test_assignment_risk_negative_dte_for_expired_position():
    with SessionLocal() as s:
        s.add(Account(account_id="U99", base_currency="USD"))
        s.add_all([
            Instrument(conid=7001, symbol="OLD   200101C00010000", description="OLD 01JAN20 10 C", asset_class="OPT", multiplier=Decimal("100")),
        ])
        s.add(PositionEod(
            snapshot_date=date(2026, 5, 15), account_id="U99", conid=7001,
            symbol="OLD   200101C00010000", asset_class="OPT",
            quantity=Decimal("-1"), multiplier=Decimal("100"),
            mark_price=Decimal("0"), position_value=Decimal("0"),
            currency="USD", fx_rate_to_base=Decimal("1"),
        ))
        s.commit()
        out = options_assignment_risk(s)
    assert out["rows"][0]["dte"] < 0  # already expired in DB; should not crash


# ---- Multi-account isolation -------------------------------------------------


def test_trades_list_filters_by_account(seed_options):
    with SessionLocal() as s:
        s.add(Account(account_id="U22", base_currency="USD"))
        s.add(Instrument(conid=8001, symbol="MSFT  260619C00400000", description="MSFT 19JUN26 400 C", asset_class="OPT", multiplier=Decimal("100")))
        s.add(Trade(
            trade_id="T-OTHER", ib_order_id="ORD-OTHER", account_id="U22", conid=8001,
            symbol="MSFT  260619C00400000", asset_class="OPT",
            currency="USD", multiplier=Decimal("100"),
            trade_date=date(2026, 3, 1), trade_datetime=datetime(2026, 3, 1, 10, 0),
            buy_sell="SELL", open_close="C", quantity=Decimal("-1"),
            trade_price=Decimal("5"), proceeds=Decimal("500"),
            ib_commission=Decimal("-1"), net_cash=Decimal("499"),
            fifo_pnl_realized=Decimal("499"), fx_rate_to_base=Decimal("1"),
        ))
        s.commit()
        u99 = options_trades_list(s, account_id="U99")
        u22 = options_trades_list(s, account_id="U22")
    assert u99["summary"]["total"] == 3 and not any(r["underlying"] == "MSFT" for r in u99["rows"])
    assert u22["summary"]["total"] == 1 and u22["rows"][0]["underlying"] == "MSFT"


# ---- Period filter edge cases ------------------------------------------------


def test_trades_list_period_excluding_all_returns_zero(seed_options):
    with SessionLocal() as s:
        out = options_trades_list(
            s, period=resolve_period(from_date=date(2030, 1, 1), to_date=date(2030, 12, 31))
        )
    assert out["summary"]["total"] == 0
    assert out["rows"] == []
    assert out["summary"]["win_rate"] is None


def test_pnl_by_underlying_period_filter(seed_options):
    with SessionLocal() as s:
        out = options_pnl_by_underlying(
            s, period=resolve_period(from_date=date(2026, 1, 1), to_date=date(2026, 1, 31))
        )
    # Only ACME Jan trade in this window
    assert {r["underlying"] for r in out["rows"]} == {"ACME"}


# ---- Pagination --------------------------------------------------------------


def test_trades_list_offset_beyond_total_returns_empty_but_keeps_summary(seed_options):
    with SessionLocal() as s:
        out = options_trades_list(s, offset=1000)
    assert out["rows"] == []
    assert out["summary"]["total"] == 3  # summary still totals across all


# ---- EAE filtering -----------------------------------------------------------


def test_eae_list_filters_by_underlying(seed_options):
    with SessionLocal() as s:
        empty = options_eae_list(s, underlying="DOESNOTEXIST")
        acme = options_eae_list(s, underlying="ACME")
    assert empty["summary"]["total"] == 0
    assert acme["summary"]["total"] == 2  # Exercise + Buy both for ACME


# ------------------------------------------------- side split / wheel shares
def _opt_trade(tid, conid, symbol, buy_sell, pnl, *, ttype="ExchTrade", notes=None, proceeds="100"):
    return Trade(
        trade_id=tid, ib_order_id=f"O-{tid}", account_id="U77", conid=conid,
        symbol=symbol, asset_class="OPT", currency="USD", multiplier=Decimal("100"),
        trade_date=date(2026, 3, 1), trade_datetime=datetime(2026, 3, 1, 14, 0),
        buy_sell=buy_sell, open_close="C", quantity=Decimal("1" if buy_sell == "BUY" else "-1"),
        trade_price=Decimal("1"), proceeds=Decimal(proceeds if buy_sell == "SELL" else "-" + proceeds),
        ib_commission=Decimal("-1"), net_cash=Decimal("0"),
        fifo_pnl_realized=Decimal(pnl), fx_rate_to_base=Decimal("1"),
        transaction_type=ttype, notes=notes,
    )


def test_attribution_splits_premium_by_opening_side_and_limits_wheel_stock():
    with SessionLocal() as s:
        s.add(Account(account_id="U77", base_currency="USD"))
        s.flush()
        s.add_all([
            Instrument(conid=1, symbol="AAA   260918P00040000", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=2, symbol="AAA   260918C00050000", asset_class="OPT", multiplier=Decimal("100")),
            Instrument(conid=3, symbol="AAA", asset_class="STK"),
        ])
        s.flush()
        s.add(_opt_trade("short", 1, "AAA   260918P00040000", "BUY", "100"))   # BUY close = was short
        s.add(_opt_trade("long", 2, "AAA   260918C00050000", "SELL", "900"))   # SELL close = was long
        s.add(OptionExerciseAssignment(
            eae_id="E1", account_id="U77", conid=3, symbol="AAA", underlying_symbol="AAA",
            transaction_type="Buy", event_date=date(2026, 2, 1), quantity=Decimal("100"),
        ))
        # 300 shares sold for +300 in total: only the 100 received by assignment are wheel.
        s.add(Trade(
            trade_id="stk", ib_order_id="S", account_id="U77", conid=3, symbol="AAA",
            asset_class="STK", currency="USD", trade_date=date(2026, 4, 1),
            trade_datetime=datetime(2026, 4, 1, 14, 0), buy_sell="SELL", open_close="C",
            quantity=Decimal("-300"), trade_price=Decimal("1"), proceeds=Decimal("300"),
            net_cash=Decimal("300"), fifo_pnl_realized=Decimal("300"), fx_rate_to_base=Decimal("1"),
        ))
        s.commit()
        t = options_pnl_attribution(s)["totals"]
    assert t["short_premium_base"] == pytest.approx(100.0)
    assert t["long_premium_base"] == pytest.approx(900.0)
    assert t["wheel_stock_base"] == pytest.approx(100.0)        # 100 of 300 shares
    assert t["non_options_stock_base"] == pytest.approx(200.0)  # the remaining shares
    assert t["grand_total_base"] == pytest.approx(100 + 900 + 100 + 200)


def test_assignment_rate_ignores_booktrade_close_rows():
    with SessionLocal() as s:
        s.add(Account(account_id="U77", base_currency="USD"))
        s.flush()
        s.add(Instrument(conid=1, symbol="BBB   260918C00050000", asset_class="OPT", multiplier=Decimal("100")))
        s.flush()
        s.add(_opt_trade("real", 1, "BBB   260918C00050000", "SELL", "50"))
        s.add(_opt_trade("book", 1, "BBB   260918C00050000", "SELL", "0", ttype="BookTrade", notes="Ex"))
        s.commit()
        out = options_assignment_rate(s)
    assert out["totals"]["sold_to_close"] == 1
