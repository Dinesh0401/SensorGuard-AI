# SensorGuard AI — Real PostgreSQL 18 Integration Results

**Milestone:** 7.1 — Real Database Integration, Forecast Persistence & Backup Recovery  
**Date:** 2026-11-25  
**Branch:** `feat/milestone-3-forecasting-uncertainty`  
**Host Environment:** `Windows-11-10.0.26300-SP0`, `AMD64`, `16` logical CPUs  
**Database Engine:** `PostgreSQL 18.0` running on loopback TCP `127.0.0.1:5433` with `scram-sha-256` authentication  

---

## 1. Schema Evolution & Migration Execution

Added SQLAlchemy ORM persistence tables [`ForecastRun`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/db/schema.py) (`forecast_runs`) and [`ForecastRecord`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/db/schema.py) (`forecasts`) along with Alembic revision [`005_persistent_forecasts`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/alembic/versions/005_persistent_forecasts.py).

`alembic upgrade head` executed directly against a fresh PostgreSQL 18.0 database (`sensorguard`) cleanly applied all five revisions without errors:
- `001_initial_schema` -> `002_version_aware_observations` -> `003_add_persisted_at` -> `004_snapshots_and_raw_bytes` -> `005_persistent_forecasts`

---

## 2. Historical SQLite-to-PostgreSQL Migration & Idempotency

Executed [`scripts/migrate_sqlite_to_postgres.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/scripts/migrate_sqlite_to_postgres.py) copying `data/sensorguard.db` in foreign-key topological order with explicit UTC timezone coercion on naive SQLite datetimes and `pg_get_serial_sequence` + `setval` sequence synchronization:

| Table | Source Rows (SQLite) | Inserted Rows (Run 1) | Re-run Skipped (Idempotency) | Final PostgreSQL Rows | Synchronized Sequence (`MAX(id)`) |
|---|---:|---:|---:|---:|---|
| `stations` | `8` | `8` | `8` | `8` | `public.stations_id_seq` (`8`) |
| `sensors` | `38` | `38` | `38` | `38` | `public.sensors_id_seq` (`38`) |
| `raw_ingest_batches` | `17` | `17` | `17` | `17` | `public.raw_ingest_batches_id_seq` (`17`) |
| `observations` | `14,556` | `14,556` | `14,556` | `14,556` | `public.observations_id_seq` (`14556`) |
| `forecast_feature_snapshots` | `0` | `0` | `0` | `0` | `public.forecast_feature_snapshots_id_seq` (`None`) |

---

## 3. End-to-End Integration Verification Against Live PostgreSQL 18

Executed full Ruff static analysis and the complete 91-test suite with `DATABASE_URL` and `SENSORGUARD_TEST_POSTGRES_URL` pointing to `127.0.0.1:5433/sensorguard`:

- **Linting:** `ruff check src tests scripts alembic` — **All checks passed!**
- **Pytest Full Suite:** `pytest -v` — **`91 passed, 0 failed` in `56.05s`** (`87` core tests + `4` live PostgreSQL 18 tests in [`tests/test_postgres_integration.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/tests/test_postgres_integration.py)):
  1. `test_postgres_migrations_and_row_counts`: Confirmed `alembic_version == '005_persistent_forecasts'` and table queryability.
  2. `test_postgres_versioned_ingest_and_pit_replay`: Verified append-only `version=1` (`14.2 µg/m³`) and `version=2` (`19.8 µg/m³`) ingest plus strict `prospective` `PointInTimeReplayEngine.query_as_of` cutoff isolation.
  3. `test_postgres_forecast_persistence_and_api_roundtrip`: Verified SHA-256 `ModelManifest.verify_file`, persisted `ForecastRun` + `ForecastRecord` batches (`24` rows/station across `8` stations), and isolated FastAPI `TestClient` subprocess connectivity (`/health/ready`, `8` stations, `12` forecasts).
  4. `test_postgres_scheduler_lock_recovery`: Executed `IngestScheduler.poll_once()` against PostgreSQL 18, simulated a transient database exception inside `check_network_freshness`, confirmed atomic inter-process file lock release (`not lock_path.exists()`), and verified immediate recovery poll success.

---

## 4. Custom-Format Backup & Clean Restore (`pg_dump` / `pg_restore`)

Executed cold custom-format archive backup and restore into a separate empty database (`sensorguard_restored`):

- **Backup Command:** `pg_dump -h 127.0.0.1 -p 5433 -U postgres -Fc -f sensorguard_backup.dump sensorguard` (`1.284s`, archive size `961,505` bytes)
- **Restore Command:** `pg_restore -h 127.0.0.1 -p 5433 -U postgres -d sensorguard_restored --no-owner --no-privileges sensorguard_backup.dump` (`1.495s`)
- **Restored Row-Count Parity Check:**
  - `stations = 8`, `sensors = 38`, `raw_ingest_batches = 17`, `observations = 14556`, `forecast_runs = 3`, `forecasts = 576`

> [!NOTE]
> These measurements establish functional parity, transactional migration safety, scheduler recovery, forecast persistence, and backup/restore integrity against local PostgreSQL 18.0 on loopback TCP. Multi-day cloud soak uptime and external TLS ingress remain unverified until cloud staging deployment.
