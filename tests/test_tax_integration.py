"""Read-only OptimizadorFiscal contract integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ibkr_backend import config as cfg
from ibkr_backend.api.main import create_app


FIXTURE = Path(__file__).parent / "fixtures" / "tax_sale_scenarios.v1.synthetic.json"


def _client_with_path(monkeypatch, value: str) -> TestClient:
    monkeypatch.setenv("TAX_SCENARIOS_FILE", value)
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    return TestClient(create_app())


def _fixture_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _write_fixture_variant(tmp_path: Path, name: str, payload: dict) -> Path:
    output = tmp_path / name
    output.write_text(json.dumps(payload), encoding="utf-8")
    return output


def _set_nested(payload: dict, path: tuple[str | int, ...], value) -> None:
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def test_scenarios_not_configured_is_safe(monkeypatch):
    response = _client_with_path(monkeypatch, "").get("/tax/scenarios")

    assert response.status_code == 200
    assert response.json() == {
        "integration_status": "not_configured",
        "reason_code": "path_not_configured",
        "expected_schema_version": "tax_sale_scenarios.v1",
        "document_hash": None,
        "scenario_set": None,
    }


def test_scenarios_missing_file_fails_closed(monkeypatch, tmp_path):
    missing = tmp_path / "missing.json"
    response = _client_with_path(monkeypatch, str(missing)).get("/tax/scenarios")

    assert response.status_code == 200
    assert response.json()["integration_status"] == "missing"
    assert response.json()["reason_code"] == "file_not_found"
    assert "path" not in response.json()


def test_scenarios_valid_contract_is_returned(monkeypatch):
    response = _client_with_path(monkeypatch, str(FIXTURE)).get("/tax/scenarios")

    assert response.status_code == 200
    body = response.json()
    assert body["integration_status"] == "ready"
    assert body["document_hash"].startswith("sha256:")
    assert body["scenario_set"]["schema_version"] == "tax_sale_scenarios.v1"
    assert body["scenario_set"]["status"] == "blocked"
    assert {
        candidate["selection_source"]
        for candidate in body["scenario_set"]["candidate_set"]["candidates"]
    } == {"manual", "metodo_tendencia_exit"}
    assert body["scenario_set"]["scenarios"][0]["sales"][0]["candidate_id"] == (
        "candidate_mt_exit_alpha"
    )
    assert body["scenario_set"]["scenarios"][0]["sales"][0]["fifo_allocations"] == [
        {"acquisition_event_id": "event_buy_oldest", "quantity": "6"}
    ]


def test_scenario_without_candidate_set_is_invalid(monkeypatch, tmp_path):
    payload = _fixture_payload()
    payload.pop("candidate_set")
    invalid = _write_fixture_variant(
        tmp_path,
        "without-candidate-set.json",
        payload,
    )

    body = _client_with_path(monkeypatch, str(invalid)).get("/tax/scenarios").json()

    assert body["integration_status"] == "invalid"
    assert body["reason_code"] == "contract_validation_failed"


@pytest.mark.parametrize(
    ("name", "path", "value"),
    [
        (
            "missing-candidate.json",
            ("scenarios", 0, "sales", 0, "candidate_id"),
            "candidate_missing",
        ),
        (
            "mismatched-instrument.json",
            ("scenarios", 0, "sales", 0, "instrument_id"),
            "instrument_synthetic_other",
        ),
        (
            "manual-with-signal.json",
            ("candidate_set", "candidates", 1, "signal_kind"),
            "exit_lp",
        ),
        (
            "trend-without-signal.json",
            ("candidate_set", "candidates", 0, "signal_kind"),
            None,
        ),
        (
            "unknown-source.json",
            ("candidate_set", "candidates", 0, "selection_source"),
            "portfolio_scan",
        ),
    ],
)
def test_candidate_gate_rejects_invalid_contracts(
    monkeypatch, tmp_path, name, path, value
):
    payload = _fixture_payload()
    _set_nested(payload, path, value)
    invalid = _write_fixture_variant(tmp_path, name, payload)

    body = _client_with_path(monkeypatch, str(invalid)).get("/tax/scenarios").json()

    assert body["integration_status"] == "invalid"
    assert body["reason_code"] == "contract_validation_failed"


def test_duplicate_candidate_identifier_is_invalid(monkeypatch, tmp_path):
    payload = _fixture_payload()
    payload["candidate_set"]["candidates"][1]["candidate_id"] = payload[
        "candidate_set"
    ]["candidates"][0]["candidate_id"]

    invalid = _write_fixture_variant(
        tmp_path, "duplicate-candidate.json", payload
    )

    body = _client_with_path(monkeypatch, str(invalid)).get("/tax/scenarios").json()

    assert body["integration_status"] == "invalid"
    assert body["reason_code"] == "contract_validation_failed"


def test_scenarios_wrong_contract_version_is_invalid(monkeypatch, tmp_path):
    invalid = tmp_path / "wrong-version.json"
    invalid.write_text('{"schema_version":"tax_sale_scenarios.v2"}', encoding="utf-8")

    response = _client_with_path(monkeypatch, str(invalid)).get("/tax/scenarios")

    assert response.status_code == 200
    body = response.json()
    assert body["integration_status"] == "invalid"
    assert body["reason_code"] == "contract_validation_failed"
    assert body["document_hash"].startswith("sha256:")
    assert body["scenario_set"] is None


def test_scenarios_oversized_file_is_rejected_without_hashing(monkeypatch, tmp_path):
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (2 * 1024 * 1024 + 1))

    response = _client_with_path(monkeypatch, str(oversized)).get("/tax/scenarios")

    assert response.status_code == 200
    assert response.json()["integration_status"] == "invalid"
    assert response.json()["reason_code"] == "file_too_large"
    assert response.json()["document_hash"] is None


def test_scenarios_extra_fields_are_rejected(monkeypatch, tmp_path):
    raw = FIXTURE.read_text(encoding="utf-8")
    invalid = tmp_path / "extra-field.json"
    invalid.write_text(raw.replace('"blockers": [', '"unexpected": true, "blockers": [', 1), encoding="utf-8")

    response = _client_with_path(monkeypatch, str(invalid)).get("/tax/scenarios")

    assert response.status_code == 200
    assert response.json()["integration_status"] == "invalid"
    assert response.json()["reason_code"] == "contract_validation_failed"
