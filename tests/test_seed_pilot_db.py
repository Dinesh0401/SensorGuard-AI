"""Unit tests for deterministic pilot database seeder and provenance validation."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from scripts.seed_pilot_db import seed_sqlite_pilot_db


def test_seed_sqlite_pilot_db_creates_and_validates_database(tmp_path: Path):
    """Verify seeder restores verified fixture contents, SHA-256 hashes, and exact metrics."""
    target_db = tmp_path / "test_sensorguard.db"
    assert not target_db.exists()

    result = seed_sqlite_pilot_db(db_path=target_db)
    assert result is True
    assert target_db.exists()

    with closing(sqlite3.connect(target_db)) as conn:
        st_cnt = conn.execute("SELECT count(*) FROM stations").fetchone()[0]
        s_cnt = conn.execute("SELECT count(*) FROM sensors").fetchone()[0]
        b_cnt = conn.execute("SELECT count(*) FROM raw_ingest_batches").fetchone()[0]
        obs_cnt = conn.execute("SELECT count(*) FROM observations").fetchone()[0]

    assert st_cnt == 8
    assert s_cnt == 38
    assert b_cnt == 17
    assert obs_cnt == 14556

    # Idempotent second call when verified fixture already matches returns True cleanly
    assert seed_sqlite_pilot_db(db_path=target_db) is True


def test_seed_refuses_silent_overwrite_of_user_database(tmp_path: Path):
    """Seeder must refuse overwriting an existing custom DB unless force+confirm_overwrite are passed."""
    custom_db = tmp_path / "custom_user.db"
    with closing(sqlite3.connect(custom_db)) as conn:
        conn.execute("CREATE TABLE stations (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO stations VALUES (1, 'User Custom Live Station')")
        conn.commit()

    # Without force -> must refuse cleanly
    with pytest.raises(FileExistsError, match="Refusing to silently overwrite"):
        seed_sqlite_pilot_db(db_path=custom_db)

    # With force=True alone (without confirm_overwrite=True) -> must refuse
    with pytest.raises(PermissionError, match="confirm_overwrite=True"):
        seed_sqlite_pilot_db(db_path=custom_db, force=True)

    # With explicit force=True AND confirm_overwrite=True -> succeeds AND writes .bak backup
    assert seed_sqlite_pilot_db(db_path=custom_db, force=True, confirm_overwrite=True) is True
    backup = custom_db.with_suffix(".db.bak")
    assert backup.exists()
    with closing(sqlite3.connect(backup)) as bconn:
        assert bconn.execute("SELECT name FROM stations").fetchone()[0] == "User Custom Live Station"
