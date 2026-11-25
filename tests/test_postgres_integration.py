"""Real PostgreSQL integration tests for SensorGuard AI (Milestone 7.1).

Gated via SENSORGUARD_TEST_POSTGRES_URL so default offline SQLite test runs
skip cleanly unless a live PostgreSQL instance is provided.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import joblib
import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from alembic import command
from sensorguard.db.forecast_persistence import (
    get_latest_station_forecasts,
    materialize_station_forecasts,
)
from sensorguard.db.schema import (
    ForecastRecord,
    ForecastRun,
    Observation,
    RawIngestBatch,
    Station,
)
from sensorguard.ingest import IngestionPipeline
from sensorguard.models.data import build_spatiotemporal_features, load_pilot_dataset_from_db
from sensorguard.models.manifest import ModelManifest
from sensorguard.providers import OpenAQClient
from sensorguard.replay import PointInTimeReplayEngine
from sensorguard.scheduler import IngestScheduler, SchedulerConfig

POSTGRES_URL = os.getenv("SENSORGUARD_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL.startswith("postgresql"),
    reason="SENSORGUARD_TEST_POSTGRES_URL not configured",
)

ROOT_DIR = Path(__file__).resolve().parent.parent


def _alembic_cfg(db_url: str) -> Config:
    cfg = Config(str(ROOT_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture(scope="module")
def pg_engine():
    """Return SQLAlchemy engine connected to the test PostgreSQL instance."""
    engine = create_engine(POSTGRES_URL, echo=False, pool_pre_ping=True)
    command.upgrade(_alembic_cfg(POSTGRES_URL), "head")
    yield engine
    engine.dispose()


def test_postgres_migrations_and_row_counts(pg_engine):
    """Verify Alembic migrations at head and table accessibility in PostgreSQL."""
    with pg_engine.connect() as conn:
        ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert ver == "005_persistent_forecasts"
        station_cnt = conn.execute(select(func.count()).select_from(Station)).scalar() or 0
        obs_cnt = conn.execute(select(func.count()).select_from(Observation)).scalar() or 0
        assert station_cnt >= 0
        assert obs_cnt >= 0


def test_postgres_versioned_ingest_and_pit_replay(pg_engine):
    """Test append-only observation revisions, deduplication, and PIT replay on PostgreSQL."""
    t_obs = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
    t_fetch_v1 = datetime(2026, 2, 1, 12, 10, 0, tzinfo=UTC)
    t_fetch_v2 = datetime(2026, 2, 1, 15, 0, 0, tzinfo=UTC)

    with Session(bind=pg_engine) as session:
        # Clean synthetic test station if leftover from previous run
        existing = session.scalars(
            select(Station).where(Station.provider_location_id == 999901)
        ).first()
        if existing is not None:
            session.delete(existing)
            session.commit()

        loc_payload = {
            "id": 999901,
            "name": "PG Integration Synthetic Station",
            "coordinates": {"latitude": 51.5, "longitude": -0.1},
            "country": {"code": "GB"},
            "timezone": "UTC",
            "sensors": [{"id": 9999011, "parameter": {"name": "pm25", "units": "µg/m³"}}],
        }
        pipeline = IngestionPipeline(session)
        station, sensors = pipeline.upsert_station_and_sensors(loc_payload)
        sensor = sensors[0]
        session.commit()

        try:
            def handler_v1(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "datetime": {"utc": t_obs.isoformat().replace("+00:00", "Z")},
                                "period": {
                                    "datetimeFrom": {
                                        "utc": (t_obs - timedelta(hours=1))
                                        .isoformat()
                                        .replace("+00:00", "Z")
                                    },
                                    "datetimeTo": {
                                        "utc": t_obs.isoformat().replace("+00:00", "Z")
                                    },
                                },
                                "value": 14.2,
                                "parameter": {"name": "pm25", "units": "µg/m³"},
                            }
                        ]
                    },
                )

            client_v1 = OpenAQClient(api_key="test", transport=httpx.MockTransport(handler_v1))
            res1 = pipeline.ingest_sensor_hours(
                sensor_db_id=sensor.id,
                provider_sensor_id=9999011,
                client=client_v1,
                max_pages=1,
            )
            assert res1.records_inserted == 1

            obs_v1 = session.scalars(
                select(Observation).where(Observation.sensor_id == sensor.id, Observation.version == 1)
            ).one()
            obs_v1.fetched_at = t_fetch_v1
            obs_v1.persisted_at = t_fetch_v1
            session.commit()

            def handler_v2(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "datetime": {"utc": t_obs.isoformat().replace("+00:00", "Z")},
                                "value": 19.8,
                                "parameter": {"name": "pm25", "units": "µg/m³"},
                            }
                        ]
                    },
                )

            client_v2 = OpenAQClient(api_key="test", transport=httpx.MockTransport(handler_v2))
            res2 = pipeline.ingest_sensor_hours(
                sensor_db_id=sensor.id,
                provider_sensor_id=9999011,
                client=client_v2,
                max_pages=1,
            )
            assert res2.records_revised == 1
            obs_v2 = session.scalars(
                select(Observation).where(Observation.sensor_id == sensor.id, Observation.version == 2)
            ).one()
            obs_v2.fetched_at = t_fetch_v2
            obs_v2.persisted_at = t_fetch_v2
            session.commit()

            replay = PointInTimeReplayEngine(session)
            as_of_early, summary_early = replay.query_as_of(
                [sensor.id],
                decision_cutoff=datetime(2026, 2, 1, 13, 0, 0, tzinfo=UTC),
                mode="prospective",
            )
            assert len(as_of_early) == 1
            assert summary_early.total_visible_records == 1
            assert float(as_of_early.iloc[0]["value"]) == pytest.approx(14.2)

            as_of_late, summary_late = replay.query_as_of(
                [sensor.id],
                decision_cutoff=datetime(2026, 2, 1, 16, 0, 0, tzinfo=UTC),
                mode="prospective",
            )
            assert len(as_of_late) == 1
            assert summary_late.total_visible_records == 1
            assert float(as_of_late.iloc[0]["value"]) == pytest.approx(19.8)
        finally:
            # Clean up synthetic station and raw batches so pilot dataset counts stay exact
            batch_ids = [
                row[0]
                for row in session.execute(
                    select(RawIngestBatch.id).where(
                        RawIngestBatch.endpoint == "/v3/sensors/9999011/hours"
                    )
                ).all()
            ]
            session.delete(station)
            session.flush()
            for bid in batch_ids:
                batch = session.get(RawIngestBatch, bid)
                if batch is not None:
                    session.delete(batch)
            session.commit()


def test_postgres_forecast_persistence_and_api_roundtrip(pg_engine):
    """Verify SHA-256 model load, ForecastRun/ForecastRecord persistence, and FastAPI queries."""
    manifest_path = ROOT_DIR / "artifacts" / "models" / "manifest.json"
    model_path = ROOT_DIR / "artifacts" / "models" / "sage_pilot_q1_2024.joblib"
    assert manifest_path.exists()
    manifest = ModelManifest.load(manifest_path)
    assert manifest.verify_file(model_path) is True
    model = joblib.load(model_path)

    with Session(bind=pg_engine) as session:
        obs_cnt = session.scalars(select(func.count()).select_from(Observation)).one()
        if obs_cnt == 0:
            pytest.skip("PostgreSQL database has 0 migrated observations (CI clean DB mode)")

        df, coords = load_pilot_dataset_from_db(session)
        featured_df = build_spatiotemporal_features(df, coords)
        run = materialize_station_forecasts(
            session=session,
            featured_df=featured_df,
            model=model,
            manifest=manifest,
            limit_per_station=24,
            notes="M7.1 PostgreSQL persistence verification",
        )
        assert isinstance(run, ForecastRun)
        assert run.id is not None

        latest_run, records = get_latest_station_forecasts(session, station_id=1, limit=24)
        assert isinstance(latest_run, ForecastRun)
        assert latest_run.id == run.id
        assert len(records) == 24
        assert all(isinstance(r, ForecastRecord) for r in records)
        assert all(r.status in {"PUBLISH", "CAUTION", "ABSTAIN"} for r in records)

    # Verify live FastAPI endpoints bound via DATABASE_URL subprocess check
    env = os.environ.copy()
    env["DATABASE_URL"] = POSTGRES_URL
    code = (
        "from fastapi.testclient import TestClient; "
        "from sensorguard.api import app; "
        "c = TestClient(app); "
        "r1 = c.get('/health/ready').json(); "
        "assert r1['database_connected'] and r1['model_loaded'], r1; "
        "r2 = c.get('/api/v1/stations').json(); "
        "assert r2['total_count'] == 8, r2; "
        "r3 = c.get('/api/v1/stations/1/forecasts?limit=12').json(); "
        "assert len(r3['forecasts']) == 12, r3; "
        "print('API_PG_OK')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT_DIR),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "API_PG_OK" in proc.stdout


def test_postgres_scheduler_lock_recovery(pg_engine, tmp_path):
    """Verify IngestScheduler polls against PostgreSQL and recovers cleanly after lock/errors."""
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=pg_engine, expire_on_commit=False)
    lock_path = tmp_path / "pg_scheduler.lock"

    cfg = SchedulerConfig(
        enabled=True,
        fetch_live_provider=False,
        max_retries=0,
        lock_file=str(lock_path),
    )
    scheduler = IngestScheduler(config=cfg, session_factory=factory)

    # Normal poll succeeds and releases lock
    ok_summary = asyncio.run(scheduler.poll_once())
    assert ok_summary["status"] == "SUCCESS"
    assert not lock_path.exists()

    # Simulated failure inside freshness check must still release the inter-process file lock
    orig_check = scheduler.check_network_freshness

    def _boom(_session):
        raise RuntimeError("simulated transient DB error")

    scheduler.check_network_freshness = _boom  # type: ignore[method-assign]
    err_summary = asyncio.run(scheduler.poll_once())
    assert err_summary["status"] == "FAILED"
    assert not lock_path.exists()

    # Recovery poll immediately succeeds after failure
    scheduler.check_network_freshness = orig_check  # type: ignore[method-assign]
    rec_summary = asyncio.run(scheduler.poll_once())
    assert rec_summary["status"] == "SUCCESS"
    assert not lock_path.exists()
