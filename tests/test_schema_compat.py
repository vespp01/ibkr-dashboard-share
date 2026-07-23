"""Compatibility migrations for existing SQLite databases."""

from __future__ import annotations

from sqlalchemy import inspect, text


def test_init_db_adds_option_columns_to_existing_instruments_table(tmp_path, monkeypatch):
    """`create_all()` does not alter existing tables. The app boots against a
    long-lived SQLite volume, so additive nullable columns need an explicit
    compatibility step instead of requiring a rebootstrap.
    """
    dbfile = tmp_path / "old.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{dbfile.as_posix()}")

    from ibkr_backend import config as cfg
    from ibkr_backend.db import session as sess

    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    sess.engine = sess._build_engine()  # type: ignore[attr-defined]
    sess.SessionLocal.configure(bind=sess.engine)

    with sess.engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE instruments "
                "(conid INTEGER PRIMARY KEY, symbol VARCHAR(64) NOT NULL)"
            )
        )

    sess.init_db()

    cols = {col["name"] for col in inspect(sess.engine).get_columns("instruments")}
    assert {"option_underlying", "option_expiry", "option_right", "option_strike"} <= cols


def test_init_db_backfills_existing_option_instruments(tmp_path, monkeypatch):
    dbfile = tmp_path / "backfill.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{dbfile.as_posix()}")

    from ibkr_backend import config as cfg
    from ibkr_backend.db import session as sess
    from ibkr_backend.db.models import Instrument

    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    sess.engine = sess._build_engine()  # type: ignore[attr-defined]
    sess.SessionLocal.configure(bind=sess.engine)
    sess.init_db()

    with sess.SessionLocal() as session:
        session.add(
            Instrument(
                conid=42,
                symbol="MSFT  260619C00400000",
                description=None,
                asset_class="OPT",
            )
        )
        session.commit()

    sess.init_db()

    with sess.SessionLocal() as session:
        instr = session.get(Instrument, 42)
        assert instr is not None
        assert instr.option_underlying == "MSFT"
        assert instr.option_expiry.isoformat() == "2026-06-19"
        assert instr.option_right == "C"
        assert instr.option_strike == 400
