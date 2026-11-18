"""Strict reader for OptimizadorFiscal scenario contract files.

The producer owns calculations and the contract payload. This module only
validates and presents a configured local file. It never writes to the file,
opens broker credentials, or submits orders.
"""

from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


EXPECTED_SCHEMA_VERSION = "tax_sale_scenarios.v1"
MAX_SCENARIO_FILE_BYTES = 2 * 1024 * 1024

DecimalString = Annotated[str, Field(pattern=r"^-?[0-9]+(?:\.[0-9]+)?$")]
HashString = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
ScenarioStatus = Literal["draft", "reviewed", "approved", "blocked"]


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaxProfile(StrictContractModel):
    jurisdiction: Literal["ES"]
    tax_year: int = Field(ge=2007)
    residency_status: Literal["confirmed", "unconfirmed"]
    regime: str
    region: str


class RulePack(StrictContractModel):
    id: str
    version: str
    review_status: Literal["draft", "reviewed", "approved", "expired"]
    content_hash: HashString


class SaleCandidate(StrictContractModel):
    candidate_id: Annotated[
        str, Field(pattern=r"^candidate_[a-zA-Z0-9_-]+$")
    ]
    instrument_id: Annotated[str, Field(min_length=1)]
    selection_source: Literal["manual", "metodo_tendencia_exit"]
    source_ref: Annotated[str, Field(min_length=1)]
    selected_at: datetime
    signal_kind: Literal["exit_mp", "exit_lp"] | None

    @model_validator(mode="after")
    def validate_signal_kind(self) -> "SaleCandidate":
        if self.selection_source == "manual" and self.signal_kind is not None:
            raise ValueError("manual candidates cannot declare a trend signal")
        if (
            self.selection_source == "metodo_tendencia_exit"
            and self.signal_kind is None
        ):
            raise ValueError("trend-exit candidates require an MP or LP signal")
        return self


class CandidateSet(StrictContractModel):
    candidate_set_id: Annotated[
        str, Field(pattern=r"^candidate_set_[a-zA-Z0-9_-]+$")
    ]
    generated_at: datetime
    candidates: list[SaleCandidate]


class FifoAllocation(StrictContractModel):
    acquisition_event_id: str
    quantity: DecimalString


class Sale(StrictContractModel):
    candidate_id: Annotated[
        str, Field(pattern=r"^candidate_[a-zA-Z0-9_-]+$")
    ]
    instrument_id: str
    quantity: DecimalString
    assumed_sale_date: date
    assumed_price: DecimalString
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    fifo_allocations: list[FifoAllocation]
    estimated_gain_loss_eur: DecimalString
    deferred_loss_eur: DecimalString


class ScenarioSummary(StrictContractModel):
    estimated_proceeds_eur: DecimalString
    estimated_gain_loss_eur: DecimalString
    estimated_tax_delta_eur: DecimalString
    estimated_transaction_cost_eur: DecimalString
    portfolio_deviation_score: DecimalString


class TaxScenario(StrictContractModel):
    scenario_id: str
    status: ScenarioStatus
    objective: str
    sales: list[Sale]
    summary: ScenarioSummary
    warnings: list[str]
    blockers: list[str]


class TaxScenarioSet(StrictContractModel):
    schema_version: Literal[EXPECTED_SCHEMA_VERSION]
    scenario_set_id: Annotated[
        str, Field(pattern=r"^scenario_set_[a-zA-Z0-9_-]+$")
    ]
    generated_at: datetime
    snapshot_id: str
    snapshot_hash: HashString
    tax_profile: TaxProfile
    rule_pack: RulePack
    candidate_set: CandidateSet
    status: ScenarioStatus
    scenarios: list[TaxScenario]
    warnings: list[str]
    blockers: list[str]

    @model_validator(mode="after")
    def validate_sale_candidates(self) -> "TaxScenarioSet":
        candidates: dict[str, SaleCandidate] = {}
        for candidate in self.candidate_set.candidates:
            if candidate.candidate_id in candidates:
                raise ValueError("candidate identifiers must be unique")
            candidates[candidate.candidate_id] = candidate

        for scenario in self.scenarios:
            for sale in scenario.sales:
                candidate = candidates.get(sale.candidate_id)
                if candidate is None:
                    raise ValueError("every sale must reference an approved candidate")
                if candidate.instrument_id != sale.instrument_id:
                    raise ValueError("sale and candidate instruments must match")
        return self


class TaxIntegrationResponse(StrictContractModel):
    integration_status: Literal["not_configured", "missing", "invalid", "ready"]
    reason_code: str | None
    expected_schema_version: Literal[EXPECTED_SCHEMA_VERSION]
    document_hash: HashString | None
    scenario_set: TaxScenarioSet | None


def _response(
    integration_status: Literal["not_configured", "missing", "invalid", "ready"],
    *,
    reason_code: str | None = None,
    document_hash: str | None = None,
    scenario_set: TaxScenarioSet | None = None,
) -> TaxIntegrationResponse:
    return TaxIntegrationResponse(
        integration_status=integration_status,
        reason_code=reason_code,
        expected_schema_version=EXPECTED_SCHEMA_VERSION,
        document_hash=document_hash,
        scenario_set=scenario_set,
    )


def load_tax_scenarios(path: Path | None) -> TaxIntegrationResponse:
    """Load one configured contract file and fail closed on every ambiguity."""

    if path is None:
        return _response("not_configured", reason_code="path_not_configured")

    try:
        if not path.is_file():
            return _response("missing", reason_code="file_not_found")
        with path.open("rb") as contract_file:
            raw = contract_file.read(MAX_SCENARIO_FILE_BYTES + 1)
    except OSError:
        return _response("invalid", reason_code="file_unreadable")

    if len(raw) > MAX_SCENARIO_FILE_BYTES:
        return _response("invalid", reason_code="file_too_large")

    document_hash = f"sha256:{sha256(raw).hexdigest()}"
    try:
        scenario_set = TaxScenarioSet.model_validate_json(raw)
    except (ValidationError, ValueError):
        return _response(
            "invalid",
            reason_code="contract_validation_failed",
            document_hash=document_hash,
        )

    return _response(
        "ready",
        document_hash=document_hash,
        scenario_set=scenario_set,
    )
