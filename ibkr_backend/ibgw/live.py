"""Read-only ib_async wrapper for live overlay data.

Use this for:
  * intraday quote enrichment (Flex is end-of-day only)
  * contract details (sector / industry / primary exchange)
  * intraday P&L (`reqPnL` / `reqPnLSingle`)

Hardening rules per the deep research §1.6:
  * `readonly=True` so the socket is incapable of placing orders.
  * `host=127.0.0.1` only — never expose ports publicly.
  * Non-zero, non-default `clientId`.
  * If the Gateway is down, the call fails fast with `LiveUnavailable` so
    the dashboard degrades gracefully to the last EOD snapshot.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from ..config import get_settings

log = logging.getLogger(__name__)


class LiveUnavailable(RuntimeError):
    """Raised when IB Gateway / TWS is not reachable or live is disabled."""


@dataclass(slots=True)
class Quote:
    symbol: str
    conid: int | None
    last: float | None
    bid: float | None
    ask: float | None
    close: float | None
    currency: str | None


# IB Gateway refuses a second connection that reuses a live `clientId`. The
# scheduler's alert jobs and the API routes can overlap (the */3 margin poll, the
# */15 price poll, the after-pull margin message, a GET /portfolio/margin/live),
# so each caller names its purpose and gets its own id = base + offset (the
# 2026-10 review found them all sharing the single base id 11).
CLIENT_ID_OFFSETS: dict[str, int] = {
    "margin_alerts": 1,
    "margin_usage": 2,
    "price_alerts": 3,
    "portfolio": 4,
}


class LiveClient:
    """Synchronous facade over `ib_async.IB`.

    Usage:
        with LiveClient() as live:
            quotes = live.snapshot_quotes([("AAPL", "SMART", "USD"), ...])
    """

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        client_id: int | None = None,
        purpose: str | None = None,
        readonly: bool | None = None,
        timeout: float = 8.0,
    ) -> None:
        s = get_settings()
        if not s.enable_live_overlay:
            raise LiveUnavailable(
                "Live overlay disabled (ENABLE_LIVE_OVERLAY=false). "
                "Set the flag and ensure IB Gateway is running."
            )
        try:
            from ib_async import IB
        except ImportError as e:  # pragma: no cover
            raise LiveUnavailable(
                f"ib_async is not installed ({e}). "
                "Run: pip install -r requirements-backend.txt"
            ) from e
        self._IB = IB
        self._host = host or s.ibgw_host
        self._port = port or s.ibgw_port
        self._client_id = client_id or (
            s.ibgw_client_id + CLIENT_ID_OFFSETS.get(purpose or "", 0)
        )
        self._readonly = s.ibgw_readonly if readonly is None else readonly
        self._timeout = timeout
        self._ib: Any | None = None

    # ------------------------------------------------------------ context mgr
    def __enter__(self) -> "LiveClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()

    def connect(self) -> None:
        ib = self._IB()
        try:
            ib.connect(
                self._host,
                self._port,
                clientId=self._client_id,
                readonly=self._readonly,
                timeout=self._timeout,
            )
        except Exception as e:  # noqa: BLE001
            raise LiveUnavailable(
                f"Cannot connect to IB Gateway at {self._host}:{self._port}: {e}"
            ) from e
        if not ib.isConnected():
            raise LiveUnavailable(
                f"IB Gateway not connected after handshake at {self._host}:{self._port}"
            )
        # ib_async's RequestTimeout defaults to 0 (== wait forever); a half-alive
        # Gateway would then block a scheduler worker indefinitely. Bound every
        # blocking request so it raises asyncio.TimeoutError instead of hanging.
        ib.RequestTimeout = self._timeout
        self._ib = ib

    def disconnect(self) -> None:
        if self._ib is not None:
            try:
                self._ib.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._ib = None

    # ----------------------------------------------------------------- public
    def is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    def accounts(self) -> list[str]:
        self._require_connected()
        return list(self._ib.managedAccounts())

    def snapshot_quotes(
        self, contracts: list[tuple[str, str, str]] | list[dict]
    ) -> list[Quote]:
        """Return current snapshot quote (last/bid/ask/close) for each contract.

        Each entry is `(symbol, exchange, currency)` or a dict with same keys.
        """
        self._require_connected()
        from ib_async import Stock

        normalised: list[Stock] = []
        for c in contracts:
            if isinstance(c, dict):
                symbol = c["symbol"]
                exchange = c.get("exchange") or "SMART"
                currency = c.get("currency") or "USD"
                primary = c.get("primaryExchange")
                conid = c.get("conid")
            else:
                symbol, exchange, currency = c
                primary = None
                conid = None
            stock = Stock(symbol=symbol, exchange=exchange, currency=currency)
            if primary:
                stock.primaryExchange = primary
            if conid:
                stock.conId = conid
            normalised.append(stock)

        out: list[Quote] = []
        for stock in normalised:
            try:
                ticker = self._ib.reqMktData(
                    stock, "", snapshot=True, regulatorySnapshot=False
                )
                # Snapshot semantics: short wait then read.
                self._ib.sleep(2)
                out.append(
                    Quote(
                        symbol=stock.symbol,
                        conid=stock.conId or None,
                        last=_clean(getattr(ticker, "last", None)),
                        bid=_clean(getattr(ticker, "bid", None)),
                        ask=_clean(getattr(ticker, "ask", None)),
                        close=_clean(getattr(ticker, "close", None)),
                        currency=stock.currency,
                    )
                )
            except Exception as e:  # noqa: BLE001
                log.warning("Quote snapshot failed for %s: %s", stock.symbol, e)
                out.append(
                    Quote(
                        symbol=stock.symbol, conid=stock.conId or None,
                        last=None, bid=None, ask=None, close=None,
                        currency=stock.currency,
                    )
                )
        return out

    def contract_details(
        self, symbol: str, *, exchange: str = "SMART", currency: str = "USD"
    ) -> dict[str, Any] | None:
        self._require_connected()
        from ib_async import Stock

        details = self._ib.reqContractDetails(Stock(symbol, exchange, currency))
        if not details:
            return None
        d = details[0]
        c = d.contract
        return {
            "conid": c.conId,
            "symbol": c.symbol,
            "primary_exchange": c.primaryExchange,
            "exchange": c.exchange,
            "currency": c.currency,
            "long_name": d.longName,
            "industry": d.industry,
            "category": d.category,
            "subcategory": d.subcategory,
            "stock_type": d.stockType,
            "time_zone_id": d.timeZoneId,
        }

    def account_summary(self, account: str | None = None) -> dict[str, Any]:
        self._require_connected()
        if account is None:
            accs = self.accounts()
            account = accs[0] if accs else ""
        rows = self._ib.accountSummary(account=account)
        out: dict[str, Any] = {"account": account}
        for r in rows:
            out[r.tag] = {"value": r.value, "currency": r.currency}
        return out

    def pnl(self, account: str | None = None) -> dict[str, Any] | None:
        """Live unrealized + realized P&L for the account (intraday)."""
        self._require_connected()
        if account is None:
            accs = self.accounts()
            account = accs[0] if accs else None
        if not account:
            return None
        try:
            obj = self._ib.reqPnL(account=account)
            self._ib.sleep(1.5)
        except Exception as e:  # noqa: BLE001
            log.warning("reqPnL failed: %s", e)
            return None
        return {
            "account": account,
            "daily_pnl": _clean(getattr(obj, "dailyPnL", None)),
            "unrealized_pnl": _clean(getattr(obj, "unrealizedPnL", None)),
            "realized_pnl": _clean(getattr(obj, "realizedPnL", None)),
        }

    # ------------------------------------------------------------- internals
    def _require_connected(self) -> None:
        if not self.is_connected():
            raise LiveUnavailable("IB Gateway is not connected")


# --------------------------------------------------------------- module utils
def _clean(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # ib_async/IBKR uses NaN or large sentinels for "no data"
    if f != f or abs(f) > 1e18:
        return None
    return f


@contextmanager
def maybe_live() -> Iterator[LiveClient | None]:
    """Yield a CONNECTED `LiveClient`, or `None` if Gateway is unavailable.

    Use when live data is a nice-to-have:
        with maybe_live() as live:
            if live is not None:
                enrich(...)
    """
    client: LiveClient | None = None
    try:
        client = LiveClient()
        client.connect()
    except LiveUnavailable as e:
        log.info("Live overlay unavailable: %s", e)
        if client is not None:
            client.disconnect()
        yield None
        return
    try:
        yield client
    finally:
        client.disconnect()
