"""Deterministic database seeder & provenance validator for SensorGuard AI pilot data.

Restores the UK AURN Q1 2024 pilot database (8 stations, 38 sensors, 17 raw batches,
14,556 versioned observations) from the tracked compressed fixture into SQLite.
Enforces SHA-256 verification against data/fixtures/PROVENANCE_AND_LICENSE.json,
validates restored data contents/causality/batch hashes (not just row counts), and
refuses to silently overwrite existing user/non-empty databases without explicit flags.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURE_PATH = ROOT_DIR / "data" / "fixtures" / "sensorguard_pilot_fixture.sql.gz"
DEFAULT_MANIFEST_PATH = ROOT_DIR / "data" / "fixtures" / "PROVENANCE_AND_LICENSE.json"
DEFAULT_DB_PATH = ROOT_DIR / "data" / "sensorguard.db"


def load_fixture_manifest(manifest_path: str | Path = DEFAULT_MANIFEST_PATH) -> dict[str, Any]:
    """Load audited provenance and checksum expectations for the tracked pilot fixture."""
    mpath = Path(manifest_path)
    if not mpath.exists():
        raise FileNotFoundError(f"Fixture provenance manifest missing: {mpath}")
    return json.loads(mpath.read_text(encoding="utf-8"))


def validate_restored_db_contents(
    conn: sqlite3.Connection,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate schema, station provenance, values, timestamps, and batch hashes inside SQLite."""
    exp = (manifest or load_fixture_manifest())["expected_restored_contents"]

    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    required_tables = {"stations", "sensors", "raw_ingest_batches", "observations"}
    missing = required_tables - tables
    if missing:
        raise ValueError(f"Database missing required tables: {sorted(missing)}")

    st_cnt = int(conn.execute("SELECT count(*) FROM stations").fetchone()[0])
    s_cnt = int(conn.execute("SELECT count(*) FROM sensors").fetchone()[0])
    b_cnt = int(conn.execute("SELECT count(*) FROM raw_ingest_batches").fetchone()[0])
    obs_cnt = int(conn.execute("SELECT count(*) FROM observations").fetchone()[0])

    if (st_cnt, s_cnt, b_cnt, obs_cnt) != (
        exp["stations_count"],
        exp["sensors_count"],
        exp["raw_ingest_batches_count"],
        exp["observations_count"],
    ):
        raise ValueError(
            f"Restored count mismatch: got ({st_cnt}, {s_cnt}, {b_cnt}, {obs_cnt}), "
            f"expected ({exp['stations_count']}, {exp['sensors_count']}, "
            f"{exp['raw_ingest_batches_count']}, {exp['observations_count']})"
        )

    loc_ids = sorted(
        int(row[0]) for row in conn.execute("SELECT provider_location_id FROM stations").fetchall()
    )
    if loc_ids != sorted(exp["expected_provider_location_ids"]):
        raise ValueError(f"Unexpected station provider_location_ids: {loc_ids}")

    # Verify observation contents, value bounds, causality, and valid flags
    obs_min, obs_max, obs_avg, future_leaks, invalid_flags, null_vals = conn.execute(
        """
        SELECT
            MIN(value),
            MAX(value),
            AVG(value),
            SUM(CASE WHEN observed_at > fetched_at THEN 1 ELSE 0 END),
            SUM(CASE WHEN quality_flag != 'VALID' THEN 1 ELSE 0 END),
            SUM(CASE WHEN value IS NULL THEN 1 ELSE 0 END)
        FROM observations
        """
    ).fetchone()
    if int(null_vals or 0) > 0:
        raise ValueError(f"Observations contain {null_vals} NULL values")
    if int(future_leaks or 0) > exp["max_future_observation_leak_count"]:
        raise ValueError(f"Causal violation in restored observations: {future_leaks} future leaks")
    if int(invalid_flags or 0) > exp["max_invalid_quality_flag_count"]:
        raise ValueError(f"Unexpected non-VALID observations count: {invalid_flags}")
    if abs(float(obs_min) - exp["pm25_min"]) > 1e-4 or abs(float(obs_max) - exp["pm25_max"]) > 1e-4:
        raise ValueError(f"Observation min/max mismatch: got ({obs_min}, {obs_max})")
    if abs(float(obs_avg) - exp["pm25_mean"]) > 1e-3:
        raise ValueError(f"Observation mean mismatch: got {obs_avg}, expected {exp['pm25_mean']}")

    min_obs_at, max_obs_at = conn.execute(
        "SELECT MIN(observed_at), MAX(observed_at) FROM observations"
    ).fetchone()
    if not (str(min_obs_at).startswith("2024-01-01") and str(max_obs_at).startswith("2024-03-31")):
        raise ValueError(f"Observation window mismatch: {min_obs_at} .. {max_obs_at}")

    # Verify every raw ingest batch hash integrity
    batch_rows = conn.execute("SELECT id, response_hash, raw_bytes FROM raw_ingest_batches").fetchall()
    for bid, expected_hash, raw_bytes in batch_rows:
        if raw_bytes is None:
            raise ValueError(f"Raw ingest batch {bid} missing raw_bytes")
        actual_hash = hashlib.sha256(bytes(raw_bytes)).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError(f"Raw batch {bid} SHA-256 mismatch: {actual_hash} != {expected_hash}")

    return {
        "stations": st_cnt,
        "sensors": s_cnt,
        "batches": b_cnt,
        "observations": obs_cnt,
        "pm25_mean": round(float(obs_avg), 6),
        "batch_hashes_verified": len(batch_rows),
    }


def _db_has_user_contents(target: Path) -> bool:
    """Return True if an existing SQLite file contains any user tables or non-zero data."""
    if not target.exists():
        return False
    if target.stat().st_size == 0:
        return False
    try:
        with closing(sqlite3.connect(target)) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            return len(tables) > 0
    except sqlite3.Error:
        return True


def seed_sqlite_pilot_db(
    db_path: str | Path = DEFAULT_DB_PATH,
    fixture_path: str | Path = DEFAULT_FIXTURE_PATH,
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    force: bool = False,
    confirm_overwrite: bool = False,
) -> bool:
    """Seed the SQLite database safely from the SHA-256 verified pilot SQL fixture.

    Never silently overwrites an existing database that does not match the audited fixture.
    To replace an existing non-fixture database, both force=True and confirm_overwrite=True
    must be specified; a timestamped .bak backup is created before replacement.
    """
    target = Path(db_path)
    fixture = Path(fixture_path)
    manifest = load_fixture_manifest(manifest_path)

    if not fixture.exists():
        raise FileNotFoundError(f"Pilot fixture not found at {fixture}")

    gz_bytes = fixture.read_bytes()
    actual_gz_sha = hashlib.sha256(gz_bytes).hexdigest()
    expected_gz_sha = manifest["compressed_sha256"]
    if actual_gz_sha != expected_gz_sha:
        raise ValueError(
            f"Fixture SHA-256 verification failed: {actual_gz_sha} != expected {expected_gz_sha}"
        )

    # Non-destructive check when target DB already exists
    if target.exists() and not force and _db_has_user_contents(target):
        try:
            with closing(sqlite3.connect(target)) as conn:
                summary = validate_restored_db_contents(conn, manifest)
            logger.info(
                "Target DB %s verified against pilot fixture manifest (%s).",
                target,
                summary,
            )
            return True
        except Exception as exc:
            raise FileExistsError(
                f"Refusing to silently overwrite existing non-empty database at '{target}' "
                f"(validation failed: {exc}). Back it up or pass force=True with confirm_overwrite=True."
            ) from exc

    if target.exists() and _db_has_user_contents(target) and not confirm_overwrite:
        raise PermissionError(
            f"Destructive overwrite of existing database '{target}' requires explicit confirm_overwrite=True."
        )

    logger.info("Decompressing SHA-256 verified pilot fixture from %s...", fixture)
    sql_bytes = gzip.decompress(gz_bytes)
    actual_sql_sha = hashlib.sha256(sql_bytes).hexdigest()
    if actual_sql_sha != manifest["decompressed_sql_sha256"]:
        raise ValueError(
            f"Decompressed SQL SHA-256 mismatch: {actual_sql_sha} != {manifest['decompressed_sql_sha256']}"
        )
    sql_text = sql_bytes.decode("utf-8")

    target.parent.mkdir(parents=True, exist_ok=True)

    # Atomic restore into sibling temporary file & validate contents BEFORE replacing target
    fd, tmp_name = tempfile.mkstemp(prefix=".seed_tmp_", suffix=".db", dir=str(target.parent))
    os.close(fd)
    tmp_db = Path(tmp_name)
    try:
        with closing(sqlite3.connect(tmp_db)) as conn:
            conn.executescript(sql_text)
            conn.commit()
            summary = validate_restored_db_contents(conn, manifest)

        if target.exists() and _db_has_user_contents(target):
            backup_path = target.with_suffix(".db.bak")
            shutil.copy2(target, backup_path)
            logger.warning("Created backup of overwritten DB at %s", backup_path)

        os.replace(tmp_db, target)
        logger.info("Successfully seeded and verified %s: %s", target, summary)
        return True
    finally:
        if tmp_db.exists():
            tmp_db.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed SensorGuard pilot database safely from verified fixture."
    )
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
        "--manifest-path",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="Path to provenance & SHA-256 manifest JSON",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Request database replacement if DB already exists",
    )
    parser.add_argument(
        "--confirm-overwrite",
        action="store_true",
        help="Explicitly confirm destructive replacement of an existing non-empty database (creates .bak)",
    )
    args = parser.parse_args()

    try:
        seed_sqlite_pilot_db(
            db_path=args.db_path,
            fixture_path=args.fixture_path,
            manifest_path=args.manifest_path,
            force=args.force,
            confirm_overwrite=args.confirm_overwrite,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("Seeding failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
