"""Informational review of the currency used to fund IBKR margin debt.

The comparison combines two independent facts:

* the current official IBKR Pro progressive borrowing rate; and
* a historical ECB replay of each currency against CHF.

It never forecasts FX, converts currency, or places an order. If either source
is missing or stale, the analysis fails closed and no Telegram notice is sent.
"""

from __future__ import annotations

import csv
import html
import io
import logging
import re
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Callable

import httpx

from ..analytics.margin import margin_summary
from ..config import Settings, get_settings
from ..db.models import NotificationState
from . import telegram

log = logging.getLogger(__name__)

IBKR_RATES_URL = "https://www.interactivebrokers.com/en/trading/margin-rates.php"
ECB_API_ROOT = "https://data-api.ecb.europa.eu/service/data/EXR"
_USER_AGENT = "ibkr-dashboard/0.1 margin-financing-review"


class FinancingUnavailable(RuntimeError):
    """Raised when an authoritative input is missing, stale, or malformed."""


class _TableCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def _tier_upper(text: str) -> float | None:
    if text.strip().startswith(">"):
        return None
    values = [
        float(value.replace(",", ""))
        for value in re.findall(r"\d[\d,]*(?:\.\d+)?", text)
    ]
    if not values:
        raise FinancingUnavailable(f"Unrecognised IBKR tier: {text!r}")
    return values[-1]


def parse_ibkr_margin_rates(
    document: str, currencies: set[str] | list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Extract IBKR Pro debit tiers from the official margin-rates page."""
    wanted = {currency.upper() for currency in currencies}
    parser = _TableCollector()
    parser.feed(document)
    rate_table = None
    for table in parser.tables:
        header = " ".join(cell for row in table[:2] for cell in row)
        if "Rate Charged" in header and "IBKR Pro" in header:
            rate_table = table
            break
    if rate_table is None:
        raise FinancingUnavailable("IBKR Pro rate table not found")

    result: dict[str, list[dict[str, Any]]] = {currency: [] for currency in wanted}
    current_currency: str | None = None
    for row in rate_table:
        if len(row) < 3:
            continue
        match = re.match(r"^([A-Z]{3})(?:\b|$)", row[0].strip())
        if match:
            current_currency = match.group(1)
        if current_currency not in wanted:
            continue
        rate_match = re.search(r"(-?\d+(?:\.\d+)?)\s*%", row[2])
        if not rate_match or (
            "≤" not in row[1] and not row[1].strip().startswith(">")
        ):
            continue
        result[current_currency].append(
            {
                "tier": row[1],
                "up_to": _tier_upper(row[1]),
                "apr_pct": float(rate_match.group(1)),
            }
        )

    missing = sorted(currency for currency, tiers in result.items() if not tiers)
    if missing:
        raise FinancingUnavailable(f"IBKR Pro rates missing for: {', '.join(missing)}")
    return result


def blended_apr(principal: float, tiers: list[dict[str, Any]]) -> float:
    """Return the progressive blended APR for a positive native-currency loan."""
    if principal <= 0:
        return 0.0
    remaining = float(principal)
    previous_upper = 0.0
    annual_interest = 0.0
    for tier in tiers:
        upper = tier.get("up_to")
        capacity = (
            remaining
            if upper is None
            else max(float(upper) - previous_upper, 0.0)
        )
        amount = min(remaining, capacity)
        annual_interest += amount * float(tier["apr_pct"]) / 100
        remaining -= amount
        if upper is not None:
            previous_upper = float(upper)
        if remaining <= 1e-9:
            break
    if remaining > 1e-9:
        raise FinancingUnavailable("IBKR rate tiers do not cover the requested loan")
    return annual_interest / principal * 100


def fetch_ibkr_margin_rates(
    currencies: set[str] | list[str],
    *,
    http_get: Callable[..., Any] = httpx.get,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch current official IBKR Pro debit rates."""
    response = http_get(
        IBKR_RATES_URL,
        headers={"User-Agent": _USER_AGENT},
        timeout=20.0,
    )
    response.raise_for_status()
    captured = now or datetime.now(UTC)
    return {
        "tiers": parse_ibkr_margin_rates(response.text, currencies),
        "source_url": IBKR_RATES_URL,
        "fetched_at": captured.astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }


def parse_ecb_chf_history(
    document: str,
    currencies: set[str] | list[str],
    *,
    today: date,
    history_days: int,
    max_age_days: int,
) -> dict[str, Any]:
    """Build current and historical CHF cross-rates from ECB EUR reference rates."""
    wanted = {currency.upper() for currency in currencies} | {"CHF"}
    required_series = wanted - {"EUR"}
    observations: dict[str, dict[date, float]] = {
        currency: {} for currency in required_series
    }
    for row in csv.DictReader(io.StringIO(document)):
        currency = (row.get("CURRENCY") or "").upper()
        if currency not in observations:
            continue
        try:
            day = date.fromisoformat(row["TIME_PERIOD"])
            value = float(row["OBS_VALUE"])
        except (KeyError, TypeError, ValueError):
            continue
        if value > 0:
            observations[currency][day] = value

    missing = sorted(
        currency for currency, series in observations.items() if not series
    )
    if missing:
        raise FinancingUnavailable(f"ECB series missing for: {', '.join(missing)}")
    common_dates = set.intersection(*(set(series) for series in observations.values()))
    if not common_dates:
        raise FinancingUnavailable("ECB series have no common observation date")
    latest = max(common_dates)
    if (today - latest).days > max_age_days:
        raise FinancingUnavailable(
            f"ECB rates stale: latest {latest.isoformat()}, max age {max_age_days} days"
        )
    target = latest - timedelta(days=history_days)
    historical = min(common_dates, key=lambda day: (abs((day - target).days), day))
    if abs((historical - target).days) > 14:
        raise FinancingUnavailable("ECB history does not cover the requested replay window")

    def crosses(day: date) -> dict[str, float]:
        chf_per_eur = observations["CHF"][day]
        result: dict[str, float] = {}
        for currency in wanted:
            if currency == "CHF":
                result[currency] = 1.0
            elif currency == "EUR":
                result[currency] = chf_per_eur
            else:
                result[currency] = chf_per_eur / observations[currency][day]
        return result

    current = crosses(latest)
    past = crosses(historical)
    changes = {
        currency: (current[currency] / past[currency] - 1) * 100
        for currency in wanted
    }
    return {
        "as_of": latest.isoformat(),
        "historical_date": historical.isoformat(),
        "current_chf_per_currency": current,
        "historical_chf_per_currency": past,
        "change_vs_chf_pct": changes,
        "source": "ECB euro foreign exchange reference rates",
    }


def fetch_ecb_chf_history(
    currencies: set[str] | list[str],
    *,
    today: date,
    history_days: int,
    max_age_days: int,
    http_get: Callable[..., Any] = httpx.get,
) -> dict[str, Any]:
    """Fetch enough official ECB daily observations for the CHF replay."""
    wanted = {currency.upper() for currency in currencies} | {"CHF"}
    series = "+".join(sorted(wanted - {"EUR"}))
    start = today - timedelta(days=history_days + 21)
    url = f"{ECB_API_ROOT}/D.{series}.EUR.SP00.A"
    response = http_get(
        url,
        params={
            "startPeriod": start.isoformat(),
            "endPeriod": today.isoformat(),
            "format": "csvdata",
            "detail": "dataonly",
        },
        headers={"User-Agent": _USER_AGENT, "Accept": "text/csv"},
        timeout=20.0,
    )
    response.raise_for_status()
    result = parse_ecb_chf_history(
        response.text,
        wanted,
        today=today,
        history_days=history_days,
        max_age_days=max_age_days,
    )
    result["source_url"] = url
    return result


def analyze_margin_financing(
    *,
    debts: list[dict[str, Any]],
    rate_tiers: dict[str, list[dict[str, Any]]],
    fx_history: dict[str, Any],
    candidates: list[str],
    min_debt_chf: float,
    min_saving_chf: float,
) -> dict[str, Any]:
    """Compare equivalent CHF debt under current rates and a historical FX replay."""
    current_fx = fx_history["current_chf_per_currency"]
    fx_changes = fx_history["change_vs_chf_pct"]
    debt_by_currency: list[dict[str, Any]] = []
    for debt in debts:
        amount = abs(float(debt.get("ending_cash") or 0))
        currency = str(debt.get("currency") or "").upper()
        if amount <= 0:
            continue
        if currency not in current_fx or currency not in rate_tiers:
            raise FinancingUnavailable(f"Missing rate inputs for debt currency {currency}")
        amount_chf = amount * float(current_fx[currency])
        apr = blended_apr(amount, rate_tiers[currency])
        replay_pct = (
            (1 + apr / 100) * (1 + float(fx_changes[currency]) / 100) - 1
        ) * 100
        debt_by_currency.append(
            {
                "currency": currency,
                "amount_native": amount,
                "amount_chf": amount_chf,
                "apr_pct": apr,
                "fx_change_pct": float(fx_changes[currency]),
                "replay_total_pct": replay_pct,
            }
        )
    debt_total_chf = sum(item["amount_chf"] for item in debt_by_currency)
    if debt_total_chf < min_debt_chf:
        return {
            "status": "no_debt",
            "debt_total_chf": debt_total_chf,
            "debt_by_currency": debt_by_currency,
        }

    comparisons: list[dict[str, Any]] = []
    for raw_currency in candidates:
        currency = raw_currency.upper()
        if currency not in current_fx or currency not in rate_tiers:
            raise FinancingUnavailable(f"Missing comparison inputs for {currency}")
        principal_native = debt_total_chf / float(current_fx[currency])
        apr = blended_apr(principal_native, rate_tiers[currency])
        interest_cost_chf = principal_native * apr / 100 * float(current_fx[currency])
        fx_change = float(fx_changes[currency])
        replay_pct = ((1 + apr / 100) * (1 + fx_change / 100) - 1) * 100
        comparisons.append(
            {
                "currency": currency,
                "principal_native": principal_native,
                "apr_pct": apr,
                "annual_interest_cost_chf": interest_cost_chf,
                "fx_change_pct": fx_change,
                "replay_total_pct": replay_pct,
                "replay_total_cost_chf": debt_total_chf * replay_pct / 100,
            }
        )

    interest_choice = min(
        comparisons,
        key=lambda item: (item["annual_interest_cost_chf"], item["currency"]),
    )
    replay_choice = min(
        comparisons,
        key=lambda item: (item["replay_total_cost_chf"], item["currency"]),
    )
    robust = (
        interest_choice["currency"]
        if interest_choice["currency"] == replay_choice["currency"]
        else None
    )

    current_interest = sum(
        item["amount_chf"] * item["apr_pct"] / 100 for item in debt_by_currency
    )
    current_replay = sum(
        item["amount_chf"] * item["replay_total_pct"] / 100 for item in debt_by_currency
    )
    interest_saving = None
    replay_saving = None
    decision = "no_clear_winner"
    if robust:
        choice = next(item for item in comparisons if item["currency"] == robust)
        interest_saving = current_interest - choice["annual_interest_cost_chf"]
        replay_saving = current_replay - choice["replay_total_cost_chf"]
        current_currencies = {item["currency"] for item in debt_by_currency}
        if current_currencies == {robust}:
            decision = "already_aligned"
        elif (
            interest_saving >= 0
            and replay_saving >= 0
            and max(interest_saving, replay_saving) >= min_saving_chf
        ):
            decision = "review_switch"
        else:
            decision = "small_difference"

    return {
        "status": "ready",
        "as_of": fx_history["as_of"],
        "historical_date": fx_history["historical_date"],
        "debt_total_chf": debt_total_chf,
        "debt_by_currency": debt_by_currency,
        "comparisons": comparisons,
        "interest_winner": interest_choice["currency"],
        "historical_replay_winner": replay_choice["currency"],
        "robust_candidate": robust,
        "decision": decision,
        "current_annual_interest_cost_chf": current_interest,
        "current_replay_cost_chf": current_replay,
        "estimated_interest_saving_chf": interest_saving,
        "estimated_replay_saving_chf": replay_saving,
    }


def build_margin_financing_analysis(
    session,
    *,
    settings: Settings | None = None,
    today: date | None = None,
    rates_fetcher: Callable[..., dict[str, Any]] = fetch_ibkr_margin_rates,
    fx_fetcher: Callable[..., dict[str, Any]] = fetch_ecb_chf_history,
) -> dict[str, Any]:
    """Build the full EOD-debt analysis, failing closed on source problems."""
    s = settings or get_settings()
    current_day = today or date.today()
    margin = margin_summary(session)
    snapshot_raw = margin.get("snapshot_date")
    try:
        snapshot_day = date.fromisoformat(snapshot_raw) if snapshot_raw else None
    except ValueError:
        snapshot_day = None
    snapshot_age = (current_day - snapshot_day).days if snapshot_day else None
    if (
        snapshot_age is None
        or snapshot_age < 0
        or snapshot_age > s.margin_fx_max_snapshot_age_days
    ):
        return {
            "status": "unavailable",
            "snapshot_date": snapshot_raw,
            "error": "Latest Flex cash snapshot is missing or stale",
        }
    debts = [item for item in margin.get("cash_by_currency", []) if item.get("is_debt")]
    if not debts:
        return {
            "status": "no_debt",
            "snapshot_date": margin.get("snapshot_date"),
            "debt_total_chf": 0.0,
            "debt_by_currency": [],
        }
    candidates = s.margin_fx_currency_list
    needed = set(candidates) | {str(item.get("currency") or "").upper() for item in debts}
    try:
        rate_data = rates_fetcher(needed)
        fx_data = fx_fetcher(
            needed,
            today=current_day,
            history_days=s.margin_fx_history_days,
            max_age_days=s.margin_fx_max_ecb_age_days,
        )
        result = analyze_margin_financing(
            debts=debts,
            rate_tiers=rate_data["tiers"],
            fx_history=fx_data,
            candidates=candidates,
            min_debt_chf=s.margin_fx_min_debt_chf,
            min_saving_chf=s.margin_fx_min_saving_chf,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Margin financing analysis unavailable: %s", exc)
        return {
            "status": "unavailable",
            "snapshot_date": margin.get("snapshot_date"),
            "error": str(exc),
        }
    result["snapshot_date"] = margin.get("snapshot_date")
    result["sources"] = {
        "ibkr_rates_url": rate_data.get("source_url", IBKR_RATES_URL),
        "ibkr_rates_fetched_at": rate_data.get("fetched_at"),
        "ecb_url": fx_data.get("source_url"),
        "ecb_as_of": fx_data.get("as_of"),
        "ecb_historical_date": fx_data.get("historical_date"),
    }
    return result


def _fmt_iso_date(raw: Any) -> str | None:
    """DD/MM/YYYY from an ISO date string; escaped raw text if unparseable;
    None when unset."""
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)).strftime("%d/%m/%Y")
    except ValueError:
        return html.escape(str(raw))


def _format_notice(
    analysis: dict[str, Any], *, previous_candidate: str | None = None
) -> str:
    """Verdict-first notice. `previous_candidate` (a currency, not 'NONE')
    marks the no-winner message as a transition: the prior signal lapsed."""
    comparisons = {item["currency"]: item for item in analysis["comparisons"]}
    robust = analysis.get("robust_candidate")
    decision = analysis.get("decision")
    debt_line = f"Deuda estimada: <b>{analysis['debt_total_chf']:,.0f} CHF</b>"

    replay_from = _fmt_iso_date(analysis.get("historical_date"))
    replay_to = _fmt_iso_date(analysis.get("as_of"))
    if replay_from and replay_to:
        replay_intro = f"Replay {replay_from} → {replay_to}: describe el pasado"
    else:
        replay_intro = "El replay describe el pasado"
    footer = (
        f"<i>{replay_intro}, no es una previsión; el FX puede revertir. "
        "Información orientativa: no convierte divisas ni ejecuta órdenes.</i>"
    )

    if robust and decision == "review_switch":
        choice = comparisons[robust]
        safe_robust = html.escape(robust)
        lines = [
            f"💱 <b>Margen por divisa: revisar cambio a {safe_robust}</b>",
            f"{debt_line} · {safe_robust} a {choice['apr_pct']:.3f}% APR (IBKR Pro)",
            (
                "Ahorro anual de interés estimado: "
                f"<b>{float(analysis['estimated_interest_saving_chf']):,.0f} CHF</b>"
            ),
            (
                "Diferencia en el replay histórico: "
                f"{float(analysis['estimated_replay_saving_chf']):,.0f} CHF"
            ),
            "Revisa spread y comisiones antes de cualquier cambio.",
        ]
    elif robust and decision == "already_aligned":
        lines = [
            (
                "💱 <b>Margen por divisa: deuda ya alineada con "
                f"{html.escape(robust)}</b>"
            ),
            debt_line,
        ]
    elif robust:
        lines = [
            (
                f"💱 <b>Margen por divisa: {html.escape(robust)} gana, "
                "sin ventaja material</b>"
            ),
            f"{debt_line} · la diferencia estimada es pequeña",
        ]
    else:
        interest = comparisons[analysis["interest_winner"]]
        replay = comparisons[analysis["historical_replay_winner"]]
        lines = ["💱 <b>Margen por divisa: sin ganador claro</b>"]
        if previous_candidate and previous_candidate != "NONE":
            lines.append(
                f"La señal anterior ({html.escape(previous_candidate)}) "
                "ya no se sostiene."
            )
        lines.append(
            f"Menor interés: {interest['currency']} {interest['apr_pct']:.3f}% · "
            f"mejor replay: {replay['currency']} "
            f"{replay['replay_total_pct']:+.2f}% — no coinciden"
        )
        lines.append(debt_line)
    lines.append(footer)
    return "\n".join(lines)


def check_margin_financing_notice(
    session,
    *,
    analysis_fn: Callable[[], dict[str, Any]] | None = None,
    settings: Settings | None = None,
    today: date | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Send a notice only when it is actionable (`review_switch`) or when the
    robust candidate flipped vs the last-sent one — a candidate→NONE flip sends
    a short transition notice; NONE→NONE weeks send nothing. At most one notice
    per ISO week per candidate (weekly dedup), retrying failed sends
    (at-least-once: the marker advances only after a successful send)."""
    s = settings or get_settings()
    if not s.margin_fx_enabled:
        return {"enabled": False, "sent": False, "would_send": False}
    analysis = (
        analysis_fn
        or (lambda: build_margin_financing_analysis(session, settings=s))
    )()
    if analysis.get("status") != "ready":
        return {**analysis, "sent": False, "would_send": False}
    current_day = today or date.today()
    iso = current_day.isocalendar()
    candidate = analysis.get("robust_candidate") or "NONE"
    signature = f"{iso.year}-W{iso.week:02d}|{candidate}"
    state = session.get(NotificationState, "margin_fx_last_notice")
    prev_value = state.value if state is not None and state.value else None
    # The stored signature already encodes the candidate ("2026-W31|USD").
    previous_candidate = (
        prev_value.rsplit("|", 1)[1] if prev_value and "|" in prev_value else "NONE"
    )
    actionable = analysis.get("decision") == "review_switch"
    changed = candidate != previous_candidate
    would_send = force or (
        (actionable or changed) and prev_value != signature
    )
    message = (
        _format_notice(analysis, previous_candidate=previous_candidate)
        if would_send
        else None
    )
    result = {
        **analysis,
        "sent": False,
        "would_send": would_send,
        "message": message,
    }
    if dry_run or not would_send:
        return result
    if not telegram.is_configured(s):
        return {**result, "configured": False}
    try:
        if not telegram.send_message(message or "", settings=s):
            return {**result, "configured": True}
    except Exception as exc:  # noqa: BLE001
        log.warning("Margin financing notice failed: %s", exc)
        return {**result, "configured": True, "error": str(exc)}
    if state is None:
        session.add(NotificationState(key="margin_fx_last_notice", value=signature))
    else:
        state.value = signature
    result["sent"] = True
    return result


__all__ = [
    "FinancingUnavailable",
    "analyze_margin_financing",
    "blended_apr",
    "build_margin_financing_analysis",
    "check_margin_financing_notice",
    "fetch_ecb_chf_history",
    "fetch_ibkr_margin_rates",
    "parse_ecb_chf_history",
    "parse_ibkr_margin_rates",
]
