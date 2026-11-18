"""Settings loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
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
    # Nightly online backup of the SQLite DB (jobs/backup.py). Empty dir
    # disables the job; compose sets /backups, a bind mount of
    # ${IBKR_BACKUP_DIR:-${HOME}/.ibkr-backups} on the host.
    backup_dir: str = Field(default="", description="Nightly DB backup directory; empty disables")
    backup_cron: str = "45 3 * * *"
    backup_keep: int = Field(default=14, ge=1, description="Nightly backups to keep")
    # Alert (once per episode) when the newest nightly file is older than this.
    backup_stale_after_hours: int = Field(default=36, ge=1)
    # `flex_runs` grows by ~25 rows/day (hourly TC pull). Zero-insert TC successes
    # older than this carry no information and are pruned by a daily job.
    flex_runs_retention_days: int = Field(default=90, ge=7)
    flex_runs_retention_cron: str = "15 4 * * *"

    # OptimizadorFiscal — optional read-only scenario contract. Empty keeps the
    # dashboard disconnected and prevents accidental reads from sibling repos.
    tax_scenarios_file: Path | None = Field(
        default=None,
        description="Local tax_sale_scenarios.v1 JSON file; empty disables the integration",
    )
    # OptionsLab — read-only management signals (docker-compose mounts the host
    # ~/.local/state/optionslab at /optionslab:ro). A missing file only hides
    # the Opciones "OptionsLab" column values.
    optionslab_signals_path: Path = Field(
        default=Path("/optionslab/management_signals.v1.json"),
        description="Local optionslab.management_signals.v1 JSON file",
    )

    # Pipeline
    pipeline_cron: str = "30 6 * * *"
    # Trade Confirmation cron — hourly, every day. Idempotent so weekend reruns
    # are free no-ops, and running 7 days keeps the scheduler's *pull-attempt*
    # liveness stamp warm hourly, which the watchdog uses to catch a pull-job
    # dispatch wedge fast (see jobs/scheduler.py `_stamp_pull_attempt` +
    # deploy/watchdog/watchdog.sh). If you ever restrict this to weekdays,
    # raise WATCHDOG_PULL_ATTEMPT_STALE_SECONDS above the weekend gap.
    tc_cron: str = "15 * * * *"
    # Same-day retry slots (Zürich) for the daily Activity pull: ~11 % of the
    # 06:30 pulls fail on transient codes (1004/1009/1019/1021). Each slot runs
    # only if no scheduled pull has succeeded since the 06:30 slot (the same
    # predicate as the startup catch-up), so a good pull is never repeated.
    pipeline_retry_cron: str = "30 8,12 * * *"
    base_currency: str = "CHF"
    # Alert if the daily pull hasn't succeeded within this many hours. The cron
    # runs every day, so >30h without a success is anomalous (covers a late
    # scheduler tick without false-alarming over a normal weekend run).
    pipeline_stale_after_hours: int = 30
    # Data-age rule (2026-09-12): a pull can succeed while the statement stops
    # covering new days (the Activity query's portal period was "Year to Date" and
    # IBKR delivered it truncated — positions froze at 2026-08-17 for 26 days with
    # every pull in `success`). Alert when the newest positions_eod snapshot is
    # older than this many BUSINESS days. 3 = tolerates a holiday + the 06:30 lag.
    data_stale_after_business_days: int = 3
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
    # no alerts. Polls every 15 min on weekdays by default. APScheduler day-of-week is
    # 0=Mon (not standard cron), so day NAMES are mandatory in every cron default.
    price_alerts_file: Path = PROJECT_ROOT / "price_alerts.yaml"
    price_alert_cron: str = "*/15 * * * mon-fri"

    # Margin-risk alerts — IBKR account-summary values are the live authority.
    # The 10 % critical threshold mirrors IBKR's 110 % maintenance-margin
    # warning band. The wider 20/30 % bands are local, configurable policy.
    margin_alerts_enabled: bool = True
    margin_alert_cron: str = "*/3 * * * *"
    margin_cushion_warning_pct: float = 30.0
    margin_cushion_high_pct: float = 20.0
    margin_cushion_critical_pct: float = 10.0
    # Recovery hysteresis: the cushion must clear the warning band by this many
    # points before a RECUPERADO alert fires, so band-edge NAV noise on the
    # */3 live poll cannot flap WARNING↔RECUPERADO. Escalations are unaffected.
    margin_cushion_recovery_hysteresis_pct: float = Field(default=2.5, ge=0)

    # Margin funding-currency review. This is informational and never converts
    # cash. Official current IBKR Pro rates are combined with a historical ECB
    # replay against CHF. A weekly notice is sent only when cash debt exists.
    margin_fx_enabled: bool = True
    margin_fx_cron: str = "0 8 * * mon"
    margin_fx_currencies: str = "CHF,EUR,GBP,USD,JPY"
    margin_fx_history_days: int = Field(default=365, ge=30, le=1825)
    margin_fx_max_ecb_age_days: int = Field(default=7, ge=1, le=30)
    margin_fx_max_snapshot_age_days: int = Field(default=4, ge=1, le=30)
    margin_fx_min_debt_chf: float = Field(default=100.0, ge=0)
    margin_fx_min_saving_chf: float = Field(default=50.0, ge=0)

    @field_validator("price_alerts_file", mode="before")
    @classmethod
    def _resolve_price_alerts(cls, v):
        if v in (None, ""):
            return PROJECT_ROOT / "price_alerts.yaml"
        p = Path(v)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @field_validator("tax_scenarios_file", mode="before")
    @classmethod
    def _resolve_tax_scenarios_file(cls, v):
        if v in (None, ""):
            return None
        p = Path(v).expanduser()
        return p if p.is_absolute() else PROJECT_ROOT / p

    @model_validator(mode="after")
    def _validate_margin_bands(self):
        bands = (
            self.margin_cushion_warning_pct,
            self.margin_cushion_high_pct,
            self.margin_cushion_critical_pct,
        )
        if any(value < 0 or value > 100 for value in bands):
            raise ValueError("Margin cushion thresholds must be between 0 and 100")
        if not bands[0] > bands[1] > bands[2]:
            raise ValueError(
                "Margin cushion thresholds must satisfy warning > high > critical"
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def margin_fx_currency_list(self) -> list[str]:
        currencies = []
        for raw in self.margin_fx_currencies.split(","):
            currency = raw.strip().upper()
            if (
                len(currency) == 3
                and currency.isalpha()
                and currency not in currencies
            ):
                currencies.append(currency)
        if "CHF" not in currencies:
            currencies.insert(0, "CHF")
        return currencies


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
