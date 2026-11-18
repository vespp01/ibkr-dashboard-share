"""Versioned, privacy-bounded portfolio snapshot for Investmentlab.

The producer reads the latest Flex EOD snapshot from the local database. It
never exposes account identifiers, aliases, credentials, orders, or raw XML.
Consumers must treat any status other than ``ready`` as non-actionable context.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Instrument, NavDaily, PositionEod


SCHEMA_VERSION = "ibkr-dashboard.portfolio.v1"
MAX_READY_AGE_DAYS = 7
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

DecimalString = Annotated[str, Field(pattern=r"^-?[0-9]+(?:\.[0-9]+)?$")]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
SnapshotStatus = Literal["ready", "stale", "blocked"]


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PortfolioPosition(StrictContractModel):
    conid: int = Field(ge=1)
    symbol: Annotated[str, Field(min_length=1, max_length=64)]
    isin: Annotated[str, Field(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")] | None
    asset_class: Annotated[str, Field(min_length=1, max_length=16)]
    currency: Currency | None
    quantity: DecimalString
    market_value_base: DecimalString
    weight_pct: DecimalString | None


class PortfolioSummary(StrictContractModel):
    accounts_aggregated: int = Field(ge=0)
    position_count: int = Field(ge=0)
    nav_base: DecimalString | None
    gross_market_value_base: DecimalString
    net_market_value_base: DecimalString


class InvestmentlabPortfolioSnapshot(StrictContractModel):
    schema_version: Literal[SCHEMA_VERSION]
    producer: Literal["IBKR_Dashboard"]
    generated_at: datetime
    as_of: date | None
    status: SnapshotStatus
    days_stale: int | None = Field(default=None, ge=0)
    base_currency: Currency | None
    summary: PortfolioSummary
    positions: list[PortfolioPosition]
    warnings: list[str]

    @model_validator(mode="after")
    def validate_consistency(self) -> "InvestmentlabPortfolioSnapshot":
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must include a timezone")
        if self.summary.position_count != len(self.positions):
            raise ValueError("position_count must match positions")
        keys = {(p.conid, p.symbol, p.asset_class, p.currency) for p in self.positions}
        if len(keys) != len(self.positions):
            raise ValueError("aggregated position identifiers must be unique")
        if len(set(self.warnings)) != len(self.warnings):
            raise ValueError("warnings must be unique")
        if self.status in {"ready", "stale"}:
            if (
                self.as_of is None
                or self.days_stale is None
                or self.base_currency is None
                or self.summary.nav_base is None
                or not self.positions
                or any(position.weight_pct is None for position in self.positions)
            ):
                raise ValueError("usable snapshots require date, NAV, currency, and weights")
        elif (
            self.base_currency is not None
            or self.summary.nav_base is not None
            or self.positions
        ):
            raise ValueError("blocked snapshots cannot expose partial portfolio data")
        return self


def freshness_status(
    snapshot_date: date,
    today: date,
    max_ready_age_days: int = MAX_READY_AGE_DAYS,
) -> tuple[SnapshotStatus, int]:
    """Age rule shared by every EOD contract: future date blocks, old date is stale."""

    days_stale = max(0, (today - snapshot_date).days)
    if snapshot_date > today:
        return "blocked", days_stale
    if days_stale > max_ready_age_days:
        return "stale", days_stale
    return "ready", days_stale


def _decimal(value: object | None) -> Decimal:
    if value is None:
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _decimal_string(value: Decimal, *, places: int | None = None) -> str:
    if places is not None:
        quantum = Decimal(1).scaleb(-places)
        value = value.quantize(quantum)
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered if rendered not in {"", "-0"} else "0"


def _currency_or_none(value: str | None) -> str | None:
    normalized = value.strip().upper() if value else ""
    if len(normalized) == 3 and normalized.isascii() and normalized.isalpha():
        return normalized
    return None


def _isin_or_none(value: str | None) -> str | None:
    normalized = value.strip().upper() if value else ""
    return normalized if _ISIN_RE.fullmatch(normalized) else None


def _blocked_snapshot(
    *,
    generated_at: datetime,
    as_of: date | None,
    days_stale: int | None,
    accounts_aggregated: int,
    warnings: list[str],
) -> InvestmentlabPortfolioSnapshot:
    return InvestmentlabPortfolioSnapshot(
        schema_version=SCHEMA_VERSION,
        producer="IBKR_Dashboard",
        generated_at=generated_at,
        as_of=as_of,
        status="blocked",
        days_stale=days_stale,
        base_currency=None,
        summary=PortfolioSummary(
            accounts_aggregated=accounts_aggregated,
            position_count=0,
            nav_base=None,
            gross_market_value_base="0",
            net_market_value_base="0",
        ),
        positions=[],
        warnings=warnings,
    )


def build_investmentlab_portfolio_snapshot(
    session: Session,
    *,
    now: datetime | None = None,
    max_ready_age_days: int = MAX_READY_AGE_DAYS,
) -> InvestmentlabPortfolioSnapshot:
    """Build the latest account-identifier-free EOD portfolio contract."""

    generated_at = (now or datetime.now(UTC)).astimezone(UTC)
    if max_ready_age_days < 0:
        raise ValueError("max_ready_age_days must be non-negative")
    today = generated_at.date()
    snapshot_date = session.scalar(select(func.max(PositionEod.snapshot_date)))
    if snapshot_date is None:
        return _blocked_snapshot(
            generated_at=generated_at,
            as_of=None,
            days_stale=None,
            accounts_aggregated=0,
            warnings=["NO_POSITION_SNAPSHOT"],
        )

    position_rows = session.execute(
        select(PositionEod, Instrument.isin)
        .where(PositionEod.snapshot_date == snapshot_date)
        .join(Instrument, Instrument.conid == PositionEod.conid, isouter=True)
    ).all()
    account_ids = {position.account_id for position, _isin in position_rows}
    age_status, days_stale = freshness_status(snapshot_date, today, max_ready_age_days)
    warnings: list[str] = []
    if age_status == "blocked":
        warnings.append("FUTURE_SNAPSHOT_DATE")
    if len(account_ids) > 1:
        warnings.append("MULTIPLE_ACCOUNTS_AGGREGATED")

    nav_rows = session.scalars(
        select(NavDaily).where(NavDaily.snapshot_date == snapshot_date)
    ).all()
    account_ids.update(row.account_id for row in nav_rows)
    base_currencies = {
        currency
        for row in nav_rows
        if (currency := _currency_or_none(row.base_currency)) is not None
    }
    nav_base = sum((_decimal(row.nav_total) for row in nav_rows), Decimal("0"))
    if not nav_rows or nav_base <= 0:
        warnings.append("NAV_MISSING_FOR_POSITION_DATE")
    if len(base_currencies) != 1:
        warnings.append("BASE_CURRENCY_AMBIGUOUS")
    if not position_rows:
        warnings.append("NO_OPEN_POSITIONS")
    if any(position.conid < 1 or not position.symbol.strip() for position, _ in position_rows):
        warnings.append("INVALID_POSITION_IDENTITY")
    if any(position.fx_rate_to_base is None for position, _ in position_rows):
        # A NULL FX can only come from a foreign source (retired CSV loader);
        # never value it at 1.0 (B58).
        warnings.append("POSITION_FX_MISSING")

    if (
        age_status == "blocked"
        or not position_rows
        or not nav_rows
        or nav_base <= 0
        or len(base_currencies) != 1
        or "INVALID_POSITION_IDENTITY" in warnings
        or "POSITION_FX_MISSING" in warnings
    ):
        return _blocked_snapshot(
            generated_at=generated_at,
            as_of=snapshot_date,
            days_stale=days_stale,
            accounts_aggregated=len(account_ids),
            warnings=warnings,
        )

    aggregated: dict[tuple[int, str, str, str | None, str | None], dict[str, Decimal]] = {}
    metadata_normalized = False
    for position, isin in position_rows:
        currency = _currency_or_none(position.currency)
        clean_isin = _isin_or_none(isin)
        if (position.currency and currency is None) or (isin and clean_isin is None):
            metadata_normalized = True
        key = (
            position.conid,
            position.symbol.strip(),
            position.asset_class or "UNKNOWN",
            currency,
            clean_isin,
        )
        item = aggregated.setdefault(
            key,
            {"quantity": Decimal("0"), "market_value_base": Decimal("0")},
        )
        item["quantity"] += _decimal(position.quantity)
        item["market_value_base"] += _decimal(position.position_value) * _decimal(
            position.fx_rate_to_base if position.fx_rate_to_base is not None else 1
        )
    if metadata_normalized:
        warnings.append("POSITION_METADATA_NORMALIZED")

    positions: list[PortfolioPosition] = []
    for key in sorted(aggregated, key=lambda item: (item[2], item[1], item[0])):
        conid, symbol, asset_class, currency, isin = key
        item = aggregated[key]
        positions.append(
            PortfolioPosition(
                conid=conid,
                symbol=symbol,
                isin=isin,
                asset_class=asset_class,
                currency=currency,
                quantity=_decimal_string(item["quantity"], places=8),
                market_value_base=_decimal_string(
                    item["market_value_base"], places=8
                ),
                weight_pct=_decimal_string(
                    item["market_value_base"] / nav_base * Decimal("100"),
                    places=6,
                ),
            )
        )

    market_values = [_decimal(position.market_value_base) for position in positions]
    if age_status == "stale":
        status: SnapshotStatus = "stale"
        warnings.append("STALE_POSITION_SNAPSHOT")
    else:
        status = "ready"

    return InvestmentlabPortfolioSnapshot(
        schema_version=SCHEMA_VERSION,
        producer="IBKR_Dashboard",
        generated_at=generated_at,
        as_of=snapshot_date,
        status=status,
        days_stale=days_stale,
        base_currency=next(iter(base_currencies)),
        summary=PortfolioSummary(
            accounts_aggregated=len(account_ids),
            position_count=len(positions),
            nav_base=_decimal_string(nav_base, places=8),
            gross_market_value_base=_decimal_string(
                sum((abs(value) for value in market_values), Decimal("0")), places=8
            ),
            net_market_value_base=_decimal_string(
                sum(market_values, Decimal("0")), places=8
            ),
        ),
        positions=positions,
        warnings=warnings,
    )
