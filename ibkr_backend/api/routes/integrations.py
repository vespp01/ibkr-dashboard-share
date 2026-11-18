"""Read-only status roll-up for every integration edge (Integraciones tab)."""

from __future__ import annotations

from fastapi import APIRouter

from ...integrations.status import (
    IntegrationsStatusResponse,
    build_integrations_status,
)
from ..deps import SessionDep

router = APIRouter()


@router.get(
    "/status",
    response_model=IntegrationsStatusResponse,
    summary="Non-secret status of every cross-project / broker / service edge",
)
def integrations_status(session: SessionDep) -> IntegrationsStatusResponse:
    """Report each integration's health and control lever without secrets."""

    return build_integrations_status(session)
