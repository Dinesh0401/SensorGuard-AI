"""Idempotent SQLite-to-PostgreSQL data migration script for SensorGuard AI.

Copies stations, sensors, raw ingestion batches, versioned observations, and
feature snapshots from the local SQLite database into a target PostgreSQL
database, preserving primary keys, normalizing naive UTC timestamps, resetting
PostgreSQL SERIAL sequences, and verifying row-count parity.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import MetaData, Table, create_engine, func, select, text
from sqlalchemy.engine import Engine

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Strict foreign-key topological order
COPY_TABLES = [
    "stations",
    "sensors",
    "raw_ingest_batches",
    "observations",
    "forecast_feature_snapshots",
]

ALL_SEQUENCE_TABLES = [
    *COPY_TABLES,
    "forecast_runs",
    "forecasts",
]


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Ensure SQLite naive datetimes are stored explicitly as UTC TIMESTAMPTZ values."""
    normalized: dict[str, Any] = {}
    for key, val in row.items():
        if isinstance(val, datetime):
            normalized[key] = val.replace(tzinfo=UTC) if val.tzinfo is None else val.astimezone(UTC)
        else:
            normalized[key] = val
    return normalized


def _reset_postgres_sequences(pg_engine: Engine, tables: list[str]) -> None:
    """Synchronize PostgreSQL SERIAL/IDENTITY sequences to MAX(id) after explicit-id inserts."""
    with pg_engine.begin() as conn:
        for table_name in tables:
            seq_name = conn.execute(
                text("SELECT pg_get_serial_sequence(:tbl, 'id')"),
                {"tbl": table_name},
            ).scalar()
            if not seq_name:
                continue
            max_id = conn.execute(text(f'SELECT MAX(id) FROM "{table_name}"')).scalar()
            if max_id is None:
                conn.execute(text("SELECT setval(:seq, 1, false)"), {"seq": seq_name})
            else:
                conn.execute(
                    text("SELECT setval(:seq, :val, true)"),
                    {"seq": seq_name, "val": int(max_id)},
                )
            logger.info("Synchronized sequence %s (max_id=%s)", seq_name, max_id)


def migrate_sqlite_to_postgres(
    sqlite_url: str = "sqlite:///data/sensorguard.db",
    postgres_url: str | None = None,
    batch_size: int = 1000,
    truncate_first: bool = False,
) -> dict[str, dict[str, int]]:
    """Copy historical pilot tables from SQLite into an Alembic-migrated PostgreSQL DB."""
    target_url = postgres_url or os.getenv("DATABASE_URL")
    if not target_url:
        raise ValueError("Target PostgreSQL URL must be supplied via --target-url or DATABASE_URL")
    if not target_url.startswith("postgresql"):
        raise ValueError(f"Target URL must be PostgreSQL, got: {target_url.split('://')[0]}")

    if sqlite_url.startswith("sqlite"):
        raw_path = sqlite_url.split("sqlite:///", 1)[-1]
        db_file = Path(raw_path)
        if not db_file.exists():
            from scripts.seed_pilot_db import seed_sqlite_pilot_db

            logger.info("SQLite database %s absent; initializing from verified pilot fixture...", db_file)
            seed_sqlite_pilot_db(db_path=db_file)

    src_engine = create_engine(sqlite_url, echo=False)
    dst_engine = create_engine(target_url, echo=False, pool_pre_ping=True)

    src_meta = MetaData()
    src_meta.reflect(bind=src_engine)
    dst_meta = MetaData()
    dst_meta.reflect(bind=dst_engine)

    report: dict[str, dict[str, int]] = {}

    if truncate_first:
        logger.warning("Truncating target PostgreSQL tables prior to import (--truncate)...")
        with dst_engine.begin() as dst_conn:
            for tbl_name in reversed(COPY_TABLES):
                if tbl_name in dst_meta.tables:
                    dst_conn.execute(dst_meta.tables[tbl_name].delete())

    for table_name in COPY_TABLES:
        if table_name not in src_meta.tables:
            logger.info("Skipping %s (not present in source SQLite)", table_name)
            continue
        if table_name not in dst_meta.tables:
            raise RuntimeError(
                f"Target table '{table_name}' missing in PostgreSQL; run 'alembic upgrade head' first."
            )

        src_table: Table = src_meta.tables[table_name]
        dst_table: Table = dst_meta.tables[table_name]
        common_cols = [c.name for c in src_table.columns if c.name in dst_table.columns]

        with src_engine.connect() as src_conn:
            src_count = int(src_conn.execute(select(func.count()).select_from(src_table)).scalar() or 0)

        with dst_engine.connect() as dst_conn:
            existing_dst_count = int(
                dst_conn.execute(select(func.count()).select_from(dst_table)).scalar() or 0
            )
            existing_ids: set[int] = set()
            if existing_dst_count > 0 and "id" in dst_table.c:
                existing_ids = {int(r[0]) for r in dst_conn.execute(select(dst_table.c.id)).fetchall()}

        inserted = 0
        skipped = 0
        if src_count > 0:
            sel = select(*[src_table.c[col] for col in common_cols])
            if "id" in src_table.c:
                sel = sel.order_by(src_table.c.id.asc())

            with src_engine.connect() as src_conn:
                result_proxy = src_conn.execution_options(stream_results=True).execute(sel)
                while True:
                    chunk = result_proxy.fetchmany(batch_size)
                    if not chunk:
                        break
                    rows_to_insert: list[dict[str, Any]] = []
                    for row in chunk:
                        row_dict = _normalize_row(dict(row._mapping))
                        row_id = row_dict.get("id")
                        if row_id is not None and int(row_id) in existing_ids:
                            skipped += 1
                            continue
                        rows_to_insert.append(row_dict)

                    if rows_to_insert:
                        with dst_engine.begin() as dst_conn:
                            dst_conn.execute(dst_table.insert(), rows_to_insert)
                        inserted += len(rows_to_insert)

        with dst_engine.connect() as dst_conn:
            final_dst_count = int(dst_conn.execute(select(func.count()).select_from(dst_table)).scalar() or 0)

        report[table_name] = {
            "source_rows": src_count,
            "inserted_rows": inserted,
            "skipped_existing": skipped,
            "target_rows": final_dst_count,
        }
        logger.info(
            "Table %-26s source=%6d inserted=%6d skipped=%6d target=%6d",
            table_name,
            src_count,
            inserted,
            skipped,
            final_dst_count,
        )

    _reset_postgres_sequences(dst_engine, ALL_SEQUENCE_TABLES)

    for tbl_name, stats in report.items():
        if stats["target_rows"] < stats["source_rows"]:
            raise RuntimeError(
                f"Row count mismatch on {tbl_name}: source={stats['source_rows']} > target={stats['target_rows']}"
            )

    src_engine.dispose()
    dst_engine.dispose()
    return report


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Migrate SensorGuard SQLite data to PostgreSQL")
    parser.add_argument(
        "--source-url",
        default="sqlite:///data/sensorguard.db",
        help="Source SQLite SQLAlchemy URL",
    )
    parser.add_argument(
        "--target-url",
        default=None,
        help="Target PostgreSQL SQLAlchemy URL (defaults to DATABASE_URL env var)",
    )
    parser.add_argument("--batch-size", type=int, default=1000, help="Insert chunk size")
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Truncate copied tables in destination before inserting",
    )
    args = parser.parse_args()

    try:
        report = migrate_sqlite_to_postgres(
            sqlite_url=args.source_url,
            postgres_url=args.target_url,
            batch_size=args.batch_size,
            truncate_first=args.truncate,
        )
        print("Migration complete:")
        for tbl, stats in report.items():
            print(
                f"  {tbl}: source={stats['source_rows']} inserted={stats['inserted_rows']} "
                f"skipped={stats['skipped_existing']} target={stats['target_rows']}"
            )
        return 0
    except Exception:
        logger.exception("SQLite-to-PostgreSQL migration failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
