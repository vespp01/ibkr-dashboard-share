"""Read-only integrations status roll-up (Integraciones tab)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from ibkr_backend import config as cfg
from ibkr_backend.api.main import create_app
from ibkr_backend.config import Settings
from ibkr_backend.db import session_scope
from ibkr_backend.integrations.status import _flex, build_integrations_status


FIXTURE = Path(__file__).parent / "fixtures" / "tax_sale_scenarios.v1.synthetic.json"

EXPECTED_IDS = {
    "investmentlab_portfolio",
    "metodo_tendencia_portfolio",
    "patrimoniolab_portfolio",
    "optimizador_fiscal_scenarios",
    "optimizador_fiscal_snapshot",
    "flex",
    "live_overlay",
    "telegram",
    "margin_fx",
}


def test_status_endpoint_lists_every_edge():
    client = TestClient(create_app())
    body = client.get("/integrations/status").json()

    assert {row["id"] for row in body["integrations"]} == EXPECTED_IDS
    assert "generated_at" in body
    by_id = {row["id"]: row for row in body["integrations"]}
    # conftest forces TAX_SCENARIOS_FILE + ENABLE_LIVE_OVERLAY off; it does NOT
    # touch Telegram, so that one is environment-dependent — don't assert it here
    # (the sentinel test covers Telegram's configured path).
    assert by_id["optimizador_fiscal_scenarios"]["status"] == "not_configured"
    assert by_id["live_overlay"]["status"] == "disabled"
    # The designed-but-unbuilt export is flagged, never claimed as implemented.
    assert by_id["optimizador_fiscal_snapshot"]["implemented"] is False
    assert by_id["optimizador_fiscal_snapshot"]["status"] == "planned"


def test_status_never_leaks_secret_values():
    """Only env-var NAMES and booleans are exposed, never token/chat values."""
    settings = Settings(
        ib_flex_token="SENTINEL_FLEX_TOKEN",
        ib_flex_query_id="SENTINEL_QUERY_ID",
        telegram_bot_token="SENTINEL_BOT_TOKEN",
        telegram_chat_id="SENTINEL_CHAT_ID",
    )
    with session_scope() as session:
        response = build_integrations_status(session, settings=settings)

    dumped = response.model_dump_json()
    for secret in (
        "SENTINEL_FLEX_TOKEN",
        "SENTINEL_QUERY_ID",
        "SENTINEL_BOT_TOKEN",
        "SENTINEL_CHAT_ID",
    ):
        assert secret not in dumped

    by_id = {row.id: row for row in response.integrations}
    # The control field exposes the lever's NAME, not its value.
    assert by_id["flex"].control == "IB_FLEX_TOKEN + IB_FLEX_QUERY_ID"
    assert by_id["telegram"].control == "TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID"
    assert by_id["flex"].enabled is True
    assert by_id["telegram"].enabled is True


def test_scenarios_flag_reflected_when_configured():
    settings = Settings(tax_scenarios_file=str(FIXTURE))
    with session_scope() as session:
        response = build_integrations_status(session, settings=settings)
    by_id = {row.id: row for row in response.integrations}
    assert by_id["optimizador_fiscal_scenarios"].status == "ready"
    assert by_id["optimizador_fiscal_scenarios"].enabled is True


def test_flex_token_expiry_transitions():
    base = dict(ib_flex_token="t", ib_flex_query_id="q", token_expiry_warn_days=7)
    today = date(2026, 8, 9)

    healthy = _flex(Settings(**base, ib_flex_token_expiry="2026-12-31"), today=today)
    assert healthy.status == "configured"
    assert healthy.level == "ok"
    assert healthy.days_to_expiry == (date(2026, 12, 31) - today).days

    expiring = _flex(Settings(**base, ib_flex_token_expiry="2026-08-12"), today=today)
    assert expiring.status == "expiring"
    assert expiring.level == "warn"

    expired = _flex(Settings(**base, ib_flex_token_expiry="2026-08-01"), today=today)
    assert expired.status == "expired"
    assert expired.level == "error"


def test_flex_not_configured_warns():
    settings = Settings(ib_flex_token="", ib_flex_query_id="")
    result = _flex(settings, today=date(2026, 8, 9))
    assert result.status == "not_configured"
    assert result.level == "warn"
    assert result.enabled is False
    assert result.removable is False


def test_flex_status_downgrades_from_newest_activity_run():
    """B47: a valid-on-paper token must not read green while every Activity pull fails."""
    from datetime import timedelta

    from ibkr_backend.db import session_scope
    from ibkr_backend.db.models import FlexRun, _utcnow

    settings = Settings(
        ib_flex_token="t", ib_flex_query_id="ACT", ib_flex_query_id_tc="TC",
        ib_flex_token_expiry="2099-12-31",
    )
    with session_scope() as s:
        s.add(FlexRun(query_id="ACT", status="success", fetched_at=_utcnow() - timedelta(days=3)))
        s.add(FlexRun(query_id="ACT", status="error", fetched_at=_utcnow() - timedelta(hours=1),
                      error="FlexError: Flex 1025: Invalid token"))
        s.add(FlexRun(query_id="TC", status="success", fetched_at=_utcnow()))  # must not mask
    with session_scope() as s:
        flex = _flex(settings, today=date(2026, 10, 7), session=s)
    assert (flex.status, flex.level, flex.reason_code) == ("stale", "error", "pull_failing")

    with session_scope() as s:
        s.add(FlexRun(query_id="ACT", status="success", fetched_at=_utcnow()))
    with session_scope() as s:
        flex = _flex(settings, today=date(2026, 10, 7), session=s)
    assert (flex.status, flex.level, flex.reason_code) == ("configured", "ok", None)
