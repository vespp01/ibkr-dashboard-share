"""Convenience CLI:

    python -m ibkr_backend.cli init-db
    python -m ibkr_backend.cli pull              # uses the saved query's own period (no --period: 604 on fixed-period queries)
    python -m ibkr_backend.cli bootstrap --years 4
    python -m ibkr_backend.cli load-xml ./Extractos/file.xml
    python -m ibkr_backend.cli runs
    python -m ibkr_backend.cli serve
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()
log = logging.getLogger("ibkr_backend.cli")


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@app.command("init-db")
def init_db_cmd(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Create the database schema (idempotent)."""
    _setup_logging(verbose)
    from .db import init_db

    init_db()
    console.print("[green]✓[/green] Database initialised at ", get_settings().database_url)


@app.command("pull")
def pull_cmd(
    period: str = typer.Option(
        "",
        help=(
            "Flex period override (e.g. LastBusinessDay, YTD). Default empty = "
            "use the saved query's own period. Most IBKR queries reject period "
            "overrides — leave empty unless you know yours accepts them."
        ),
    ),
    from_date: str | None = typer.Option(None, "--from", help="yyyymmdd"),
    to_date: str | None = typer.Option(None, "--to", help="yyyymmdd"),
    note: str = typer.Option("manual"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Run the Flex Web Service pipeline once."""
    _setup_logging(verbose)
    from .db import init_db
    from .loaders.pipeline import run_full_pipeline

    init_db()
    result = run_full_pipeline(
        period=period or None, from_date=from_date, to_date=to_date, note=note
    )
    table = Table(title=f"Flex pull — ref {result.reference_code}")
    table.add_column("section")
    table.add_column("inserted", justify="right")
    table.add_column("skipped", justify="right")
    table.add_column("replaced", justify="right")
    for s in result.sections:
        table.add_row(s.section, str(s.inserted), str(s.skipped), str(s.replaced))
    console.print(table)
    console.print(
        f"[green]✓[/green] from={result.from_date} to={result.to_date} "
        f"inserted={result.total_inserted} skipped={result.total_skipped}"
    )


@app.command("pull-tc")
def pull_tc_cmd(
    from_date: str | None = typer.Option(None, "--from", help="yyyymmdd"),
    to_date: str | None = typer.Option(None, "--to", help="yyyymmdd"),
    note: str = typer.Option("manual-tc"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Pull the Trade Confirmation Flex Query (per-fill execution detail).

    Uses ``IB_FLEX_QUERY_ID_TC``. Defaults to the query's saved period
    (``Today``); pass --from/--to to backfill any window inside the 45-day
    TC rolling ceiling.
    """
    _setup_logging(verbose)
    from .db import init_db
    from .loaders.pipeline import run_tc_pipeline

    init_db()
    result = run_tc_pipeline(from_date=from_date, to_date=to_date, note=note)
    table = Table(title=f"TC pull — ref {result.reference_code}")
    table.add_column("section")
    table.add_column("inserted", justify="right")
    table.add_column("skipped", justify="right")
    table.add_column("replaced", justify="right")
    for s in result.sections:
        # Skip rows that wrote nothing — TC payloads are sparse.
        if s.inserted == 0 and s.skipped == 0 and s.replaced == 0:
            continue
        table.add_row(s.section, str(s.inserted), str(s.skipped), str(s.replaced))
    console.print(table)
    console.print(
        f"[green]✓[/green] from={result.from_date} to={result.to_date} "
        f"inserted={result.total_inserted} skipped={result.total_skipped}"
    )


@app.command("bootstrap")
def bootstrap_cmd(
    years: int = typer.Option(4, help="History depth in years"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Backfill multi-year history in 365-day windows."""
    _setup_logging(verbose)
    from .db import init_db
    from .loaders.pipeline import bootstrap_history

    init_db()
    runs = bootstrap_history(years=years)
    for r in runs:
        console.print(
            f"window {r.from_date}→{r.to_date} ref={r.reference_code} "
            f"inserted={r.total_inserted} skipped={r.total_skipped}"
        )
    console.print(f"[green]✓[/green] {len(runs)} bootstrap windows completed")


@app.command("bootstrap-if-empty")
def bootstrap_if_empty_cmd(
    years: int = typer.Option(4, help="History depth in years"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Backfill multi-year history only if the dashboard database is empty."""
    _setup_logging(verbose)
    from .db import init_db
    from .loaders.pipeline import bootstrap_history_if_empty

    init_db()
    did_run, runs = bootstrap_history_if_empty(years=years)
    if not did_run:
        console.print("[green]ok[/green] Dashboard database already has data; bootstrap skipped.")
        return
    for r in runs:
        console.print(
            f"window {r.from_date}->{r.to_date} ref={r.reference_code} "
            f"inserted={r.total_inserted} skipped={r.total_skipped}"
        )
    console.print(f"[green]ok[/green] {len(runs)} bootstrap windows completed")


@app.command("load-xml")
def load_xml_cmd(
    path: Path,
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Load a saved Flex XML file from disk (testing / offline backups)."""
    _setup_logging(verbose)
    from .db import init_db
    from .loaders.pipeline import load_xml_file

    init_db()
    results = load_xml_file(str(path))
    table = Table(title=f"Loaded {path.name}")
    table.add_column("section")
    table.add_column("inserted", justify="right")
    table.add_column("skipped", justify="right")
    table.add_column("replaced", justify="right")
    for s in results:
        table.add_row(s.section, str(s.inserted), str(s.skipped), str(s.replaced))
    console.print(table)


@app.command("runs")
def runs_cmd(limit: int = 20):
    """List recent Flex pipeline runs."""
    from sqlalchemy import select

    from .db import session_scope
    from .db.models import FlexRun

    table = Table(title="Recent Flex runs")
    for col in ("id", "fetched_at", "ref", "from", "to", "ins", "dup", "status", "note"):
        table.add_column(col)
    with session_scope() as s:
        rows = s.execute(
            select(FlexRun).order_by(FlexRun.fetched_at.desc()).limit(limit)
        ).scalars().all()
    for r in rows:
        table.add_row(
            str(r.run_id), str(r.fetched_at), r.reference_code or "—",
            str(r.from_date or "—"), str(r.to_date or "—"),
            str(r.rows_inserted), str(r.rows_skipped_dup),
            r.status or "—", r.note or "—",
        )
    console.print(table)


@app.command("normalize-asset-class")
def normalize_asset_class_cmd(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show counts of rows that would be updated, but do not write."
    ),
):
    """One-shot: collapse legacy asset_class labels into canonical short codes.

    IBKR Flex historically emitted "Stocks" / "Equity and Index Options" / "Funds"
    in some sections while modern parsing writes "STK" / "OPT" / "FUND". Mixing
    the two breaks filters like `.where(asset_class == "OPT")`. This command
    rewrites the legacy values across `trades`, `position_eod`, `instruments`,
    and `cash_transactions`. Idempotent — re-run is a no-op once normalised.
    """
    from sqlalchemy import select, update

    from .db import session_scope
    from .db.models import (
        CashTransaction,
        Instrument,
        PositionEod,
        Trade,
    )
    from .loaders._asset_class import canon

    tables = [
        ("trades", Trade),
        ("position_eod", PositionEod),
        ("instruments", Instrument),
        ("cash_transactions", CashTransaction),
    ]
    total_changed = 0
    with session_scope() as s:
        for name, model in tables:
            rows = s.execute(select(model.asset_class).distinct()).scalars().all()
            mapping = {raw: canon(raw) for raw in rows if raw and canon(raw) != raw}
            for raw, target in mapping.items():
                count = s.execute(
                    select(model).where(model.asset_class == raw)
                ).scalars().all()
                n = len(count)
                console.print(
                    f"  [cyan]{name}[/cyan]: {n} rows '{raw}' → '{target}'"
                )
                total_changed += n
                if not dry_run:
                    s.execute(
                        update(model)
                        .where(model.asset_class == raw)
                        .values(asset_class=target)
                    )
    if dry_run:
        console.print(f"[yellow]dry-run[/yellow] · {total_changed} rows would be rewritten")
    else:
        console.print(f"[green]✓[/green] {total_changed} rows rewritten")


@app.command("reconcile-cashflows")
def reconcile_cashflows_cmd(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Re-derive external_cashflow for all nav_daily rows from full cash_transactions history.

    Zeroes out residue entries (dates that had a cashflow in an old statement but no
    longer have matching transactions) and corrects any aggregate that drifted from
    the canonical sum. Use this after discovering spurious cashflow values in nav_daily.
    """
    _setup_logging(verbose)
    from .db import init_db, session_scope
    from .loaders.pipeline import reconcile_external_cashflows

    init_db()
    with session_scope() as s:
        changed = reconcile_external_cashflows(s)
    console.print(f"[green]✓[/green] {changed} nav_daily rows reconciled")


@app.command("backfill-cin-columns")
def backfill_cin_columns_cmd(
    apply: bool = typer.Option(False, "--apply", help="Write the values. Default is a dry-run."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Fill change_in_nav_daily.asset_transfers / sales_tax from each row's raw_xml.

    Idempotent. Dry-run by default. Run after deploying the new columns; the
    XIRR, all-time return, waterfall and TWR reconciliation read them.
    """
    _setup_logging(verbose)
    from .db import init_db, session_scope
    from .loaders.cin_backfill import backfill_cin_columns

    init_db()
    with session_scope() as s:
        report = backfill_cin_columns(s, apply=apply)
    for k, v in report.items():
        console.print(f"{k}: {v}")
    console.print(
        "[green]✓ applied[/green]" if apply
        else "[yellow]dry-run[/yellow] · re-run with --apply (back up first)"
    )


@app.command("drop-csv-flow-rows")
def drop_csv_flow_rows_cmd(
    apply: bool = typer.Option(False, "--apply", help="Delete the rows. Default is a dry-run."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Delete the Plan-B CSV Deposit/Withdrawal cash rows (csv_ ids).

    Guarded: only deletes when Σ ChangeInNAV depositsWithdrawals / Σ cash D/W is
    1.000 ± 0.002. Run `backfill-cin-columns --apply` first. Dry-run by default.
    """
    _setup_logging(verbose)
    from .db import init_db, session_scope
    from .loaders.cin_backfill import drop_csv_flow_rows

    init_db()
    with session_scope() as s:
        report = drop_csv_flow_rows(s, apply=apply)
    for k, v in report.items():
        console.print(f"{k}: {v}")
    if apply and report["csv_rows"] and not report["ratio_ok"]:
        console.print("[red]refused[/red] · ratio outside tolerance, nothing deleted")
        raise typer.Exit(1)
    console.print(
        "[green]✓ applied[/green]" if apply
        else "[yellow]dry-run[/yellow] · re-run with --apply (back up first)"
    )


@app.command("drop-navless-nav-rows")
def drop_navless_nav_rows_cmd(
    apply: bool = typer.Option(False, "--apply", help="Delete the rows. Default is a dry-run."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Delete nav_daily rows without a NAV (cash-flow-only residue, e.g. 2023-11-18)."""
    _setup_logging(verbose)
    from .db import init_db, session_scope
    from .loaders.cin_backfill import drop_navless_rows

    init_db()
    with session_scope() as s:
        report = drop_navless_rows(s, apply=apply)
    for k, v in report.items():
        console.print(f"{k}: {v}")
    console.print(
        "[green]✓ applied[/green]" if apply
        else "[yellow]dry-run[/yellow] · re-run with --apply (back up first)"
    )


@app.command("dedup-csv-imports")
def dedup_csv_imports_cmd(
    apply: bool = typer.Option(
        False, "--apply",
        help="Actually delete the duplicates. Default is a dry-run report.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Remove rows duplicated by overlapping CSV (Plan B) + Flex imports.

    Dry-run by default. A `csv_*` row is deleted only when a Flex row shares
    its economic key; csv orphans and genuine repeated Flex fills are kept.
    Back up the DB before `--apply`.
    """
    _setup_logging(verbose)
    from .db import init_db, session_scope
    from .loaders.dedup import dedup_csv_imports

    init_db()
    with session_scope() as s:
        report = dedup_csv_imports(s, apply=apply)
    table = Table(title=f"CSV-import dedup ({'APPLIED' if apply else 'dry-run'})")
    table.add_column("table")
    table.add_column("total", justify="right")
    table.add_column("removable csv", justify="right")
    table.add_column("orphan csv kept", justify="right")
    table.add_column("genuine groups", justify="right")
    for name, r in report.items():
        if name.startswith("_"):
            continue
        table.add_row(
            name, str(r["total"]), str(r["removable_csv"]),
            str(r["orphan_csv_kept"]), str(r["genuine_dupe_groups"]),
        )
    console.print(table)
    if not apply:
        console.print("[yellow]Dry-run.[/yellow] Re-run with [bold]--apply[/bold] to delete (back up first).")
    else:
        console.print("[green]✓[/green] duplicates removed")


@app.command("purge-csv-residue")
def purge_csv_residue_cmd(
    apply: bool = typer.Option(
        False, "--apply",
        help="Actually delete. Default is a dry-run report.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """One-time cleanup of everything the retired CSV loader left behind.

    Deletes CSV trade/cash/fee twins, CSV subtotal fee rows, CSV corporate
    actions and Flex SUMMARY duplicates, CSV positions/NAV/BASE cash rows and
    the negative-conid instruments they leave unreferenced. Keeps the CSV
    deposit/withdrawal rows (no Flex twin) and any CSV row it cannot pair.
    Refuses with exit 1 when Flex-only realized P&L does not match the FIFO
    summary per year. Dry-run by default; back up the DB before `--apply`.
    """
    _setup_logging(verbose)
    from .db import init_db, session_scope
    from .loaders.dedup import PurgeRefused, purge_csv_residue

    init_db()
    try:
        with session_scope() as s:
            report = purge_csv_residue(s, apply=apply)
    except PurgeRefused as exc:
        console.print(f"[red]REFUSED[/red] {exc}")
        raise typer.Exit(1)
    table = Table(title=f"CSV residue purge ({'APPLIED' if apply else 'dry-run'})")
    table.add_column("table")
    table.add_column("rows", justify="right")
    for name, n in report["counts"].items():
        table.add_row(name, str(n))
    console.print(table)
    for name, n in report["kept"].items():
        console.print(f"kept {name}: {n}")
    recon = report["checks"]["realized_reconciliation"]
    for year, r in recon["years"].items():
        console.print(
            f"reconciliation {year}: flex trades {r['flex_trades_realized']} vs FIFO "
            f"{r['fifo_summary_realized']} ratio={r['ratio']} {'ok' if r['ok'] else 'MISMATCH'}"
        )
    for line in report["review"]:
        console.print(f"[yellow]review[/yellow] {line}")
    if not apply:
        console.print("[yellow]Dry-run.[/yellow] Re-run with [bold]--apply[/bold] to delete (back up first).")
    else:
        console.print("[green]ok[/green] CSV residue purged")


@app.command("rekey-fees")
def rekey_fees_cmd(
    apply: bool = typer.Option(
        False, "--apply",
        help="Actually rewrite the ids. Default is a dry-run report.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Move stored Flex fee rows to the current fee_id formula (B35).

    Run once after deploying the new formula and BEFORE the next pull, or the
    pull re-inserts the fees of its window under new ids. Offline and
    idempotent; dry-run by default. Back up the DB before `--apply`.
    """
    _setup_logging(verbose)
    from .db import init_db, session_scope
    from .loaders.dedup import rekey_fee_ids

    init_db()
    with session_scope() as s:
        report = rekey_fee_ids(s, apply=apply)
    console.print(report)
    if not apply:
        console.print("[yellow]Dry-run.[/yellow] Re-run with [bold]--apply[/bold] to rewrite (back up first).")


@app.command("prune-orphan-accounts")
def prune_orphan_accounts_cmd(
    apply: bool = typer.Option(
        False, "--apply",
        help="Actually delete the orphans. Default is a dry-run report.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Delete accounts with no rows in any fact table (test/bootstrap residue).

    Dry-run by default. An account is removed only when no trade, cash, NAV,
    position, accrual — or any account-scoped row — references it. Back up the
    DB before `--apply`.
    """
    _setup_logging(verbose)
    from .db import init_db, session_scope
    from .loaders.hygiene import prune_orphan_accounts

    init_db()
    with session_scope() as s:
        report = prune_orphan_accounts(s, apply=apply)
    console.print(
        f"accounts: {report['total']} total · "
        f"kept {report['kept']} · orphans {report['orphans']}"
    )
    if apply:
        console.print(f"[green]✓[/green] deleted {len(report['deleted'])}: {report['deleted']}")
    elif report["orphans"]:
        console.print("[yellow]Dry-run.[/yellow] Re-run with [bold]--apply[/bold] to delete (back up first).")
    else:
        console.print("[green]✓[/green] no orphans to prune")


@app.command("price-alerts")
def price_alerts_cmd():
    """List the configured price-alert rules and their armed/triggered state."""
    _setup_logging()
    from .db import init_db, session_scope
    from .db.models import PriceAlertState
    from .notify.price_alerts import load_rules

    init_db()
    rules = load_rules(get_settings().price_alerts_file)
    if not rules:
        console.print("[yellow]Sin alertas[/yellow] en price_alerts.yaml.")
        return
    table = Table(title="Alertas de precio")
    table.add_column("símbolo")
    table.add_column("regla")
    table.add_column("estado")
    table.add_column("último precio", justify="right")
    with session_scope() as s:
        for r in rules:
            st = s.get(PriceAlertState, r.key)
            estado = "🔔 disparada" if (st and st.triggered) else "armada"
            last = f"{st.last_price:.2f}" if (st and st.last_price is not None) else "—"
            table.add_row(r.symbol, f"{r.op} {r.price:g} {r.currency}", estado, last)
    console.print(table)


@app.command("check-price-alerts")
def check_price_alerts_cmd(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Poll quotes once and send Telegram for any newly-crossed alert (needs IB Gateway)."""
    _setup_logging(verbose)
    from .db import init_db, session_scope
    from .notify.price_alerts import check_price_alerts

    init_db()
    with session_scope() as s:
        summary = check_price_alerts(s)
    console.print(summary)


@app.command("margin-alert")
def margin_alert_cmd(
    source: str = typer.Option(
        "live",
        "--source",
        help="Source: live (IBKR/TWS) or eod (latest Flex Reg-T estimate).",
    ),
    test: bool = typer.Option(
        False,
        "--test",
        help="Preview without sending Telegram or changing alert state.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Check margin risk once and optionally send a transition alert."""
    _setup_logging(verbose)
    from .analytics.margin import margin_summary
    from .db import init_db, session_scope
    from .notify.margin_alerts import build_eod_snapshot, check_margin_alert

    source = source.strip().lower()
    if source not in {"live", "eod"}:
        console.print("[red]✗[/red] --source must be live or eod")
        raise typer.Exit(2)
    init_db()
    with session_scope() as s:
        snapshot_fn = None
        if source == "eod":
            snapshot = build_eod_snapshot(margin_summary(s))
            snapshot_fn = lambda: snapshot
        summary = check_margin_alert(s, snapshot_fn=snapshot_fn, dry_run=test)
    console.print_json(data=summary)


@app.command("margin-financing")
def margin_financing_cmd(
    send: bool = typer.Option(
        False,
        "--send",
        help="Send the weekly Telegram notice if it is due. Default only prints analysis.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="With --send, ignore the weekly watermark.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Compare current IBKR loan rates and a historical ECB CHF replay."""
    _setup_logging(verbose)
    from .db import init_db, session_scope
    from .notify.margin_financing import (
        build_margin_financing_analysis,
        check_margin_financing_notice,
    )

    init_db()
    with session_scope() as s:
        if send:
            summary = check_margin_financing_notice(s, force=force)
        else:
            summary = build_margin_financing_analysis(s)
    console.print_json(data=summary)


@app.command("notify-test")
def notify_test_cmd(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Send a one-line test message to the configured Telegram chat."""
    _setup_logging(verbose)
    from .notify import is_configured, send_message

    if not is_configured():
        console.print(
            "[red]✗[/red] Telegram not configured — set TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_CHAT_ID in .env."
        )
        raise typer.Exit(1)
    ok = send_message("✅ IBKR Dashboard — prueba de notificación.")
    console.print(f"[green]✓[/green] sent={ok}")


@app.command("notify-now")
def notify_now_cmd(
    target: str = typer.Option(
        "", "--date", help="Day to report (yyyy-mm-dd). Default: yesterday."
    ),
    test: bool = typer.Option(
        False, "--test",
        help="Build the messages and print them WITHOUT sending or moving the watermark.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Send (or preview) the dividends / operations / commissions digest for a day."""
    _setup_logging(verbose)
    from datetime import date, timedelta

    from .db import init_db, session_scope
    from .notify.reports import (
        commissions_report,
        daily_notify,
        dividends_report,
        operations_report,
    )

    init_db()
    day = date.fromisoformat(target) if target else None
    if test:
        s = get_settings()
        day = day or (date.today() - timedelta(days=1))
        with session_scope() as sess:
            for kind, text in (
                ("dividends", dividends_report(sess, day, s.base_currency)),
                ("operations", operations_report(sess, day, s.base_currency)),
                ("commissions", commissions_report(sess, day, s.base_currency)),
            ):
                console.rule(f"{kind} — {day}")
                console.print(text or "[dim](nada)[/dim]")
        return
    with session_scope() as sess:
        summary = daily_notify(sess, target=day, force=bool(target), update_watermark=not target)
    console.print(summary)


@app.command("serve")
def serve_cmd(
    host: str = typer.Option(None),
    port: int = typer.Option(None),
    reload: bool = typer.Option(False),
):
    """Start the FastAPI server (uvicorn)."""
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "ibkr_backend.api.main:app",
        host=host or s.api_host,
        port=port or s.api_port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
