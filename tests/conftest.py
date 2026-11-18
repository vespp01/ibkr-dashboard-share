"""Test fixtures — isolated SQLite DB per test, synthetic Flex XML."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Force a temporary SQLite DB before importing any backend module so settings
# pick it up cleanly.
os.environ.setdefault("DATABASE_URL", "sqlite:///./.test.db")
os.environ.setdefault("IB_FLEX_TOKEN", "")
os.environ.setdefault("IB_FLEX_QUERY_ID", "")
os.environ.setdefault("ENABLE_LIVE_OVERLAY", "false")
os.environ.setdefault("TAX_SCENARIOS_FILE", "")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Per-test isolated SQLite file."""
    dbfile = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{dbfile.as_posix()}")
    # Force settings cache + engine rebuild
    from ibkr_backend import config as cfg
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    from ibkr_backend.db import session as sess

    sess.engine = sess._build_engine()  # type: ignore[attr-defined]
    sess.SessionLocal.configure(bind=sess.engine)
    sess.init_db()
    yield


@pytest.fixture()
def sample_flex_xml() -> bytes:
    """Tiny synthetic Activity Flex XML covering the sections we load.

    Modelled on real Flex output — attribute names match IBKR's reference.
    """
    p = Path(__file__).parent / "fixtures" / "sample_flex.xml"
    return p.read_bytes()
