"""SQLAlchemy 2.0 engine + session management.

SQLite is the default and is configured with WAL + foreign keys via PRAGMA so
multiple readers can run alongside the daily-pull writer.

Switching to Postgres is a one-line `DATABASE_URL` change; the schema is
portable (no SQLite-specific functions in the models).
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy import create_engine, event, inspect, or_, select, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..config import get_settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


def _build_engine():
    settings = get_settings()
    url = settings.database_url
    is_sqlite = url.startswith("sqlite")
    kwargs: dict = {"future": True, "echo": False}
    if is_sqlite:
        # check_same_thread=False so FastAPI threadpool can share connections,
        # safe because we only use Session per-request.
        kwargs["connect_args"] = {"check_same_thread": False}
    eng = create_engine(url, **kwargs)
    if is_sqlite:
        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA foreign_keys=ON;")
            cur.execute("PRAGMA busy_timeout=5000;")
            cur.close()
    return eng


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency."""
    with SessionLocal() as session:
        yield session


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Standalone scripts. Commits on success, rolls back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create tables if they don't exist. Idempotent."""
    from . import models  # noqa: F401  — registers models on Base.metadata

    Base.metadata.create_all(engine)
    _ensure_schema_compat()
    _backfill_instrument_option_fields()
    log.info("Database initialised at %s", get_settings().database_url)


def _ensure_schema_compat() -> None:
    """Apply tiny additive migrations for existing SQLite/Postgres databases.

    This project does not have Alembic. `create_all()` creates new tables but
    does not add nullable columns to tables that already exist, so keep
    backwards-compatible schema additions here and make them idempotent.
    """
    inspector = inspect(engine)
    if "instruments" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("instruments")}
    columns = {
        "option_underlying": "VARCHAR(32)",
        "option_expiry": "DATE",
        "option_right": "VARCHAR(1)",
        "option_strike": "NUMERIC(20, 8)",
    }
    missing = [(name, sql_type) for name, sql_type in columns.items() if name not in existing]
    if not missing:
        return

    dialect = engine.dialect.name
    with engine.begin() as conn:
        for name, sql_type in missing:
            if dialect == "postgresql":
                conn.execute(
                    text(
                        f"ALTER TABLE instruments ADD COLUMN IF NOT EXISTS {name} {sql_type}"
                    )
                )
            else:
                conn.execute(text(f"ALTER TABLE instruments ADD COLUMN {name} {sql_type}"))
            log.info("Added instruments.%s compatibility column", name)


def _backfill_instrument_option_fields() -> None:
    """Populate persisted OCC fields for existing option instruments."""
    from .models import Instrument
    from ..analytics.options_parsing import enrich_option

    inspector = inspect(engine)
    if "instruments" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("instruments")}
    required = {
        "symbol",
        "description",
        "asset_class",
        "option_underlying",
        "option_expiry",
        "option_right",
        "option_strike",
    }
    if not required <= existing:
        return

    with Session(engine) as session:
        instruments = session.execute(
            select(Instrument).where(
                Instrument.asset_class == "OPT",
                or_(
                    Instrument.option_underlying.is_(None),
                    Instrument.option_expiry.is_(None),
                    Instrument.option_right.is_(None),
                    Instrument.option_strike.is_(None),
                ),
            )
        ).scalars().all()
        changed = 0
        for instr in instruments:
            contract = enrich_option(instr.symbol, instr.description)
            before = (
                instr.option_underlying,
                instr.option_expiry,
                instr.option_right,
                instr.option_strike,
            )
            if contract.underlying and not instr.option_underlying:
                instr.option_underlying = contract.underlying
            if contract.expiry and not instr.option_expiry:
                instr.option_expiry = contract.expiry
            if contract.right and not instr.option_right:
                instr.option_right = contract.right
            if contract.strike is not None and instr.option_strike is None:
                instr.option_strike = Decimal(str(contract.strike))
            after = (
                instr.option_underlying,
                instr.option_expiry,
                instr.option_right,
                instr.option_strike,
            )
            if after != before:
                changed += 1
        if changed:
            session.commit()
            log.info("Backfilled OCC fields for %s option instruments", changed)
