"""Watchlist endpoints — list, sync from YAML, enrich with live quotes,
import from IBKR via Client Portal Gateway."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ...config import get_settings
from ...watchlist import enrich_with_live, import_from_ibkr, sync_yaml_to_db
from ...watchlist.store import list_db_entries
from ..deps import SessionDep

router = APIRouter()


@router.get("")
def list_entries(
    session: SessionDep,
    list_name: str | None = Query(None),
    enrich: bool = Query(False, description="Add live last/bid/ask via ib_async"),
):
    entries = list_db_entries(session, list_name=list_name)
    if not entries:
        # Fall back to YAML if the DB has not been synced yet.
        from ...watchlist.store import load_yaml

        entries = load_yaml()
        if list_name:
            entries = [e for e in entries if e.list_name == list_name]
    if enrich:
        return enrich_with_live(entries)
    return [
        {
            "list_name": e.list_name,
            "symbol": e.symbol,
            "conid": e.conid,
            "exchange": e.exchange,
            "currency": e.currency,
            "notes": e.notes,
        }
        for e in entries
    ]


@router.post("/sync")
def sync(session: SessionDep):
    """Re-sync the YAML watchlist file into the DB."""
    n = sync_yaml_to_db(session)
    session.commit()
    return {"synced": n}


@router.post("/import-from-ibkr", summary="Pull watchlists via Client Portal Gateway")
def import_from_ibkr_endpoint(
    merge_yaml: bool = Query(True, description="Also rewrite watchlists.yaml on disk"),
):
    """Requires the Client Portal Gateway to be running and a logged-in
    session at https://localhost:5000."""
    s = get_settings()
    v = (s.cpg_verify_tls or "false").strip().lower()
    verify = True if v in ("true", "1", "yes") else (
        False if v in ("false", "0", "no", "") else s.cpg_verify_tls
    )
    result = import_from_ibkr(
        cpg_base_url=s.cpg_base_url, verify_tls=verify, merge_yaml=merge_yaml,
    )
    if result.error:
        raise HTTPException(status_code=502, detail=result.to_dict())
    return result.to_dict()
