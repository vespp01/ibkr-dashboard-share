"""Client Portal Gateway (CPG) client — IBKR's official watchlist API.

The Flex Web Service has no watchlist export, and the TWS API socket has no
`getWatchlist` method. The only first-party programmatic surface for watchlists
is the Client Portal Web API:

    GET    /iserver/watchlists?SC=USER_WATCHLIST   list ids + names
    GET    /iserver/watchlist?id=<id>              rows of a single watchlist
    POST   /iserver/watchlist                      create
    DELETE /iserver/watchlist?id=<id>              delete

Operational notes (per IBKR docs):
  * The Gateway is a small Java program (`clientportal.gw`) you run locally on
    port 5000 with a self-signed TLS cert.
  * Auth is interactive: open https://localhost:5000 in a browser, sign in
    with your IBKR username + password + 2FA. Sessions last up to 24h and
    expire after ~6 minutes of idle unless `/tickle` is called every 5 minutes.
  * Sessions reset at midnight local time at the IBKR data centre
    (NY/Zug/HK).
  * Global rate limit: 10 requests/sec per username; 429 puts you in a
    10-minute "penalty box".
  * Brokerage `/iserver/*` endpoints are unavailable for ~15 minutes daily
    around 01:00 local time for maintenance.

This client is intentionally OPT-IN: it is invoked on demand (e.g. via the
`watchlist-import` CLI command). It does not run continuously, so we don't have
to keep `/tickle` alive long-term.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://localhost:5000/v1/api"


class CpgUnavailable(RuntimeError):
    """CP Gateway not reachable, not authenticated, or rate-limited."""


@dataclass(slots=True)
class IbkrWatchlist:
    """Header info returned by `/iserver/watchlists`."""

    id: str
    name: str
    type: str | None = None  # 'wl' / 'sl' / etc.
    readonly: bool | None = None


@dataclass(slots=True)
class IbkrWatchlistRow:
    """A single row inside a watchlist (`/iserver/watchlist?id=`)."""

    conid: int | None
    symbol: str | None = None
    name: str | None = None
    asset_class: str | None = None
    is_blank: bool = False


class CpgClient:
    """Synchronous httpx client over Client Portal Gateway.

    Parameters
    ----------
    base_url:
        Defaults to ``https://localhost:5000/v1/api``.
    verify:
        TLS verification. CPG ships with a self-signed cert by default;
        set False to skip verification on localhost (recommended for the
        default install) or pass a path to the PEM bundle.
    timeout:
        HTTP read timeout in seconds.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        verify: bool | str = False,
        timeout: float = 15.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self._base_url,
            verify=verify,
            timeout=timeout,
            headers={
                "User-Agent": "ibkr-dashboard/0.1 (+cpg-client)",
                "Accept": "application/json",
            },
        )

    def __enter__(self) -> "CpgClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------ session
    def authenticated(self) -> bool:
        """Returns True if the CPG session is currently authenticated.

        Uses `POST /iserver/auth/status` per IBKR docs (the GET form is also
        accepted on most builds).
        """
        try:
            r = self._http.post("/iserver/auth/status")
        except httpx.HTTPError as e:
            raise CpgUnavailable(
                f"CP Gateway not reachable at {self._base_url}: {e}"
            ) from e
        if r.status_code == 401:
            return False
        try:
            j = r.json()
        except ValueError:
            return False
        return bool(j.get("authenticated"))

    def tickle(self) -> dict[str, Any]:
        """Reset the idle timer. Call every ~5 min if you keep the client alive."""
        r = self._http.post("/tickle")
        return r.json() if r.status_code == 200 else {}

    def logout(self) -> None:
        try:
            self._http.post("/logout")
        except httpx.HTTPError:
            pass

    # ----------------------------------------------------------- helpers
    def _get_json(self, path: str, **params) -> Any:
        try:
            r = self._http.get(path, params=params)
        except httpx.HTTPError as e:
            raise CpgUnavailable(f"CPG GET {path} failed: {e}") from e
        if r.status_code == 401:
            raise CpgUnavailable(
                "CPG session is not authenticated. Open https://localhost:5000 "
                "in a browser and sign in with username + 2FA."
            )
        if r.status_code == 429:
            raise CpgUnavailable("CPG rate-limited (429). Wait 10 minutes.")
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise CpgUnavailable(f"CPG GET {path} -> {r.status_code}: {r.text[:200]}") from e
        try:
            return r.json()
        except ValueError as e:
            raise CpgUnavailable(f"CPG GET {path} returned non-JSON: {r.text[:200]}") from e

    # -------------------------------------------------------- watchlists
    def list_watchlists(self, sc: str = "USER_WATCHLIST") -> list[IbkrWatchlist]:
        """List all watchlists (saved by the user). Returns header info only."""
        body = self._get_json("/iserver/watchlists", SC=sc)
        # The CPG response shape is `{"data": {"user_lists": [...]}}` for SC=USER_WATCHLIST.
        # Older builds returned `{"data": [...]}` — accept both.
        data = body.get("data") if isinstance(body, dict) else body
        items: list[dict] = []
        if isinstance(data, dict):
            for key in ("user_lists", "scanner_list", "lists"):
                if isinstance(data.get(key), list):
                    items = data[key]
                    break
        elif isinstance(data, list):
            items = data
        out: list[IbkrWatchlist] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            wid = str(it.get("id") or it.get("ID") or "").strip()
            if not wid:
                continue
            out.append(
                IbkrWatchlist(
                    id=wid,
                    name=str(it.get("name") or "").strip() or wid,
                    type=it.get("type"),
                    readonly=it.get("readOnly"),
                )
            )
        return out

    def get_watchlist(self, watchlist_id: str) -> tuple[IbkrWatchlist, list[IbkrWatchlistRow]]:
        """Return the watchlist header + rows."""
        body = self._get_json("/iserver/watchlist", id=watchlist_id)
        instr = body.get("instruments") or body.get("rows") or []
        header = IbkrWatchlist(
            id=str(body.get("id") or watchlist_id),
            name=str(body.get("name") or watchlist_id),
            type=body.get("type"),
            readonly=body.get("readOnly"),
        )
        rows: list[IbkrWatchlistRow] = []
        for r in instr:
            if not isinstance(r, dict):
                continue
            # Blank rows are markers used in TWS to visually separate groups.
            if r.get("H") == "1" or r.get("blank"):
                rows.append(IbkrWatchlistRow(conid=None, is_blank=True))
                continue
            conid = r.get("C") or r.get("conid")
            try:
                conid_int = int(conid) if conid is not None else None
            except (TypeError, ValueError):
                conid_int = None
            rows.append(
                IbkrWatchlistRow(
                    conid=conid_int,
                    symbol=r.get("ticker") or r.get("symbol"),
                    name=r.get("name") or r.get("description"),
                    asset_class=r.get("assetClass"),
                )
            )
        return header, rows

    # -------------------------------------------------------- mutations
    def create_watchlist(
        self, *, watchlist_id: str, name: str, conids: list[int]
    ) -> dict[str, Any]:
        body = {
            "id": watchlist_id,
            "name": name,
            "rows": [{"C": str(c)} for c in conids],
        }
        r = self._http.post("/iserver/watchlist", json=body)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise CpgUnavailable(f"CPG POST watchlist failed: {e}") from e
        return r.json()

    def delete_watchlist(self, watchlist_id: str) -> None:
        r = self._http.delete("/iserver/watchlist", params={"id": watchlist_id})
        if r.status_code not in (200, 204):
            raise CpgUnavailable(
                f"CPG DELETE watchlist {watchlist_id} -> {r.status_code}: {r.text[:200]}"
            )
