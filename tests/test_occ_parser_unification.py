"""Pin the behavior of the unified OCC parser at its metrics.py call-sites.

`metrics._parse_option_symbol`
duplicated `options_parsing.parse_occ_symbol` with a *different* regex.
metrics now delegates to `options_parsing.enrich_option`. These tests fix the
edge cases where the two implementations diverged, keeping the behavior of
the more correct one (options_parsing):

  * human-form symbols ("XYZ 18SEP26 40 C") now classify — old regex bailed;
  * OSI roots with embedded spaces ("BRK B") now parse — old `\\S+` bailed;
  * structurally-OSI symbols with an impossible calendar date (month 13)
    now bail to the generic label — old regex accepted the raw string;
  * `Instrument.description` human-form fallback works when the symbol
    alone is unparseable (same 3-tier chain the Opciones tab uses);
  * `_strategy_hint` no longer mislabels human-form SEP calls as puts
    (the old `"P" in sym[-9:]` heuristic matched the "P" in "SEP").
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from ibkr_backend.analytics.metrics import _classify_multi_leg, _strategy_hint
from ibkr_backend.db.models import Instrument, Trade


def _mk_trade(symbol: str | None, side: str, qty: int = 1,
              asset_class: str = "OPT", trade_id: str = "T1") -> Trade:
    return Trade(
        trade_id=trade_id,
        ib_order_id="ORD",
        account_id="U99",
        conid=hash((symbol, trade_id)) & 0xFFFFFFFF,
        symbol=symbol,
        asset_class=asset_class,
        currency="USD",
        trade_date=date(2026, 5, 1),
        trade_datetime=datetime(2026, 5, 1, 10, 0, 0),
        quantity=qty,
        trade_price=1,
        proceeds=qty * 100,
        net_cash=qty * 100,
        buy_sell=side,
    )


def _mk_instr(description: str | None) -> Instrument:
    return Instrument(conid=1, symbol="XYZ", description=description,
                      asset_class="OPT")


# ---------------------------------------------------------------------------
# _classify_multi_leg
# ---------------------------------------------------------------------------

def test_classify_multi_leg_parses_human_form_symbols():
    """IBKR Flex sometimes emits the human form in Trade.symbol; the old
    metrics-local regex returned None → generic 'Multi-leg (N)'. The unified
    parser classifies these."""
    group = [
        (_mk_trade("XYZ 18SEP26 40 C", "BUY", 1, trade_id="T1"), None),
        (_mk_trade("XYZ 18SEP26 45 C", "SELL", 1, trade_id="T2"), None),
    ]
    assert _classify_multi_leg(group) == "Vertical spread"


def test_classify_multi_leg_parses_root_with_embedded_space():
    """OSI roots may contain a space (class shares, e.g. 'BRK B'). The old
    `\\S+` root regex could not parse them."""
    group = [
        (_mk_trade("BRK B", "BUY", 100, asset_class="STK", trade_id="T1"), None),
        (_mk_trade("BRK B 260918C00400000", "SELL", 1, trade_id="T2"), None),
    ]
    assert _classify_multi_leg(group) == "Covered call"


def test_classify_multi_leg_rejects_impossible_calendar_date():
    """Month 13 is not a date. The old regex accepted the raw yymmdd string
    and classified anyway; the unified parser yields expiry=None → bail to
    the generic label (None)."""
    group = [
        (_mk_trade("XYZ   261332C00040000", "BUY", 1, trade_id="T1"), None),
        (_mk_trade("XYZ   261332C00045000", "SELL", 1, trade_id="T2"), None),
    ]
    assert _classify_multi_leg(group) is None


def test_classify_multi_leg_falls_back_to_instrument_description():
    """When the symbol alone is unparseable, the human-form description on
    the joined Instrument resolves the contract — same 3-tier chain as the
    Opciones tab, so both tabs label the same trade identically."""
    group = [
        (_mk_trade("XYZ", "BUY", 1, trade_id="T1"),
         _mk_instr("XYZ 18SEP26 40 C")),
        (_mk_trade("XYZ", "SELL", 1, trade_id="T2"),
         _mk_instr("XYZ 18SEP26 45 C")),
    ]
    assert _classify_multi_leg(group) == "Vertical spread"


def test_classify_multi_leg_uses_persisted_instrument_option_fields():
    """Once the loader persists OCC fields on Instrument, strategy labelling
    should not need to re-parse a lossy/non-OSI trade symbol."""
    instr_a = Instrument(
        conid=1,
        symbol="BROKEN-A",
        asset_class="OPT",
        option_underlying="XYZ",
        option_expiry=date(2026, 9, 18),
        option_right="C",
        option_strike=Decimal("40"),
    )
    instr_b = Instrument(
        conid=2,
        symbol="BROKEN-B",
        asset_class="OPT",
        option_underlying="XYZ",
        option_expiry=date(2026, 9, 18),
        option_right="C",
        option_strike=Decimal("45"),
    )
    group = [
        (_mk_trade("BROKEN-A", "BUY", 1, trade_id="T1"), instr_a),
        (_mk_trade("BROKEN-B", "SELL", 1, trade_id="T2"), instr_b),
    ]
    assert _classify_multi_leg(group) == "Vertical spread"


def test_classify_multi_leg_osi_still_classifies():
    """Plain OSI symbols (the common case) keep working unchanged."""
    group = [
        (_mk_trade("XYZ   260918C00040000", "BUY", 1, trade_id="T1"), None),
        (_mk_trade("XYZ   260918P00040000", "BUY", 1, trade_id="T2"), None),
    ]
    assert _classify_multi_leg(group) == "Straddle"


# ---------------------------------------------------------------------------
# _strategy_hint
# ---------------------------------------------------------------------------

def test_strategy_hint_human_form_september_call_is_a_call():
    """Old heuristic: '"P" in sym[-9:]' — the trailing 9 chars of
    'XYZ 19SEP25 100 C' are 'P25 100 C', so a SEPtember *call* was labelled
    a put. The unified parser reads the right correctly."""
    t = _mk_trade("XYZ 19SEP25 100 C", "BUY", 1)
    assert _strategy_hint(t, None) == "Long call"


def test_strategy_hint_osi_put_and_call():
    assert _strategy_hint(
        _mk_trade("XYZ   260918P00040000", "SELL", -1), None) == "Short put"
    assert _strategy_hint(
        _mk_trade("XYZ   260918C00040000", "BUY", 1), None) == "Long call"


def test_strategy_hint_unparseable_symbol_stays_generic_option():
    assert _strategy_hint(_mk_trade("XYZ", "BUY", 1), None) == "Long option"
