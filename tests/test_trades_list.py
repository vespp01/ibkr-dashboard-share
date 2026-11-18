"""trades_list: labels from open_close, multi-leg needs 2 conids, closed stats per
order, server-side strategy filter, dividends on closing rows only (B26-B29, B68)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from ibkr_backend.analytics.trades import trades_list
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import Account, CashTransaction, Instrument, Trade


def _t(tid, conid, symbol, ac, side, oc, qty, pnl, day, order, *, tx="ExchTrade"):
    return Trade(
        trade_id=tid, ib_order_id=order, account_id="U1", conid=conid, symbol=symbol,
        asset_class=ac, currency="USD", fx_rate_to_base=Decimal(1), trade_date=day,
        trade_datetime=datetime(day.year, day.month, day.day, 10, int(tid[-1])),
        buy_sell=side, open_close=oc, quantity=Decimal(qty), trade_price=Decimal(1),
        proceeds=Decimal(0), net_cash=Decimal(0), fifo_pnl_realized=Decimal(pnl),
        multiplier=Decimal(100 if ac == "OPT" else 1), transaction_type=tx,
    )


@pytest.fixture()
def seeded():
    with session_scope() as s:
        s.add(Account(account_id="U1", base_currency="USD"))
        s.add_all([
            Instrument(conid=1, symbol="AAA", asset_class="STK"),
            Instrument(conid=2, symbol="AAA   261218C00010000", asset_class="OPT", multiplier=Decimal(100)),
            Instrument(conid=3, symbol="AAA   261218C00020000", asset_class="OPT", multiplier=Decimal(100)),
            Instrument(conid=4, symbol="BBB   261218P00010000", asset_class="OPT", multiplier=Decimal(100)),
        ])
        s.flush()
        d = date
        s.add_all([
            # AAA stock: buy in 2 fills of one order, sold in 2 fills of one order
            # (+60 and -10 = +50 for the order, a single win).
            _t("a1", 1, "AAA", "STK", "BUY", "O", "50", 0, d(2026, 1, 5), "B1"),
            _t("a2", 1, "AAA", "STK", "BUY", "O", "50", 0, d(2026, 1, 5), "B1"),
            _t("a3", 1, "AAA", "STK", "SELL", "C", "-50", 60, d(2026, 3, 5), "S1"),
            _t("a4", 1, "AAA", "STK", "SELL", "C", "-50", -10, d(2026, 3, 5), "S1"),
            # Vertical spread: two conids, one order.
            _t("v1", 2, "AAA   261218C00010000", "OPT", "BUY", "O", "1", 0, d(2026, 2, 1), "V1"),
            _t("v2", 3, "AAA   261218C00020000", "OPT", "SELL", "O", "-1", 0, d(2026, 2, 1), "V1"),
            # One short put opened in two fills of ONE contract: not multi-leg.
            _t("p1", 4, "BBB   261218P00010000", "OPT", "SELL", "O", "-1", 0, d(2026, 2, 2), "P1"),
            _t("p2", 4, "BBB   261218P00010000", "OPT", "SELL", "O", "-1", 0, d(2026, 2, 2), "P1"),
            # Short put closed by a BUY: still a short put; a SELL close is a long.
            _t("p3", 4, "BBB   261218P00010000", "OPT", "BUY", "C", "2", 40, d(2026, 2, 20), "P2"),
            _t("l1", 2, "AAA   261218C00010000", "OPT", "SELL", "C", "-1", 0, d(2026, 2, 21), "L1"),
        ])
        # Dividends: one inside the holding window.
        s.add(CashTransaction(
            transaction_id="d1", account_id="U1", conid=1, symbol="AAA", type="Dividends",
            amount=Decimal(7), currency="USD", fx_rate_to_base=Decimal(1),
            event_datetime=datetime(2026, 2, 10), settle_date=date(2026, 2, 10),
        ))


def test_labels_multi_leg_and_open_close(seeded):
    with session_scope() as s:
        out = trades_list(s, limit=100)
    by = {r["trade_id"]: r for r in out["rows"]}
    assert by["v1"]["strategy"] == "Vertical spread" and by["v1"]["strategy_legs"] == 2
    assert not by["p1"]["is_multi_leg"] and by["p1"]["strategy"] == "Short put"
    assert by["p3"]["strategy"] == "Short put"        # BUY to close ends a short
    assert by["l1"]["strategy"] == "Long call"        # SELL to close ends a long
    assert by["a3"]["strategy"] == "Stock close"      # a sale is not a short
    assert not any(r["strategy"].startswith("Multi-leg") for r in out["rows"])


def test_stats_over_closed_orders_only_and_equal_for_every_status(seeded):
    with session_scope() as s:
        summaries = {st: trades_list(s, status=st, limit=100)["summary"] for st in ("all", "closed", "open")}
    sm = summaries["all"]
    # Closed orders: S1 (+50, a win), P2 (+40, a win), L1 (0, BE).
    assert (sm["win"], sm["loss"], sm["be"]) == (2, 0, 1)
    assert sm["closed_count"] == 3
    assert sm["open_count"] == 6
    for st in ("closed", "open"):
        assert {k: summaries[st][k] for k in ("win", "loss", "be")} == {"win": 2, "loss": 0, "be": 1}


def test_strategy_filter_applies_before_paging(seeded):
    with session_scope() as s:
        page1 = trades_list(s, strategy="Stock long", limit=1, offset=0)
        page2 = trades_list(s, strategy="Stock long", limit=1, offset=1)
        every = trades_list(s, limit=100)
    assert page1["summary"]["total"] == 2
    assert [r["strategy"] for r in page1["rows"] + page2["rows"]] == ["Stock long", "Stock long"]
    assert "Vertical spread" in every["strategies"] and "Stock long" in page1["strategies"]


def test_dividends_only_on_one_closing_row(seeded):
    with session_scope() as s:
        out = trades_list(s, limit=100)
    by = {r["trade_id"]: r for r in out["rows"]}
    divs = {tid: r["dividends_during_hold_base"] for tid, r in by.items() if r["dividends_during_hold_base"]}
    assert divs == {"a3": 7.0}                       # not on opens, not repeated on a4
    assert by["a1"]["total_return_base"] is None     # opening row: no total return
