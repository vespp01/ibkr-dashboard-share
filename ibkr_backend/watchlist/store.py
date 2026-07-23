"""Watchlist persistence — YAML on disk + DB mirror for fast queries."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import Watchlist


@dataclass(slots=True)
class WatchlistEntry:
    list_name: str
    symbol: str
    conid: int | None = None
    exchange: str | None = "SMART"
    currency: str | None = "USD"
    notes: str | None = None

    def to_db_row(self) -> dict[str, Any]:
        return asdict(self)


def load_yaml(path: Path | str | None = None) -> list[WatchlistEntry]:
    p = Path(path) if path else get_settings().watchlist_file
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out: list[WatchlistEntry] = []
    for list_name, entries in raw.items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict) or "symbol" not in e:
                continue
            out.append(
                WatchlistEntry(
                    list_name=list_name,
                    symbol=e["symbol"],
                    conid=e.get("conid"),
                    exchange=e.get("exchange") or "SMART",
                    currency=e.get("currency") or "USD",
                    notes=e.get("notes"),
                )
            )
    return out


def save_yaml(entries: list[WatchlistEntry], path: Path | str | None = None) -> Path:
    """Group entries by list_name and write the YAML file atomically."""
    p = Path(path) if path else get_settings().watchlist_file
    grouped: dict[str, list[dict]] = {}
    for e in entries:
        grouped.setdefault(e.list_name, []).append({
            k: v for k, v in {
                "symbol": e.symbol,
                "conid": e.conid,
                "exchange": e.exchange,
                "currency": e.currency,
                "notes": e.notes,
            }.items() if v is not None
        })
    body = yaml.safe_dump(grouped, sort_keys=False, allow_unicode=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(p)
    return p


def sync_yaml_to_db(session: Session, path: Path | str | None = None) -> int:
    """Replace the DB watchlist contents with the YAML file. Returns row count."""
    entries = load_yaml(path)
    session.execute(delete(Watchlist))
    if entries:
        session.add_all([Watchlist(**e.to_db_row()) for e in entries])
    session.flush()  # make rows visible to the same session before commit
    return len(entries)


def list_db_entries(
    session: Session, list_name: str | None = None
) -> list[WatchlistEntry]:
    q = select(Watchlist)
    if list_name:
        q = q.where(Watchlist.list_name == list_name)
    rows = session.execute(q).scalars().all()
    return [
        WatchlistEntry(
            list_name=r.list_name,
            symbol=r.symbol,
            conid=r.conid,
            exchange=r.exchange,
            currency=r.currency,
            notes=r.notes,
        )
        for r in rows
    ]
