"""POST /pipeline/run contract — sync default, ?async=true queues with 202.

Finding #46 (A7): the synchronous path can block up to flex_max_wait_seconds
(300 s) — exactly nginx's proxy_read_timeout. The mitigation is the documented
``?async=true`` mode, which must return 202 immediately and run the pipeline
as a background task. These tests pin that contract (run_full_pipeline is
stubbed — no network).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ibkr_backend.api.main import create_app
from ibkr_backend.api.routes import pipeline as pipeline_routes
from ibkr_backend import config as cfg


@pytest.fixture()
def configured_flex(monkeypatch):
    """Pretend a Flex token/query id are configured (no network is hit)."""
    monkeypatch.setenv("IB_FLEX_TOKEN", "t" * 16)
    monkeypatch.setenv("IB_FLEX_QUERY_ID", "123456")
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]


@pytest.fixture()
def pipeline_stub(monkeypatch):
    """Record calls to run_full_pipeline instead of hitting IBKR."""
    calls: list[dict] = []

    def _stub(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            reference_code="REF123",
            from_date=None,
            to_date=None,
            statements=1,
            total_inserted=0,
            total_skipped=0,
            sections=[],
        )

    monkeypatch.setattr(pipeline_routes, "run_full_pipeline", _stub)
    return calls


@pytest.fixture()
def unconfigured_flex(monkeypatch):
    """Force token/query id empty regardless of a local .env. Env vars override
    the .env file in pydantic-settings, so the test is hermetic on dev machines
    too (CI has no .env; a populated .env used to flip this assertion to a 500)."""
    monkeypatch.setenv("IB_FLEX_TOKEN", "")
    monkeypatch.setenv("IB_FLEX_QUERY_ID", "")
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]


def test_run_unconfigured_returns_400(unconfigured_flex):
    """Without token/query id the endpoint refuses early — no blocking."""
    client = TestClient(create_app())
    r = client.post("/pipeline/run")
    assert r.status_code == 400


def test_run_async_returns_202_queued(configured_flex, pipeline_stub):
    """?async=true → immediate 202 + queued; pipeline runs in background."""
    client = TestClient(create_app())
    r = client.post("/pipeline/run?async=true&note=test-async")
    assert r.status_code == 202
    assert r.json() == {"status": "queued", "note": "test-async"}
    # TestClient executes background tasks before returning the response.
    assert len(pipeline_stub) == 1
    assert pipeline_stub[0]["note"] == "test-async"


def test_run_sync_returns_200_with_summary(configured_flex, pipeline_stub):
    """Default (sync) mode blocks and returns the run summary inline."""
    client = TestClient(create_app())
    r = client.post("/pipeline/run")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["reference_code"] == "REF123"
    assert len(pipeline_stub) == 1
