"""Contract tests for the read-only ``ibkr-dashboard.options.v1`` option legs.

All accounts, conids and amounts are synthetic.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from ibkr_backend.analytics.options import (
    OPTIONS_SCHEMA_VERSION,
    OptionsContractSnapshot,
    options_contract_snapshot,
)
from ibkr_backend.api.main import create_app
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import Account, Instrument, PositionEod


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "ibkr-dashboard.options.v1.schema.json"
NOW = datetime(2026, 8, 2, 8, 30, tzinfo=UTC)
EXPIRY = date(2026, 9, 18)


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def _occ(root: str, expiry: date, right: str, strike: int) -> str:
    return f"{root:<6}{expiry.strftime('%y%m%d')}{right}{strike * 1000:08d}"


def _add_option(
    session,
    account_id: str,
    snapshot_date: date,
    *,
    conid: int,
    quantity: str,
    cost_basis_money: str | None,
    root: str = "SPY",
    right: str = "P",
    strike: int = 500,
    cost_basis_price: str | None = None,
    mark_price: str | None = "1.10",
) -> None:
    symbol = _occ(root, EXPIRY, right, strike)
    if session.get(Instrument, conid) is None:
        session.add(
            Instrument(
                conid=conid,
                symbol=symbol,
                asset_class="OPT",
                currency="USD",
                multiplier=Decimal("100"),
            )
        )
        session.flush()
    session.add(
        PositionEod(
            snapshot_date=snapshot_date,
            account_id=account_id,
            conid=conid,
            symbol=symbol,
            quantity=Decimal(quantity),
            mark_price=Decimal(mark_price) if mark_price is not None else None,
            position_value=Decimal("-110") * Decimal(quantity).copy_abs(),
            cost_basis_money=Decimal(cost_basis_money) if cost_basis_money is not None else None,
            cost_basis_price=Decimal(cost_basis_price) if cost_basis_price is not None else None,
            fifo_pnl_unrealized=Decimal("12.34"),
            currency="USD",
            fx_rate_to_base=Decimal("0.9"),
            multiplier=Decimal("100"),
            asset_class="OPT",
            raw_xml="<OpenPosition accountId='PRIVATE_ACCOUNT_A' />",
        )
    )


def _seed_two_accounts(snapshot_date: date) -> None:
    with session_scope() as session:
        session.add(Account(account_id="PRIVATE_ACCOUNT_A", base_currency="CHF"))
        session.add(Account(account_id="PRIVATE_ACCOUNT_B", base_currency="CHF"))
        # Same short put in both accounts: 1 @ 1.50/share and 2 @ 1.65/share.
        _add_option(session, "PRIVATE_ACCOUNT_A", snapshot_date, conid=900002,
                    quantity="-1", cost_basis_money="-150")
        _add_option(session, "PRIVATE_ACCOUNT_B", snapshot_date, conid=900002,
                    quantity="-2", cost_basis_money="-330")
        # A long protective put only in account A.
        _add_option(session, "PRIVATE_ACCOUNT_A", snapshot_date, conid=900001,
                    quantity="1", cost_basis_money="85", strike=490, mark_price="0.60")


def test_synthetic_fixture_validates_against_schema_and_model():
    payload = json.loads(
        (ROOT / "tests" / "fixtures" / "ibkr-dashboard.options.v1.synthetic.json").read_text(
            encoding="utf-8"
        )
    )

    _validator().validate(payload)
    OptionsContractSnapshot.model_validate(payload)


def test_generated_contracts_validate_against_tracked_schema():
    validator = _validator()
    with session_scope() as session:
        blocked = options_contract_snapshot(session, now=NOW)
    validator.validate(blocked.model_dump(mode="json"))

    _seed_two_accounts(NOW.date())
    with session_scope() as session:
        ready = options_contract_snapshot(session, now=NOW)
    payload = ready.model_dump(mode="json")
    validator.validate(payload)
    assert payload["generated_at"] == "2026-08-02T08:30:00Z"
    assert payload["producer"] == "ibkr-dashboard"


def test_schema_rejects_legs_on_blocked_and_extra_fields():
    validator = _validator()
    leg = {
        "conid": 1, "underlying": "SPY", "right": "P", "strike": 500.0,
        "expiry": "2026-09-18", "multiplier": 100, "quantity": -1.0,
        "currency": "USD", "avg_cost_per_share": 1.5, "mark_price": 1.1,
    }
    blocked = {
        "schema_version": OPTIONS_SCHEMA_VERSION, "producer": "ibkr-dashboard",
        "generated_at": "2026-08-02T08:30:00Z", "as_of": None, "status": "blocked",
        "days_stale": None, "legs": [leg], "warnings": [],
    }
    assert not validator.is_valid(blocked)
    ready_with_account = {
        **blocked, "status": "ready", "as_of": "2026-08-02", "days_stale": 0,
        "legs": [{**leg, "account_id": "PRIVATE_ACCOUNT_A"}],
    }
    assert not validator.is_valid(ready_with_account)


def test_legs_aggregate_by_conid_across_accounts():
    _seed_two_accounts(NOW.date())

    with session_scope() as session:
        contract = options_contract_snapshot(session, now=NOW)

    assert contract.status == "ready"
    assert contract.as_of == NOW.date()
    assert contract.days_stale == 0
    assert contract.warnings == ["MULTIPLE_ACCOUNTS_AGGREGATED"]
    assert [leg.conid for leg in contract.legs] == [900001, 900002]
    short = contract.legs[1]
    assert short.quantity == -3
    assert (short.underlying, short.right, short.strike) == ("SPY", "P", 500.0)
    assert short.expiry == EXPIRY
    assert short.multiplier == 100
    assert short.currency == "USD"
    assert short.mark_price == pytest.approx(1.10)
    # Quantity-weighted: (1 x 1.50 + 2 x 1.65) / 3 = 1.60 per share.
    assert short.avg_cost_per_share == pytest.approx(1.60)
    long = contract.legs[0]
    assert long.quantity == 1
    assert long.avg_cost_per_share == pytest.approx(0.85)


def test_cost_basis_is_per_share_from_cost_basis_money():
    with session_scope() as session:
        session.add(Account(account_id="PRIVATE_ACCOUNT_A", base_currency="USD"))
        # LOT-level Flex rows leave cost_basis_price = costBasisMoney / quantity,
        # i.e. per contract (148.95). The contract must still report per share.
        _add_option(session, "PRIVATE_ACCOUNT_A", NOW.date(), conid=900002,
                    quantity="-1", cost_basis_money="-148.95", cost_basis_price="148.95")
        # Missing cost basis -> null + warning, never invented.
        _add_option(session, "PRIVATE_ACCOUNT_A", NOW.date(), conid=900003,
                    quantity="-2", cost_basis_money=None, root="ACME", right="C",
                    strike=30)

    with session_scope() as session:
        contract = options_contract_snapshot(session, now=NOW)

    by_conid = {leg.conid: leg for leg in contract.legs}
    assert by_conid[900002].avg_cost_per_share == pytest.approx(1.4895)
    assert by_conid[900003].avg_cost_per_share is None
    assert "COST_BASIS_UNAVAILABLE" in contract.warnings


def test_mixed_long_and_short_books_do_not_average_cost():
    with session_scope() as session:
        _add_option(session, "PRIVATE_ACCOUNT_A", NOW.date(), conid=900002,
                    quantity="-2", cost_basis_money="-300")
        _add_option(session, "PRIVATE_ACCOUNT_B", NOW.date(), conid=900002,
                    quantity="1", cost_basis_money="160")

    with session_scope() as session:
        contract = options_contract_snapshot(session, now=NOW)

    (leg,) = contract.legs
    assert leg.quantity == -1
    assert leg.avg_cost_per_share is None
    assert "COST_BASIS_UNAVAILABLE" in contract.warnings


def test_no_snapshot_and_future_snapshot_are_blocked_with_empty_legs():
    with session_scope() as session:
        empty = options_contract_snapshot(session, now=NOW)
    assert empty.status == "blocked"
    assert empty.as_of is None
    assert empty.legs == []
    assert empty.warnings == ["NO_POSITION_SNAPSHOT"]

    _seed_two_accounts(NOW.date() + timedelta(days=1))
    with session_scope() as session:
        future = options_contract_snapshot(session, now=NOW)
    assert future.status == "blocked"
    assert future.legs == []
    assert future.warnings == ["FUTURE_SNAPSHOT_DATE"]


def test_stale_snapshot_keeps_legs_but_is_not_ready():
    _seed_two_accounts(NOW.date() - timedelta(days=8))

    with session_scope() as session:
        contract = options_contract_snapshot(session, now=NOW)

    assert contract.status == "stale"
    assert contract.days_stale == 8
    assert len(contract.legs) == 2
    assert "STALE_POSITION_SNAPSHOT" in contract.warnings


def test_options_closed_after_last_option_day_read_as_no_legs():
    # Options existed yesterday; today's snapshot only holds stock.
    _seed_two_accounts(NOW.date() - timedelta(days=1))
    with session_scope() as session:
        session.add(Instrument(conid=101, symbol="ACME", asset_class="STK", currency="USD"))
        session.flush()
        session.add(
            PositionEod(
                snapshot_date=NOW.date(), account_id="PRIVATE_ACCOUNT_A", conid=101,
                symbol="ACME", quantity=Decimal("100"), currency="USD",
                fx_rate_to_base=Decimal("1"), asset_class="STK",
            )
        )

    with session_scope() as session:
        contract = options_contract_snapshot(session, now=NOW)

    assert contract.status == "ready"
    assert contract.as_of == NOW.date()
    assert contract.legs == []
    assert contract.warnings == ["NO_OPEN_OPTION_POSITIONS"]


def test_contract_exposes_no_account_nav_pnl_or_raw_fields():
    _seed_two_accounts(NOW.date())

    with session_scope() as session:
        contract = options_contract_snapshot(session, now=NOW)

    encoded = json.dumps(contract.model_dump(mode="json"))
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
    assert "PRIVATE_ACCOUNT" not in encoded
    for forbidden in (
        "account_id", "accountid", "alias", "nav", "pnl", "unrealized", "position_value",
        "cost_basis_money", "fx_rate", "raw_xml", "credential", "order",
    ):
        assert forbidden not in encoded.lower(), forbidden
        assert forbidden not in schema_text.lower(), forbidden


def test_api_endpoint_declares_and_returns_versioned_response_model():
    # The age rule compares against the UTC date (same as portfolio.v1).
    _seed_two_accounts(datetime.now(UTC).date())
    client = TestClient(create_app())

    response = client.get("/options/contract-snapshot")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == OPTIONS_SCHEMA_VERSION
    assert body["status"] == "ready"
    _validator().validate(body)
    operation = client.app.openapi()["paths"]["/options/contract-snapshot"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/OptionsContractSnapshot")
