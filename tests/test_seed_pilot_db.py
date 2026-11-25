"""Unit tests for deterministic pilot database seeder."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.seed_pilot_db import seed_sqlite_pilot_db


def test_seed_sqlite_pilot_db_creates_valid_database(tmp_path: Path):
    """Verify seeder restores 8 stations, 38 sensors, 17 batches, and 14,556 observations."""
    target_db = tmp_path / "test_sensorguard.db"
    assert not target_db.exists()

    result = seed_sqlite_pilot_db(db_path=target_db)
    assert result is True
    assert target_db.exists()

    with sqlite3.connect(target_db) as conn:
        st_cnt = conn.execute("SELECT count(*) FROM stations").fetchone()[0]
        s_cnt = conn.execute("SELECT count(*) FROM sensors").fetchone()[0]
        b_cnt = conn.execute("SELECT count(*) FROM raw_ingest_batches").fetchone()[0]
        obs_cnt = conn.execute("SELECT count(*) FROM observations").fetchone()[0]

    assert st_cnt == 8
    assert s_cnt == 38
    assert b_cnt == 17
    assert obs_cnt == 14556

    # Idempotent second call without force returns True immediately without error
    assert seed_sqlite_pilot_db(db_path=target_db) is True
