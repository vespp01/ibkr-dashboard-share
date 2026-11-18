"""OptionsLab management signals joined onto /options/open-positions rows.

The signals file is synthetic and written to tmp_path; no host file is read.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from ibkr_backend import config as cfg
from ibkr_backend.api.main import create_app
from ibkr_backend.db.models import Instrument, PositionEod
from ibkr_backend.db.session import SessionLocal
from ibkr_backend.integrations import optionslab
from ibkr_backend.integrations.optionslab import SCHEMA_VERSION, attach_management_signals


GENERATED = datetime(2026, 8, 2, 6, 0, tzinfo=UTC)


class _WarningList(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture(autouse=True)
def log_warnings():
    # A direct handler: create_app() stops the ibkr_backend logger propagating to root.
    optionslab._last_logged_reason = None
    handler = _WarningList()
    optionslab.log.addHandler(handler)
    yield handler.messages
    optionslab.log.removeHandler(handler)
    optionslab._last_logged_reason = None


def _signals(**overrides) -> dict:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "producer": "optionslab/scripts/monitor.py",
        "generated_at": "2026-08-02T06:00:00Z",
        "status": "ready",
        "rules": {"gtc_capture_pct": 70.0, "stop_delta": -0.30, "stop_premium_factor": 2.0},
        "positions": [
            {
                "underlying": "SPY", "strategy": "BPS", "right": "P",
                "expiry": "2026-09-18", "short_strike": 500.0, "long_strike": 490.0,
                "contracts": 1, "dte": 47, "action": "take_profit",
                "status_text": "GTC HIT", "capture_pct": 72.5, "short_delta": -0.12,
                "premium_ratio": 0.27, "data_source": "yfinance",
            },
            {
                "underlying": "ACME", "strategy": "CC", "right": "C",
                "expiry": "2026-09-18", "short_strike": 30.0, "long_strike": None,
                "contracts": 2, "dte": 47, "action": "hold", "status_text": "OK",
                "capture_pct": None, "short_delta": None, "premium_ratio": None,
                "data_source": "yfinance",
            },
        ],
        "warnings": [],
    }
    payload.update(overrides)
    return payload


def _write(tmp_path, payload) -> object:
    path = tmp_path / "management_signals.v1.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def _rows() -> list[dict]:
    return [
        {"underlying": "SPY", "right": "P", "expiry": "2026-09-18", "strike": 500.0, "side": "short"},
        {"underlying": "SPY", "right": "P", "expiry": "2026-09-18", "strike": 490.0000001, "side": "long"},
        {"underlying": "SPY", "right": "P", "expiry": "2026-10-16", "strike": 500.0, "side": "short"},
        {"underlying": "SPY", "right": "C", "expiry": "2026-09-18", "strike": 500.0, "side": "short"},
        {"underlying": "QQQ", "right": "P", "expiry": "2026-09-18", "strike": 500.0, "side": "short"},
    ]


def test_side_must_match_the_signal_leg(tmp_path):
    # After a roll a short 490 put must not inherit the old spread's long-leg signal.
    rows = [
        {"underlying": "SPY", "right": "P", "expiry": "2026-09-18", "strike": 490.0, "side": "short"},
        {"underlying": "SPY", "right": "P", "expiry": "2026-09-18", "strike": 500.0, "side": "long"},
    ]

    attach_management_signals(rows, _write(tmp_path, _signals()), now=GENERATED + timedelta(hours=1))

    assert [row["optionslab"] for row in rows] == [None, None]


def test_one_invalid_position_is_skipped_not_the_whole_file(tmp_path, log_warnings):
    payload = _signals()
    payload["positions"].append({**payload["positions"][0], "underlying": "QQQ", "action": "roll"})
    no_right = {k: v for k, v in payload["positions"][1].items() if k != "right"}
    payload["positions"][1] = no_right  # right is optional: valid, but never matches
    rows = _rows()

    attach_management_signals(rows, _write(tmp_path, payload), now=GENERATED + timedelta(hours=1))

    assert rows[0]["optionslab"]["action"] == "take_profit"
    assert rows[4]["optionslab"] is None  # the invalid QQQ "roll" signal was skipped
    assert any("1 invalid position" in message for message in log_warnings)


def test_matches_short_and_long_legs(tmp_path):
    rows = _rows()

    attach_management_signals(rows, _write(tmp_path, _signals()), now=GENERATED + timedelta(hours=1))

    assert rows[0]["optionslab"] == {
        "action": "take_profit",
        "status_text": "GTC HIT",
        "capture_pct": 72.5,
        "short_delta": -0.12,
        "dte": 47,
        "leg": "short",
        "generated_at": "2026-08-02T06:00:00Z",
        "stale": False,
    }
    assert rows[1]["optionslab"]["leg"] == "long"  # within the 1e-6 strike tolerance
    assert rows[1]["optionslab"]["action"] == "take_profit"


def test_rows_without_a_matching_signal_get_null(tmp_path):
    rows = _rows()

    attach_management_signals(rows, _write(tmp_path, _signals()), now=GENERATED)

    # Wrong expiry, wrong right, wrong underlying.
    assert [row["optionslab"] for row in rows[2:]] == [None, None, None]


def test_missing_file_nulls_every_row_and_warns_once(tmp_path, log_warnings):
    rows = _rows()
    missing = tmp_path / "absent.json"

    attach_management_signals(rows, missing)
    attach_management_signals(_rows(), missing)

    assert all(row["optionslab"] is None for row in rows)
    assert len(log_warnings) == 1
    assert "file_not_found" in log_warnings[0]


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (_signals(schema_version="optionslab.management_signals.v2"), "wrong_schema_version"),
        (_signals(status="blocked"), "status_not_ready"),
        ("{not json", "invalid_json"),
        (_signals(generated_at=None), "contract_validation_failed"),
    ],
)
def test_unusable_file_nulls_every_row(tmp_path, log_warnings, payload, reason):
    rows = _rows()

    attach_management_signals(rows, _write(tmp_path, payload), now=GENERATED)

    assert all(row["optionslab"] is None for row in rows)
    assert len(log_warnings) == 1
    assert reason in log_warnings[0]


def test_signals_older_than_24h_are_stale(tmp_path):
    rows = _rows()

    attach_management_signals(rows, _write(tmp_path, _signals()), now=GENERATED + timedelta(hours=25))

    assert rows[0]["optionslab"]["stale"] is True
    assert rows[0]["optionslab"]["action"] == "take_profit"


def test_open_positions_endpoint_carries_optionslab_field(tmp_path, monkeypatch):
    expiry = date.today() + timedelta(days=30)
    symbol = f"{'SPY':<6}{expiry.strftime('%y%m%d')}P{500 * 1000:08d}"
    with SessionLocal() as s:
        s.add(Instrument(conid=7001, symbol=symbol, asset_class="OPT", multiplier=Decimal("100")))
        s.add(
            PositionEod(
                snapshot_date=date.today(), account_id="PRIVATE_ACCOUNT_A", conid=7001,
                symbol=symbol, asset_class="OPT", quantity=Decimal("-1"),
                multiplier=Decimal("100"), mark_price=Decimal("1"),
                position_value=Decimal("-100"), currency="USD", fx_rate_to_base=Decimal("1"),
            )
        )
        s.commit()
    now_z = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    position = {**_signals()["positions"][0], "expiry": expiry.isoformat()}
    path = _write(tmp_path, _signals(generated_at=now_z, positions=[position]))
    client = TestClient(create_app())

    monkeypatch.setenv("OPTIONSLAB_SIGNALS_PATH", str(path))
    cfg.get_settings.cache_clear()
    (row,) = client.get("/options/open-positions").json()["rows"]
    assert row["optionslab"]["action"] == "take_profit"
    assert row["optionslab"]["leg"] == "short"
    assert row["optionslab"]["stale"] is False

    monkeypatch.setenv("OPTIONSLAB_SIGNALS_PATH", str(tmp_path / "absent.json"))
    cfg.get_settings.cache_clear()
    response = client.get("/options/open-positions")
    assert response.status_code == 200
    assert response.json()["rows"][0]["optionslab"] is None
