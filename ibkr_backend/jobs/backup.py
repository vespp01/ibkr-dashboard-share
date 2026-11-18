"""Nightly online backup of the SQLite database.

The DB lives only in the `ibkr_dashboard_db_data` volume and holds state Flex
cannot re-create: notification overrides, NotificationState watermarks and
CSV-only history rows. Before this job the last copy was a manual one from
2026-06-14.

Uses SQLite's online backup API, so it is safe while the backend writes (WAL).
Files are named ``ibkr_backend.nightly-<UTC stamp>.db``; retention only ever
deletes files with that prefix, so manual copies (``ibkr_backend.PRE-*.db``)
in the same directory are kept.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

log = logging.getLogger(__name__)

BACKUP_PREFIX = "ibkr_backend.nightly-"
BACKUP_SUFFIX = ".db"


def sqlite_path_from_url(database_url: str) -> Path | None:
    """File path of a SQLite URL, or ``None`` for other engines / in-memory DBs."""
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database:
        return None
    if url.database == ":memory:":
        return None
    return Path(url.database)


def backup_sqlite_database(
    database_url: str,
    dest_dir: Path,
    *,
    keep: int = 14,
    now: datetime | None = None,
) -> Path:
    """Write one backup into ``dest_dir`` and prune to the newest ``keep`` files.

    Returns the new backup's path. Raises on any failure (the caller logs it).
    """
    src_path = sqlite_path_from_url(database_url)
    if src_path is None:
        raise ValueError("Nightly backup supports file-based SQLite only")
    if not src_path.exists():
        raise FileNotFoundError(f"Database file not found: {src_path}")
    if keep < 1:
        raise ValueError("keep must be >= 1")

    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    final = dest_dir / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
    tmp = final.with_name(final.name + ".tmp")
    # A run killed mid-copy (container stop, OOM) skips the cleanup below and
    # leaves a DB-sized orphan (plus SQLite's `-journal`) the retention glob
    # never matches. Only files older than an hour: a copy takes seconds.
    stale_before = time.time() - 3600
    for orphan in dest_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}.tmp*"):
        if orphan.stat().st_mtime < stale_before:
            orphan.unlink(missing_ok=True)

    # Owner-only from the first byte. Outside the try: if another run owns this
    # tmp (FileExistsError), the cleanup below must not delete its file.
    os.close(os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    try:
        src = sqlite3.connect(str(src_path), timeout=30)
        try:
            dst = sqlite3.connect(str(tmp))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        os.chmod(tmp, 0o600)  # portfolio data: owner-only
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    backups = sorted(dest_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"))
    for old in backups[:-keep]:
        old.unlink()
        log.info("Nightly DB backup pruned: %s", old.name)
    return final


def latest_backup_info(
    dest_dir: Path | str,
    *,
    stale_after_hours: float,
    now: datetime | None = None,
) -> dict:
    """Newest nightly backup of ``dest_dir`` and whether it is overdue (B42).

    ``is_overdue`` (deliberately not ``is_stale``: the watchdog's flat JSON grep
    reads the first ``"is_stale"`` key) is True only when a nightly file exists
    and is older than ``stale_after_hours``. An empty directory is reported as
    ``last_backup_at: None`` and NOT overdue: the first run is the next 03:45 and
    a failing run already pushes its own alert (`_alert_backup_failure`).
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    newest: datetime | None = None
    path = Path(dest_dir) if dest_dir else None
    if path is not None and path.is_dir():
        for f in path.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"):
            stamp = f.name[len(BACKUP_PREFIX) : -len(BACKUP_SUFFIX)]
            try:
                when = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            except ValueError:
                when = datetime.fromtimestamp(f.stat().st_mtime, UTC)
            if newest is None or when > newest:
                newest = when
    if newest is None:
        return {
            "enabled": path is not None,
            "last_backup_at": None,
            "age_hours": None,
            "is_overdue": False,
            "threshold_hours": stale_after_hours,
        }
    age = (now - newest).total_seconds() / 3600.0
    return {
        "enabled": True,
        "last_backup_at": newest.isoformat().replace("+00:00", "Z"),
        "age_hours": round(age, 1),
        "is_overdue": age > stale_after_hours,
        "threshold_hours": stale_after_hours,
    }
