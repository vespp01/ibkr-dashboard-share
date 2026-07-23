"""Price alerts — rule parsing, crossing logic, and fire/re-arm state.
Real SQLite per test (no DB mocking); quotes and Telegram send are injected."""

from __future__ import annotations

from ibkr_backend.config import Settings
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import PriceAlertState
from ibkr_backend.notify import price_alerts
from ibkr_backend.notify.price_alerts import (
    AlertRule,
    check_price_alerts,
    crossed,
    load_rules,
)

_YAML = """
alerts:
  - symbol: aapl
    op: ">"
    price: 250
    currency: USD
  - symbol: ACME
    op: "<"
    price: 30
  - symbol: BAD
    op: "="          # invalid op -> skipped
    price: 1
"""


def _settings(tmp_path):
    p = tmp_path / "price_alerts.yaml"
    p.write_text(_YAML, encoding="utf-8")
    return Settings(price_alerts_file=str(p))


# ----------------------------------------------------------------- parsing
def test_load_rules_normalises_and_skips_invalid(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text(_YAML, encoding="utf-8")
    rules = load_rules(p)
    assert [r.symbol for r in rules] == ["AAPL", "ACME"]   # uppercased, BAD skipped
    assert rules[0].op == ">" and rules[0].price == 250.0


def test_crossed_logic():
    above = AlertRule(symbol="X", op=">", price=100)
    below = AlertRule(symbol="Y", op="<", price=100)
    assert crossed(above, 101) and not crossed(above, 100)
    assert crossed(below, 99) and not crossed(below, 100)
    assert not crossed(above, None)


# --------------------------------------------------------- fire / re-arm
def test_fires_once_then_rearms(tmp_path, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(price_alerts.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        price_alerts.telegram, "send_message", lambda text, **k: sent.append(text) or True
    )
    s = _settings(tmp_path)

    # 1) AAPL above 250 -> fires once
    with session_scope() as db:
        r = check_price_alerts(db, quotes_fn=lambda rules: {"AAPL": 252.0, "ACME": 35.0}, settings=s)
    assert r["fired"] == ["AAPL|>|250"]
    assert len(sent) == 1 and "AAPL" in sent[0] and "superado" in sent[0]

    # 2) still above -> no re-fire
    with session_scope() as db:
        check_price_alerts(db, quotes_fn=lambda rules: {"AAPL": 255.0, "ACME": 35.0}, settings=s)
    assert len(sent) == 1

    # 3) drops below -> re-arms (no message)
    with session_scope() as db:
        check_price_alerts(db, quotes_fn=lambda rules: {"AAPL": 240.0, "ACME": 35.0}, settings=s)
    with session_scope() as db:
        st = db.get(PriceAlertState, "AAPL|>|250")
        assert st.triggered is False

    # 4) crosses again -> fires again
    with session_scope() as db:
        check_price_alerts(db, quotes_fn=lambda rules: {"AAPL": 260.0, "ACME": 35.0}, settings=s)
    assert len(sent) == 2


def test_acme_below_threshold_fires(tmp_path, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(price_alerts.telegram, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(
        price_alerts.telegram, "send_message", lambda text, **k: sent.append(text) or True
    )
    s = _settings(tmp_path)
    with session_scope() as db:
        r = check_price_alerts(db, quotes_fn=lambda rules: {"AAPL": 100.0, "ACME": 25.0}, settings=s)
    assert r["fired"] == ["ACME|<|30"]
    assert "caído por debajo" in sent[0]


def test_no_rules_is_noop(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("alerts: []\n", encoding="utf-8")
    s = Settings(price_alerts_file=str(p))
    with session_scope() as db:
        r = check_price_alerts(db, quotes_fn=lambda rules: {}, settings=s)
    assert r == {"rules": 0, "fired": []}
