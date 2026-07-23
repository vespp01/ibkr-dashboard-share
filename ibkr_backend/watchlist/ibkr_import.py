"""Pull watchlists out of IBKR via Client Portal Gateway and store them.

Usage from code:
    from ibkr_backend.watchlist import import_from_ibkr
    result = import_from_ibkr(merge_yaml=True)
    print(result.lists_imported, result.rows_imported)

The YAML namespace for imported lists is `ibkr:<list_name>` so they don't
collide with hand-managed lists. If `merge_yaml=True` is passed the YAML
file on disk is rewritten with the IBKR-sourced lists merged in.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..db import session_scope
from .cpg import CpgClient, CpgUnavailable
from .store import WatchlistEntry, load_yaml, save_yaml, sync_yaml_to_db

log = logging.getLogger(__name__)

NAMESPACE_PREFIX = "ibkr:"
_SAFE = re.compile(r"[^a-z0-9_]+")


def _slugify(name: str) -> str:
    s = _SAFE.sub("_", (name or "").strip().lower()).strip("_")
    return s or "unnamed"


@dataclass(slots=True)
class ImportResult:
    lists_imported: int = 0
    rows_imported: int = 0
    rows_skipped: int = 0
    list_names: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "lists_imported": self.lists_imported,
            "rows_imported": self.rows_imported,
            "rows_skipped": self.rows_skipped,
            "list_names": self.list_names,
            "error": self.error,
        }


def import_from_ibkr(
    *,
    cpg_base_url: str | None = None,
    verify_tls: bool | str = False,
    merge_yaml: bool = False,
    overwrite: bool = True,
    session: Session | None = None,
) -> ImportResult:
    """Fetch all user watchlists from CPG and persist them.

    Parameters
    ----------
    merge_yaml:
        Also write the imported lists back to the YAML file on disk so they
        stay version-controlled.
    overwrite:
        If a list with the same `ibkr:<id>` namespace already exists in YAML,
        replace its rows. Set False to merge by union of symbols.
    """
    result = ImportResult()
    try:
        client_kwargs = {"verify": verify_tls}
        if cpg_base_url:
            client_kwargs["base_url"] = cpg_base_url
        with CpgClient(**client_kwargs) as cpg:
            if not cpg.authenticated():
                raise CpgUnavailable(
                    "CPG session is not authenticated. Open "
                    "https://localhost:5000 in a browser, sign in with your "
                    "IBKR username + password + 2FA, then retry."
                )
            headers = cpg.list_watchlists()
            log.info("CPG returned %d watchlists", len(headers))
            collected: dict[str, list[WatchlistEntry]] = {}
            for h in headers:
                _, rows = cpg.get_watchlist(h.id)
                list_name = f"{NAMESPACE_PREFIX}{_slugify(h.name)}"
                entries: list[WatchlistEntry] = []
                for r in rows:
                    if r.is_blank or r.conid is None:
                        result.rows_skipped += 1
                        continue
                    entries.append(WatchlistEntry(
                        list_name=list_name,
                        symbol=r.symbol or f"conid:{r.conid}",
                        conid=r.conid,
                        notes=h.id,  # keep the IBKR-internal id for round-trip
                    ))
                if entries:
                    collected[list_name] = entries
                    result.list_names.append(list_name)
                    result.rows_imported += len(entries)
            result.lists_imported = len(collected)
    except CpgUnavailable as e:
        result.error = str(e)
        return result

    # Persist
    if merge_yaml:
        existing = load_yaml()
        if overwrite:
            kept = [e for e in existing if not e.list_name.startswith(NAMESPACE_PREFIX)
                    or e.list_name not in collected]
        else:
            kept = list(existing)
        all_entries = kept + [e for lst in collected.values() for e in lst]
        save_yaml(all_entries)
        # Then mirror YAML → DB (user-managed + ibkr-imported, single source).
        owns_session = session is None
        if owns_session:
            with session_scope() as s:
                sync_yaml_to_db(s)
        else:
            sync_yaml_to_db(session)
    else:
        # DB-only import: insert/replace just the ibkr:* namespace.
        from sqlalchemy import delete
        from ..db.models import Watchlist

        owns_session = session is None
        ctx = session_scope() if owns_session else _NullContext(session)
        with ctx as s:
            # remove any existing rows from these lists, then re-insert
            for lname in collected:
                s.execute(delete(Watchlist).where(Watchlist.list_name == lname))
            for lname, entries in collected.items():
                s.add_all([Watchlist(**e.to_db_row()) for e in entries])
    return result


class _NullContext:
    """Context manager wrapper for an externally-owned Session."""

    def __init__(self, session: Session):
        self._s = session

    def __enter__(self) -> Session:
        return self._s

    def __exit__(self, *exc) -> None:
        pass
