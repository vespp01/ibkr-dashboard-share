"""Cross-dialect idempotent INSERT helpers.

Both SQLite and Postgres support `INSERT … ON CONFLICT DO …`. SQLAlchemy 2.0
exposes `Insert.on_conflict_do_nothing` and `on_conflict_do_update` only on the
dialect-specific Insert class. We pick the right import at call time and fall
back to the generic SQLAlchemy `Insert` (which delegates to the dialect).
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def _dialect_insert(session: Session):
    eng: Engine = session.get_bind()
    name = eng.dialect.name
    if name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as ins
        return ins
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as ins
        return ins
    # Fallback: generic SQLAlchemy insert (no ON CONFLICT support).
    from sqlalchemy import insert as ins  # type: ignore
    return ins


def upsert_ignore(
    session: Session,
    model: type,
    rows: Iterable[dict[str, Any]],
    *,
    pk_cols: list[str] | None = None,
) -> tuple[int, int]:
    """INSERT rows skipping conflicts on primary-key (or specified `pk_cols`).

    Returns ``(inserted, skipped_or_unknown)``. SQLite/Postgres report
    `rowcount` == affected rows for ON CONFLICT DO NOTHING, so we can derive
    the inserted count exactly; on other dialects we approximate.
    """
    rows = list(rows)
    if not rows:
        return 0, 0
    ins = _dialect_insert(session)
    table = model.__table__
    if pk_cols is None:
        pk_cols = [c.name for c in inspect(model).primary_key]
    stmt = ins(table).values(rows)
    name = session.get_bind().dialect.name
    if name in ("sqlite", "postgresql"):
        stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)
        result = session.execute(stmt)
        inserted = result.rowcount or 0
        skipped = max(0, len(rows) - inserted)
        return inserted, skipped
    session.execute(stmt)
    return len(rows), 0


def upsert_replace(
    session: Session,
    model: type,
    rows: Iterable[dict[str, Any]],
    *,
    pk_cols: list[str] | None = None,
    update_cols: list[str] | None = None,
) -> int:
    """INSERT … ON CONFLICT DO UPDATE — replaces non-key columns on dup."""
    rows = list(rows)
    if not rows:
        return 0
    ins = _dialect_insert(session)
    table = model.__table__
    if pk_cols is None:
        pk_cols = [c.name for c in inspect(model).primary_key]
    if update_cols is None:
        update_cols = [c.name for c in table.columns if c.name not in pk_cols]
    stmt = ins(table).values(rows)
    name = session.get_bind().dialect.name
    if name == "sqlite":
        stmt = stmt.on_conflict_do_update(
            index_elements=pk_cols,
            set_={c: getattr(stmt.excluded, c) for c in update_cols},
        )
    elif name == "postgresql":
        stmt = stmt.on_conflict_do_update(
            index_elements=pk_cols,
            set_={c: getattr(stmt.excluded, c) for c in update_cols},
        )
    session.execute(stmt)
    return len(rows)
