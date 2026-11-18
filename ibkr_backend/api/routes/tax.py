"""Read-only OptimizadorFiscal presentation endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from ...config import get_settings
from ...integrations.optimizador_fiscal import (
    TaxIntegrationResponse,
    load_tax_scenarios,
)


router = APIRouter()


@router.get(
    "/scenarios",
    response_model=TaxIntegrationResponse,
    summary="Read the latest validated OptimizadorFiscal scenario contract",
)
def scenarios() -> TaxIntegrationResponse:
    """Present a local contract file without exposing its configured path."""

    return load_tax_scenarios(get_settings().tax_scenarios_file)
