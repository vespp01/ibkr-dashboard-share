"""Nightly SQLite backup: real files under tmp_path, no mocking."""

from __future__ import annotations

import os
import sqlite3
import stat
import time
from datetime import UTC, datetime, timedelta

import pytest

from ibkr_backend.config import get_settings
from ibkr_backend.db import session_scope
from ibkr_backend.db.models import NotificationState
from ibkr_backend.jobs import scheduler as sch
from ibkr_backend.jobs.backup import (
    BACKUP_PREFIX,
    backup_sqlite_database,
    sqlite_path_from_url,
)


def test_backup_copies_the_live_db(tmp_path):
    with session_scope() as s:
        s.add(NotificationState(key="backup_probe", value="kept"))
    dest = tmp_path / "backups"

    path = backup_sqlite_database(get_settings().database_url, dest, keep=14)

    assert path.parent == dest
    assert path.name.startswith(BACKUP_PREFIX) and path.suffix == ".db"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT value FROM notification_state WHERE key = 'backup_probe'"
        ).fetchone()
    assert row == ("kept",)
    assert not list(dest.glob("*.tmp"))


def test_backup_keeps_newest_n_and_never_touches_manual_copies(tmp_path):
    dest = tmp_path / "backups"
    dest.mkdir()
    manual = dest / "ibkr_backend.PRE-hygiene-20260614.db"
    manual.write_bytes(b"manual")
    start = datetime(2026, 9, 1, 1, 45, tzinfo=UTC)
    url = get_settings().database_url

    for day in range(5):
        backup_sqlite_database(url, dest, keep=3, now=start + timedelta(days=day))

    nightly = sorted(p.name for p in dest.glob(f"{BACKUP_PREFIX}*.db"))
    assert nightly == [
        f"{BACKUP_PREFIX}20260903T014500Z.db",
        f"{BACKUP_PREFIX}20260904T014500Z.db",
        f"{BACKUP_PREFIX}20260905T014500Z.db",
    ]
    assert manual.read_bytes() == b"manual"


def test_backup_rejects_non_file_databases(tmp_path):
    assert sqlite_path_from_url("sqlite:///:memory:") is None
    assert sqlite_path_from_url("postgresql://u@h/db") is None
    with pytest.raises(ValueError):
        backup_sqlite_database("postgresql://u@h/db", tmp_path)


def test_backup_job_scheduled_only_when_backup_dir_is_set(tmp_path, monkeypatch):
    # Independent of a BACKUP_DIR in the host .env or the container (compose sets /backups).
    monkeypatch.setenv("BACKUP_DIR", "")
    get_settings.cache_clear()
    sched = sch.build_scheduler()
    assert sched.get_job("nightly_db_backup") is None  # default: disabled

    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    get_settings.cache_clear()
    sched = sch.build_scheduler()
    sched.start(paused=True)
    try:
        job = sched.get_job("nightly_db_backup")
        assert job is not None
        assert str(job.trigger) == str(sch.CronTrigger.from_crontab(get_settings().backup_cron))
    finally:
        sched.shutdown(wait=False)

    sch._backup_job()  # writes into BACKUP_DIR, never raises
    assert len(list((tmp_path / "backups").glob(f"{BACKUP_PREFIX}*.db"))) == 1


def test_backup_removes_orphan_tmp_files(tmp_path):
    src = tmp_path / "live.db"
    sqlite3.connect(src).close()
    dest = tmp_path / "backups"
    dest.mkdir()
    orphan = dest / f"{BACKUP_PREFIX}20260901T014500Z.db.tmp"
    orphan.write_bytes(b"half a copy")
    journal = dest / f"{BACKUP_PREFIX}20260901T014500Z.db.tmp-journal"
    journal.write_bytes(b"journal")
    two_hours_ago = time.time() - 7200
    for f in (orphan, journal):
        os.utime(f, (two_hours_ago, two_hours_ago))
    in_progress = dest / f"{BACKUP_PREFIX}20260906T014500Z.db.tmp"  # fresh: another run's copy
    in_progress.write_bytes(b"copying")

    path = backup_sqlite_database(f"sqlite:///{src}", dest)

    assert not orphan.exists() and not journal.exists()
    assert in_progress.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_invalid_backup_cron_does_not_break_the_scheduler(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("BACKUP_CRON", "45 3 * *")  # 4 fields
    get_settings.cache_clear()

    sched = sch.build_scheduler()  # must not raise

    assert sched.get_job("nightly_db_backup") is None
    assert sched.get_job("daily_flex_pull") is not None


def test_backup_failure_sends_one_alert(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "missing.db"))
    get_settings.cache_clear()
    sent: list[str] = []
    monkeypatch.setattr("ibkr_backend.notify.is_configured", lambda: True)
    monkeypatch.setattr("ibkr_backend.notify.send_message", lambda text, **kw: sent.append(text) or True)

    sch._backup_job()  # never raises

    assert len(sent) == 1
    assert "backup nocturno" in sent[0] and "FileNotFoundError" in sent[0]
