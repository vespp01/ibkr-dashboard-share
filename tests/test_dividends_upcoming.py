"""GET /dividends/upcoming — open Flex accruals with pay date >= data date."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from ibkr_backend.analytics import upcoming_dividends
from ibkr_backend.api.main import create_app
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import DividendAccrual, FxRate, PositionEod

AS_OF = date(2026, 10, 7)


def _snapshot(s, account="U1"):
    s.add(PositionEod(
        snapshot_date=AS_OF, account_id=account, conid=999, symbol="ZZZ",
        quantity=Decimal("1"), asset_class="STK",
    ))


def _acc(s, code, *, conid=1, symbol="AAA", ex=date(2026, 9, 18),
         pay=date(2026, 10, 9), ccy="USD", gross="10", net="8.5", account="U1"):
    s.add(DividendAccrual(
        account_id=account, conid=conid, symbol=symbol, ex_date=ex, pay_date=pay,
        quantity=Decimal("100"), gross_rate=Decimal("0.1"),
        gross_amount=Decimal(gross), net_amount=Decimal(net),
        tax=Decimal(net) - Decimal(gross),
        currency=ccy, code=code,
    ))


def _fx(s, d, ccy, rate, to="CHF"):
    s.add(FxRate(rate_date=d, from_ccy=ccy, to_ccy=to, rate=Decimal(rate)))


def _run(**kw):
    with session_scope() as s:
        return upcoming_dividends(s, base_currency="CHF", **kw)


def test_empty_db():
    r = _run()
    assert r["as_of"] is None
    assert r["events"] == [] and r["totals"]["count"] == 0


def test_open_listed_reversed_excluded():
    with session_scope() as s:
        _snapshot(s)
        _acc(s, "Po", conid=1, symbol="OPEN")
        _acc(s, "Po", conid=2, symbol="DONE")
        _acc(s, "Re", conid=2, symbol="DONE", gross="-10", net="-8.5")
        _fx(s, AS_OF, "USD", "0.8")
    r = _run()
    assert [e["symbol"] for e in r["events"]] == ["OPEN"]
    assert r["stale_open_events"] == 0
    assert r["as_of"] == "2026-10-07"


def test_adr_codes_count():
    with session_scope() as s:
        _snapshot(s)
        _acc(s, "ADR;Po", conid=1, symbol="ADR1")
        _acc(s, "ADR;Po", conid=2, symbol="ADR2")
        _acc(s, "ADR;Re", conid=2, symbol="ADR2", gross="-10", net="-8.5")
    assert [e["symbol"] for e in _run()["events"]] == ["ADR1"]


def test_pay_date_move_gawl_case():
    with session_scope() as s:
        _snapshot(s)
        _acc(s, "Po", conid=5, symbol="XYZl", pay=date(2026, 10, 5), ccy="GBP")
        _acc(s, "Re", conid=5, symbol="XYZl", pay=date(2026, 10, 5), ccy="GBP", gross="-10", net="-8.5")
        _acc(s, "Po", conid=5, symbol="XYZl", pay=date(2026, 10, 8), ccy="GBP")
    r = _run()
    assert [(e["symbol"], e["pay_date"]) for e in r["events"]] == [("XYZl", "2026-10-08")]
    assert r["stale_open_events"] == 0


def test_currency_reelection_csu_case_uses_latest_po():
    with session_scope() as s:
        _snapshot(s)
        _acc(s, "Po", conid=7, symbol="CCC", ccy="USD", gross="10", net="8")
        _acc(s, "Po", conid=7, symbol="CCC", ccy="CAD", gross="13", net="10")
        _acc(s, "Re", conid=7, symbol="CCC", ccy="USD", gross="-10", net="-8")
        _fx(s, AS_OF, "CAD", "0.6")
    r = _run()
    assert len(r["events"]) == 1
    e = r["events"][0]
    assert e["currency"] == "CAD"
    assert e["net_amount"] == 10.0
    assert abs(e["net_base"] - 6.0) < 1e-9


def test_orphan_in_past_counted_stale_not_listed():
    with session_scope() as s:
        _snapshot(s)
        _acc(s, "Po", conid=9, symbol="OLD", pay=date(2026, 6, 1))
        _acc(s, "Po", conid=1, symbol="NEW")
    r = _run()
    assert [e["symbol"] for e in r["events"]] == ["NEW"]
    assert r["stale_open_events"] == 1


def test_pay_date_equal_as_of_is_upcoming():
    with session_scope() as s:
        _snapshot(s)
        _acc(s, "Po", pay=AS_OF)
    assert len(_run()["events"]) == 1


def test_fx_conversion_uses_latest_rate_on_or_before_as_of():
    with session_scope() as s:
        _snapshot(s)
        _acc(s, "Po", gross="100", net="85")
        _fx(s, date(2026, 10, 1), "USD", "0.70")
        _fx(s, date(2026, 10, 6), "USD", "0.80")
        _fx(s, date(2026, 10, 9), "USD", "0.99")  # after as_of → ignored
    r = _run()
    e = r["events"][0]
    assert e["fx_rate_to_base"] == 0.8
    assert abs(e["gross_base"] - 80.0) < 1e-9
    assert abs(e["net_base"] - 68.0) < 1e-9
    assert abs(r["totals"]["net_base"] - 68.0) < 1e-9


def test_same_currency_rate_is_one_and_missing_fx_is_null():
    with session_scope() as s:
        _snapshot(s)
        _acc(s, "Po", conid=1, symbol="CHFCO", ccy="CHF", gross="5", net="4")
        _acc(s, "Po", conid=2, symbol="NOFX", ccy="ZAR", gross="50", net="40")
    r = _run()
    by = {e["symbol"]: e for e in r["events"]}
    assert by["CHFCO"]["fx_rate_to_base"] == 1.0 and by["CHFCO"]["net_base"] == 4.0
    assert by["NOFX"]["fx_rate_to_base"] is None
    assert by["NOFX"]["net_base"] is None and by["NOFX"]["net_amount"] == 40.0
    assert r["totals"]["count"] == 2 and r["totals"]["net_base"] == 4.0


def test_as_of_anchoring_uses_latest_snapshot_not_today():
    with session_scope() as s:
        _snapshot(s)
        _acc(s, "Po", conid=1, symbol="A", pay=date(2026, 10, 6))  # before as_of
        _acc(s, "Po", conid=2, symbol="B", pay=date(2026, 10, 7))
    r = _run()
    assert [e["symbol"] for e in r["events"]] == ["B"]
    assert r["stale_open_events"] == 1


def test_sorted_by_pay_date_then_symbol():
    with session_scope() as s:
        _snapshot(s)
        _acc(s, "Po", conid=1, symbol="ZED", pay=date(2026, 10, 9))
        _acc(s, "Po", conid=2, symbol="ABC", pay=date(2026, 10, 9))
        _acc(s, "Po", conid=3, symbol="MID", pay=date(2026, 10, 8))
    assert [e["symbol"] for e in _run()["events"]] == ["MID", "ABC", "ZED"]


def test_account_filter():
    with session_scope() as s:
        _snapshot(s, "U1")
        _acc(s, "Po", conid=1, symbol="MINE", account="U1")
        _acc(s, "Po", conid=2, symbol="OTHER", account="U2")
    assert [e["symbol"] for e in _run(account_id="U1")["events"]] == ["MINE"]


def test_endpoint_contract():
    with session_scope() as s:
        _snapshot(s)
        _acc(s, "Po", symbol="AAA", ccy="CHF")
    body = TestClient(create_app()).get("/dividends/upcoming").json()
    assert set(body) == {"as_of", "base_currency", "events", "totals", "stale_open_events"}
    assert set(body["events"][0]) == {
        "symbol", "conid", "ex_date", "pay_date", "quantity", "gross_rate",
        "currency", "gross_amount", "tax", "net_amount", "fx_rate_to_base",
        "gross_base", "net_base",
    }
    assert set(body["totals"]) == {"count", "gross_base", "net_base"}
