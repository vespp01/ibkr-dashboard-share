"""Read-only status roll-up for every cross-project / broker / service edge.

This module never exposes secrets. It reports whether each integration is
configured (booleans), the *name* of the env var that toggles it, and the
health token already computed by each integration's own reader. It never
returns token values, chat ids, account ids, or configured file paths.

The frontend "Integraciones" tab presents this. All human-facing prose is
resolved client-side from the stable tokens below (``status`` / ``reason_code``
/ ``direction`` / ``category``); ``mechanism`` and ``control`` are technical
identifiers shown verbatim.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..notify.telegram import is_configured as telegram_is_configured
from .investmentlab import build_investmentlab_portfolio_snapshot
from .optimizador_fiscal import load_tax_scenarios


IntegrationCategory = Literal["project", "broker", "service"]
IntegrationDirection = Literal["outbound", "inbound", "bidirectional"]
IntegrationLevel = Literal["ok", "warn", "error", "off", "info"]
IntegrationControlKind = Literal["env", "route", "none"]


class IntegrationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: IntegrationCategory
    name: str
    contract: str | None
    direction: IntegrationDirection
    mechanism: str
    enabled: bool
    implemented: bool
    removable: bool
    status: str
    level: IntegrationLevel
    control: str | None
    control_kind: IntegrationControlKind
    reason_code: str | None = None
    as_of: str | None = None
    days_stale: int | None = None
    days_to_expiry: int | None = None


class IntegrationsStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    integrations: list[IntegrationStatus]


def _investmentlab_portfolio(session: Session) -> IntegrationStatus:
    snapshot = build_investmentlab_portfolio_snapshot(session)
    level: IntegrationLevel = {
        "ready": "ok",
        "stale": "warn",
        "blocked": "warn",
    }[snapshot.status]
    reason_code = snapshot.warnings[0] if snapshot.warnings else None
    return IntegrationStatus(
        id="investmentlab_portfolio",
        category="project",
        name="Investmentlab",
        contract="ibkr-dashboard.portfolio.v1",
        direction="outbound",
        mechanism="HTTP GET pull",
        enabled=True,
        implemented=True,
        removable=True,
        status=snapshot.status,
        level=level,
        control="GET /portfolio/investmentlab-snapshot",
        control_kind="route",
        reason_code=reason_code,
        as_of=snapshot.as_of.isoformat() if snapshot.as_of else None,
        days_stale=snapshot.days_stale,
    )


def _metodo_tendencia_portfolio(session: Session) -> IntegrationStatus:
    """metodo-tendencia consume el MISMO snapshot (read-only, loopback) para avisar
    por el bot de Telegram de este proyecto de las salidas del Método Tendencia de
    posiciones reales."""
    snapshot = build_investmentlab_portfolio_snapshot(session)
    level: IntegrationLevel = {
        "ready": "ok",
        "stale": "warn",
        "blocked": "warn",
    }[snapshot.status]
    reason_code = snapshot.warnings[0] if snapshot.warnings else None
    return IntegrationStatus(
        id="metodo_tendencia_portfolio",
        category="project",
        name="Método Tendencia",
        contract="ibkr-dashboard.portfolio.v1",
        direction="outbound",
        mechanism=(
            "HTTP GET pull, lunes 08:30 (+ reintentos 09:30 y 12:00; una vez por barra) "
            "→ Telegram (bot de este proyecto)"
        ),
        enabled=True,
        implemented=True,
        removable=True,
        status=snapshot.status,
        level=level,
        control="GET /portfolio/investmentlab-snapshot",
        control_kind="route",
        reason_code=reason_code,
        as_of=snapshot.as_of.isoformat() if snapshot.as_of else None,
        days_stale=snapshot.days_stale,
    )


def _patrimoniolab_portfolio(session: Session) -> IntegrationStatus:
    """PatrimonioLab consume el MISMO snapshot (read-only, loopback) para dimensionar
    la cobertura sugerida y avisa por el bot de Telegram de este proyecto."""
    snapshot = build_investmentlab_portfolio_snapshot(session)
    level: IntegrationLevel = {
        "ready": "ok",
        "stale": "warn",
        "blocked": "warn",
    }[snapshot.status]
    reason_code = snapshot.warnings[0] if snapshot.warnings else None
    return IntegrationStatus(
        id="patrimoniolab_portfolio",
        category="project",
        name="PatrimonioLab",
        contract="ibkr-dashboard.portfolio.v1",
        direction="outbound",
        mechanism=(
            "HTTP GET pull (lunes-sábado 08:10 + revisión trimestral) "
            "→ Telegram (bot de este proyecto)"
        ),
        enabled=True,
        implemented=True,
        removable=True,
        status=snapshot.status,
        level=level,
        control="GET /portfolio/investmentlab-snapshot",
        control_kind="route",
        reason_code=reason_code,
        as_of=snapshot.as_of.isoformat() if snapshot.as_of else None,
        days_stale=snapshot.days_stale,
    )


def _optimizador_scenarios(settings: Settings) -> IntegrationStatus:
    response = load_tax_scenarios(settings.tax_scenarios_file)
    status = response.integration_status
    level: IntegrationLevel = {
        "not_configured": "off",
        "missing": "warn",
        "invalid": "error",
        "ready": "ok",
    }[status]
    return IntegrationStatus(
        id="optimizador_fiscal_scenarios",
        category="project",
        name="OptimizadorFiscal",
        contract="tax_sale_scenarios.v1",
        direction="inbound",
        mechanism="local JSON file (bind-mount RO)",
        enabled=status != "not_configured",
        implemented=True,
        removable=True,
        status=status,
        level=level,
        control="TAX_SCENARIOS_FILE",
        control_kind="env",
        reason_code=response.reason_code,
    )


def _optimizador_snapshot() -> IntegrationStatus:
    """Outbound portfolio→tax export: designed, producer not built yet."""
    return IntegrationStatus(
        id="optimizador_fiscal_snapshot",
        category="project",
        name="OptimizadorFiscal",
        contract="portfolio_tax_snapshot.v1",
        direction="outbound",
        mechanism="local JSON export",
        enabled=False,
        implemented=False,
        removable=False,
        status="planned",
        level="off",
        control=None,
        control_kind="none",
        reason_code="producer_not_implemented",
    )


def _telegram(settings: Settings) -> IntegrationStatus:
    configured = telegram_is_configured(settings)
    return IntegrationStatus(
        id="telegram",
        category="service",
        name="Telegram",
        contract=None,
        direction="outbound",
        mechanism=(
            "httpx → api.telegram.org (bot compartido con metodo-tendencia y PatrimonioLab)"
        ),
        enabled=configured,
        implemented=True,
        removable=True,
        status="configured" if configured else "not_configured",
        level="ok" if configured else "off",
        control="TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID",
        control_kind="env",
    )


def _live_overlay(settings: Settings) -> IntegrationStatus:
    enabled = settings.enable_live_overlay
    return IntegrationStatus(
        id="live_overlay",
        category="broker",
        name="IB Gateway (live overlay)",
        contract=None,
        direction="inbound",
        mechanism="TCP socket · ib_async (read-only)",
        enabled=enabled,
        implemented=True,
        removable=True,
        status="enabled" if enabled else "disabled",
        level="ok" if enabled else "off",
        control="ENABLE_LIVE_OVERLAY",
        control_kind="env",
    )


def _margin_fx(settings: Settings) -> IntegrationStatus:
    enabled = settings.margin_fx_enabled
    return IntegrationStatus(
        id="margin_fx",
        category="service",
        name="IBKR rates + ECB FX",
        contract=None,
        direction="inbound",
        mechanism="HTML scrape + ECB API",
        enabled=enabled,
        implemented=True,
        removable=True,
        status="enabled" if enabled else "disabled",
        level="ok" if enabled else "off",
        control="MARGIN_FX_ENABLED",
        control_kind="env",
    )


def _flex(
    settings: Settings, *, today: date, session: Session | None = None
) -> IntegrationStatus:
    configured = bool(settings.ib_flex_token and settings.ib_flex_query_id)
    status = "configured" if configured else "not_configured"
    level: IntegrationLevel = "ok" if configured else "warn"
    days_to_expiry: int | None = None
    reason_code: str | None = None
    as_of: str | None = None
    if configured and session is not None:
        # The newest Activity run decides: a token that is "valid" on paper
        # while every pull fails (1012/1025) must not read green (B47).
        from ..jobs.health import compute_staleness, latest_run_error

        qid = settings.ib_flex_query_id
        if latest_run_error(session, query_id=qid) is not None:
            status, level, reason_code = "stale", "error", "pull_failing"
        else:
            st = compute_staleness(
                session, threshold_hours=settings.pipeline_stale_after_hours, query_id=qid
            )
            if st["is_stale"] and st["last_success_at"] is not None:
                status, level, reason_code = "stale", "error", "pull_stale"
        last = compute_staleness(session, threshold_hours=1e9, query_id=qid)["last_success_at"]
        as_of = last[:10] if last else None
    if configured and settings.ib_flex_token_expiry:
        try:
            expiry = date.fromisoformat(settings.ib_flex_token_expiry)
        except ValueError:
            expiry = None
        if expiry is not None:
            days_to_expiry = (expiry - today).days
            if days_to_expiry < 0:
                status, level, reason_code = "expired", "error", None
            elif days_to_expiry <= settings.token_expiry_warn_days and level == "ok":
                status, level = "expiring", "warn"
    return IntegrationStatus(
        id="flex",
        category="broker",
        name="IBKR Flex Web Service",
        contract=None,
        direction="inbound",
        mechanism="Flex XML pull",
        enabled=configured,
        implemented=True,
        removable=False,
        status=status,
        level=level,
        control="IB_FLEX_TOKEN + IB_FLEX_QUERY_ID",
        control_kind="env",
        reason_code=reason_code,
        as_of=as_of,
        days_to_expiry=days_to_expiry,
    )


def build_integrations_status(
    session: Session,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> IntegrationsStatusResponse:
    """Assemble the read-only status of every integration edge."""
    settings = settings or get_settings()
    generated_at = (now or datetime.now(UTC)).astimezone(UTC)
    today = generated_at.date()
    integrations = [
        _investmentlab_portfolio(session),
        _metodo_tendencia_portfolio(session),
        _patrimoniolab_portfolio(session),
        _optimizador_scenarios(settings),
        _optimizador_snapshot(),
        _flex(settings, today=today, session=session),
        _live_overlay(settings),
        _telegram(settings),
        _margin_fx(settings),
    ]
    return IntegrationsStatusResponse(
        generated_at=generated_at, integrations=integrations
    )
