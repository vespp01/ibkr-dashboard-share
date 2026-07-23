"""Settings loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Flex Web Service
    ib_flex_token: str = Field(default="", description="Flex Web Service token")
    ib_flex_query_id: str = Field(default="", description="Activity Flex Query ID")
    ib_flex_query_id_tc: str = Field(default="", description="Trade Confirmation Flex Query ID")
    flex_user_agent: str = Field(default="ibkr-dashboard/0.1")
    flex_max_wait_seconds: int = Field(default=300)
    flex_poll_interval_seconds: int = Field(default=5)
    # Token expiry warning — the Flex Web Service token expires periodically
    # (the pull returns HTTP 604 once it does). Put the expiry date shown in
    # the portal here; the daily job pings Telegram when within
    # `token_expiry_warn_days` days of it (and once it's past). Empty disables.
    ib_flex_token_expiry: str = Field(
        default="", description="Flex token expiry date (YYYY-MM-DD); empty disables the warning"
    )
    token_expiry_warn_days: int = Field(
        default=7, description="Days before token expiry to start the Telegram warning"
    )

    # IB Gateway / TWS
    ibgw_host: str = "127.0.0.1"
    ibgw_port: int = 4002
    ibgw_client_id: int = 11
    ibgw_readonly: bool = True
    enable_live_overlay: bool = False

    # Database
    database_url: str = "sqlite:///./ibkr_backend.db"

    # Pipeline
    pipeline_cron: str = "30 6 * * *"
    # Trade Confirmation cron — hourly weekdays during market hours (UTC+2 →
    # 09:30–22:30 local covers Europe + US sessions; we just run all hours
    # 1-5 weekdays, idempotent so reruns are free).
    tc_cron: str = "15 * * * 1-5"
    base_currency: str = "CHF"
    # Alert if the daily pull hasn't succeeded within this many hours. The cron
    # runs every day, so >30h without a success is anomalous (covers a late
    # scheduler tick without false-alarming over a normal weekend run).
    pipeline_stale_after_hours: int = 30
    # How often the staleness watchdog logs a WARNING when stale (cron).
    staleness_watchdog_cron: str = "0 */6 * * *"

    # Logging — level for the ibkr_backend.* loggers in the server process.
    # INFO surfaces digest / token-expiry success lines in `docker logs`;
    # WARNING quiets them.
    log_level: str = Field(default="INFO", description="Level for ibkr_backend.* loggers")

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = (
        "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173"
    )

    # Telegram notifications — push a digest of newly-ingested trades +
    # dividends after each successful daily pull. Off unless BOTH are set
    # (no secrets in the repo; they live in .env). Resolve the chat id by
    # messaging the bot then GET /bot<token>/getUpdates.
    telegram_bot_token: str = Field(default="", description="Telegram bot token")
    telegram_chat_id: str = Field(default="", description="Telegram chat id to notify")

    # Price alerts — the dashboard polls IB Gateway for quotes and pings
    # Telegram when a rule crosses. Needs `enable_live_overlay=True` + IB
    # Gateway running. Rules live in `price_alerts.yaml`. Off if the file has
    # no alerts. Polls every 15 min on weekdays by default.
    price_alerts_file: Path = PROJECT_ROOT / "price_alerts.yaml"
    price_alert_cron: str = "*/15 * * * 1-5"

    # Watchlists
    watchlist_file: Path = PROJECT_ROOT / "watchlists.yaml"

    # Client Portal Gateway (CPG) — used to import IBKR-side watchlists
    cpg_base_url: str = "https://localhost:5000/v1/api"
    cpg_verify_tls: str = "false"  # "false" | "true" | path to PEM bundle

    @field_validator("watchlist_file", mode="before")
    @classmethod
    def _resolve_watchlist(cls, v):
        if v in (None, ""):
            return PROJECT_ROOT / "watchlists.yaml"
        p = Path(v)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @field_validator("price_alerts_file", mode="before")
    @classmethod
    def _resolve_price_alerts(cls, v):
        if v in (None, ""):
            return PROJECT_ROOT / "price_alerts.yaml"
        p = Path(v)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
