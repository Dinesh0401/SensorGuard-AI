"""Deterministic database seeder for SensorGuard AI pilot data.

Restores the UK AURN Q1 2024 pilot database (8 stations, 38 sensors, 17 raw batches,
14,556 versioned observations) from the tracked compressed fixture into SQLite.
Enables instant (<2s) deterministic database initialization in CI/CD runners without
external network dependencies.
"""

from __future__ import annotations

import argparse
import gzip
import logging
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURE_PATH = ROOT_DIR / "data" / "fixtures" / "sensorguard_pilot_fixture.sql.gz"
DEFAULT_DB_PATH = ROOT_DIR / "data" / "sensorguard.db"


def seed_sqlite_pilot_db(
    db_path: str | Path = DEFAULT_DB_PATH,
    fixture_path: str | Path = DEFAULT_FIXTURE_PATH,
    force: bool = False,
) -> bool:
    """Seed the SQLite database from the compressed SQL fixture.

    Parameters
    ----------
    db_path:
        Target path for the SQLite database file.
    fixture_path:
        Path to the gzipped SQL fixture file.
    force:
        If True, re-seeds even if target database is already populated.

    Returns
    -------
    bool:
        True if database is validly seeded, False otherwise.
    """
    target = Path(db_path)
    fixture = Path(fixture_path)

    if not fixture.exists():
        raise FileNotFoundError(f"Pilot fixture not found at {fixture}")

    # Check existing database state
    if target.exists() and not force:
        try:
            with sqlite3.connect(target) as conn:
                st_cnt = conn.execute("SELECT count(*) FROM stations").fetchone()[0]
                obs_cnt = conn.execute("SELECT count(*) FROM observations").fetchone()[0]
                if st_cnt == 8 and obs_cnt == 14556:
                    logger.info(
                        "Target DB %s already seeded (%d stations, %d observations).",
                        target,
                        st_cnt,
                        obs_cnt,
                    )
                    return True
        except Exception:  # noqa: BLE001
            logger.info("Target DB %s exists but lacks required pilot tables; re-seeding.", target)

    target.parent.mkdir(parents=True, exist_ok=True)

    # If force, remove existing file to start clean
    if force and target.exists():
        target.unlink()

    logger.info("Decompressing pilot fixture from %s...", fixture)
    sql_text = gzip.decompress(fixture.read_bytes()).decode("utf-8")

    logger.info("Executing SQLite restoration into %s...", target)
    with sqlite3.connect(target) as conn:
        conn.executescript(sql_text)
        st_cnt = conn.execute("SELECT count(*) FROM stations").fetchone()[0]
        s_cnt = conn.execute("SELECT count(*) FROM sensors").fetchone()[0]
        b_cnt = conn.execute("SELECT count(*) FROM raw_ingest_batches").fetchone()[0]
        obs_cnt = conn.execute("SELECT count(*) FROM observations").fetchone()[0]

    logger.info(
        "Successfully seeded %s: %d stations, %d sensors, %d batches, %d observations.",
        target,
        st_cnt,
        s_cnt,
        b_cnt,
        obs_cnt,
    )
    if st_cnt != 8 or obs_cnt != 14556:
        raise ValueError(f"Restored counts mismatch: stations={st_cnt} (exp 8), obs={obs_cnt} (exp 14556)")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed SensorGuard pilot database from fixture.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to output SQLite database (default: data/sensorguard.db)",
    )
    parser.add_argument(
        "--fixture-path",
        type=Path,
        default=DEFAULT_FIXTURE_PATH,
        help="Path to gzipped SQL fixture",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing database even if already populated",
    )
    args = parser.parse_args()

    try:
        seed_sqlite_pilot_db(db_path=args.db_path, fixture_path=args.fixture_path, force=args.force)
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("Seeding failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
