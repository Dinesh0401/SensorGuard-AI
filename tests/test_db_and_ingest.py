"""Tests for database models, Alembic migrations, revisions, and raw hash provenance."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from sensorguard.db.schema import Base, ForecastFeatureSnapshot, Observation, RawIngestBatch
from sensorguard.ingest import IngestionPipeline, ObservationValidator
from sensorguard.providers import OpenAQClient
from sensorguard.replay import PointInTimeReplayEngine


@pytest.fixture
def test_db_session():
    """Create an isolated in-memory SQLite database session for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    with Session(bind=engine) as session:
        yield session


def test_observation_validator_end_of_period():
    raw_record = {
        "datetime": {"utc": "2025-02-10T14:00:00Z"},
        "period": {
            "datetimeFrom": {"utc": "2025-02-10T13:00:00Z"},
            "datetimeTo": {"utc": "2025-02-10T14:00:00Z"},
        },
        "value": 18.5,
        "parameter": {"name": "pm25", "units": "ug/m3"},
    }
    fetched_at = datetime(2025, 2, 10, 14, 5, 0, tzinfo=UTC)
    rec = ObservationValidator.validate_openaq_hourly_record(
        raw_record, sensor_db_id=1, fetched_at=fetched_at
    )

    assert rec.observed_at.hour == 14
    assert rec.period_start.hour == 13
    assert rec.period_end.hour == 14
    assert rec.received_at is None
    assert rec.unit == "µg/m³"
    assert rec.quality_flag == "VALID"
    assert rec.source_status == "HEALTHY"


def test_observation_validator_out_of_bounds():
    raw_record = {
        "datetime": {"utc": "2025-02-10T15:00:00Z"},
        "value": 1500.0,
        "parameter": {"name": "pm25", "units": "µg/m³"},
    }
    rec = ObservationValidator.validate_openaq_hourly_record(
        raw_record, sensor_db_id=1, fetched_at=datetime.now(UTC)
    )
    assert rec.quality_flag == "OUT_OF_BOUNDS"
    assert rec.source_status == "INVALID"


def test_observation_validator_missing_unit_rejection():
    raw_record_no_unit = {
        "datetime": {"utc": "2025-02-10T15:00:00Z"},
        "value": 25.0,
        "parameter": {"name": "pm25"},  # missing units
    }
    rec1 = ObservationValidator.validate_openaq_hourly_record(
        raw_record_no_unit, sensor_db_id=1, fetched_at=datetime.now(UTC)
    )
    assert rec1.quality_flag == "MISSING_UNIT"
    assert rec1.source_status == "INVALID"

    raw_record_empty_unit = {
        "datetime": {"utc": "2025-02-10T15:00:00Z"},
        "value": 25.0,
        "parameter": {"name": "pm25", "units": "   "},
    }
    rec2 = ObservationValidator.validate_openaq_hourly_record(
        raw_record_empty_unit, sensor_db_id=1, fetched_at=datetime.now(UTC)
    )
    assert rec2.quality_flag == "MISSING_UNIT"
    assert rec2.source_status == "INVALID"


def test_observation_validator_non_finite_rejection():
    raw_record_nan = {
        "datetime": {"utc": "2025-02-10T15:00:00Z"},
        "value": float("nan"),
        "parameter": {"name": "pm25", "units": "µg/m³"},
    }
    rec_nan = ObservationValidator.validate_openaq_hourly_record(
        raw_record_nan, sensor_db_id=1, fetched_at=datetime.now(UTC)
    )
    assert rec_nan.quality_flag == "NON_FINITE_VALUE"
    assert rec_nan.source_status == "INVALID"
    assert rec_nan.value is None

    raw_record_inf = {
        "datetime": {"utc": "2025-02-10T15:00:00Z"},
        "value": float("inf"),
        "parameter": {"name": "pm25", "units": "µg/m³"},
    }
    rec_inf = ObservationValidator.validate_openaq_hourly_record(
        raw_record_inf, sensor_db_id=1, fetched_at=datetime.now(UTC)
    )
    assert rec_inf.quality_flag == "NON_FINITE_VALUE"
    assert rec_inf.source_status == "INVALID"
    assert rec_inf.value is None


def test_station_coordinate_validation(test_db_session: Session):
    pipeline = IngestionPipeline(test_db_session)

    # Missing coordinates
    with pytest.raises(ValueError, match="missing coordinates"):
        pipeline.upsert_station_and_sensors({"id": 1, "name": "Bad Station"})

    # Null island (0.0, 0.0) coordinates rejected
    with pytest.raises(ValueError, match="null island"):
        pipeline.upsert_station_and_sensors(
            {"id": 2, "name": "Null Island Station", "coordinates": {"latitude": 0.0, "longitude": 0.0}}
        )

    # Out-of-bounds coordinates
    with pytest.raises(ValueError, match="coordinates invalid"):
        pipeline.upsert_station_and_sensors(
            {"id": 3, "name": "Invalid Lat", "coordinates": {"latitude": 95.0, "longitude": 50.0}}
        )


def test_idempotent_ingestion_and_exact_hash_provenance(test_db_session: Session):
    location_payload = {
        "id": 9999,
        "name": "Downtown Station",
        "coordinates": {"latitude": 40.7128, "longitude": -74.0060},
        "country": {"code": "US"},
        "timezone": "America/New_York",
        "sensors": [{"id": 501, "parameter": {"name": "pm25", "units": "µg/m³"}}],
    }

    mock_hourly_response = {
        "results": [
            {
                "datetime": {"utc": "2025-02-01T10:00:00Z"},
                "period": {
                    "datetimeFrom": {"utc": "2025-02-01T09:00:00Z"},
                    "datetimeTo": {"utc": "2025-02-01T10:00:00Z"},
                },
                "value": 22.0,
                "parameter": {"name": "pm25", "units": "µg/m³"},
            },
        ]
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_hourly_response)

    client = OpenAQClient(api_key="mock-key", transport=httpx.MockTransport(mock_handler))
    pipeline = IngestionPipeline(test_db_session)

    _station, sensors = pipeline.upsert_station_and_sensors(location_payload)
    sensor = sensors[0]

    # First ingestion
    res1 = pipeline.ingest_sensor_hours(
        sensor_db_id=sensor.id,
        provider_sensor_id=sensor.provider_sensor_id,
        client=client,
        max_pages=1,
    )
    assert res1.records_inserted == 1
    assert res1.records_deduplicated == 0

    # Verify exact raw response hash provenance across both text and raw bytes
    batch = test_db_session.scalars(select(RawIngestBatch)).first()
    assert batch is not None
    assert hashlib.sha256(batch.payload_json.encode("utf-8")).hexdigest() == batch.response_hash
    assert batch.raw_bytes is not None
    assert hashlib.sha256(batch.raw_bytes).hexdigest() == batch.response_hash

    # Exact duplicate run -> Deduplicated
    res2 = pipeline.ingest_sensor_hours(
        sensor_db_id=sensor.id,
        provider_sensor_id=sensor.provider_sensor_id,
        client=client,
        max_pages=1,
    )
    assert res2.records_inserted == 0
    assert res2.records_deduplicated == 1


def test_append_only_observation_revision_history(test_db_session: Session):
    location_payload = {
        "id": 8888,
        "name": "Revision Test Station",
        "coordinates": {"latitude": 51.5074, "longitude": -0.1278},
        "country": {"code": "GB"},
        "sensors": [{"id": 601, "parameter": {"name": "pm25", "units": "µg/m³"}}],
    }

    initial_response = {
        "results": [
            {
                "datetime": {"utc": "2025-02-01T10:00:00Z"},
                "period": {
                    "datetimeFrom": {"utc": "2025-02-01T09:00:00Z"},
                    "datetimeTo": {"utc": "2025-02-01T10:00:00Z"},
                },
                "value": 20.0,
                "parameter": {"name": "pm25", "units": "µg/m³"},
            }
        ]
    }

    revised_response = {
        "results": [
            {
                "datetime": {"utc": "2025-02-01T10:00:00Z"},
                "period": {
                    "datetimeFrom": {"utc": "2025-02-01T09:00:00Z"},
                    "datetimeTo": {"utc": "2025-02-01T10:00:00Z"},
                },
                "value": 26.5,
                "parameter": {"name": "pm25", "units": "µg/m³"},
            }
        ]
    }

    pipeline = IngestionPipeline(test_db_session)
    _, sensors = pipeline.upsert_station_and_sensors(location_payload)
    sensor = sensors[0]

    client1 = OpenAQClient(
        api_key="mock-key",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=initial_response)),
    )
    res1 = pipeline.ingest_sensor_hours(sensor.id, sensor.provider_sensor_id, client1, max_pages=1)
    assert res1.records_inserted == 1
    assert res1.records_revised == 0

    client2 = OpenAQClient(
        api_key="mock-key",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=revised_response)),
    )
    res2 = pipeline.ingest_sensor_hours(sensor.id, sensor.provider_sensor_id, client2, max_pages=1)
    assert res2.records_revised == 1
    assert res2.records_inserted == 0

    obs_rows = test_db_session.scalars(
        select(Observation).where(Observation.sensor_id == sensor.id).order_by(Observation.version.asc())
    ).all()
    assert len(obs_rows) == 2
    assert obs_rows[0].version == 1
    assert obs_rows[0].value == 20.0
    assert obs_rows[0].persisted_at is not None
    assert obs_rows[1].version == 2
    assert obs_rows[1].value == 26.5
    assert obs_rows[1].persisted_at is not None


def test_forecast_feature_snapshot_provenance(test_db_session: Session):
    location_payload = {
        "id": 7777,
        "name": "Snapshot Test Station",
        "coordinates": {"latitude": 51.5, "longitude": -0.1},
        "country": {"code": "GB"},
        "sensors": [{"id": 701, "parameter": {"name": "pm25", "units": "µg/m³"}}],
    }
    pipeline = IngestionPipeline(test_db_session)
    _, sensors = pipeline.upsert_station_and_sensors(location_payload)
    sensor = sensors[0]

    engine = PointInTimeReplayEngine(test_db_session)
    cutoff = datetime(2025, 3, 1, 12, 0, tzinfo=UTC)
    features = {
        "sensor_id": sensor.id,
        "cutoff_time": cutoff.isoformat(),
        "target_time": datetime(2025, 3, 1, 13, 0, tzinfo=UTC).isoformat(),
        "last_observed_pm25": 14.5,
        "age_hours": 0.5,
        "status": "publish",
    }

    snapshot = engine.record_feature_snapshot(
        sensor_id=sensor.id,
        decision_cutoff=cutoff,
        features=features,
    )

    assert snapshot.id is not None
    assert snapshot.sensor_id == sensor.id
    assert snapshot.decision_cutoff == cutoff

    # Verify queryability from DB
    persisted = test_db_session.scalars(
        select(ForecastFeatureSnapshot).where(ForecastFeatureSnapshot.sensor_id == sensor.id)
    ).first()
    assert persisted is not None
    assert persisted.feature_hash == snapshot.feature_hash
    assert "14.5" in persisted.features_json


def test_postgres_engine_pool_configuration(monkeypatch):
    """Verify PostgreSQL engine creation configures production pooling parameters."""
    from sensorguard.db import session as session_mod

    captured: dict = {}

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return "mock-pg-engine"

    monkeypatch.setattr(session_mod, "create_engine", fake_create_engine)
    eng = session_mod.create_db_engine("postgresql+psycopg2://user:pass@db:5432/sensorguard")
    assert eng == "mock-pg-engine"
    assert captured["kwargs"]["pool_size"] == 20
    assert captured["kwargs"]["max_overflow"] == 10
    assert captured["kwargs"]["pool_recycle"] == 1800
    assert captured["kwargs"]["pool_pre_ping"] is True
