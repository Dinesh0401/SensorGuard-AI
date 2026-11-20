"""Tests for database models, Alembic migrations, and idempotent ingestion."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from sensorguard.db.schema import Base, Observation, RawIngestBatch
from sensorguard.ingest import IngestionPipeline, ObservationValidator
from sensorguard.providers import OpenAQClient


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

    # Observed at must equal period end
    assert rec.observed_at.hour == 14
    assert rec.period_start.hour == 13
    assert rec.period_end.hour == 14
    assert rec.received_at is None  # Not fabricated
    assert rec.unit == "µg/m³"  # Normalized
    assert rec.quality_flag == "VALID"
    assert rec.source_status == "HEALTHY"


def test_observation_validator_out_of_bounds():
    raw_record = {
        "datetime": {"utc": "2025-02-10T15:00:00Z"},
        "value": 1500.0,  # Unphysical PM2.5 spike > 1000
        "parameter": {"name": "pm25", "units": "µg/m³"},
    }
    rec = ObservationValidator.validate_openaq_hourly_record(
        raw_record, sensor_db_id=1, fetched_at=datetime.now(UTC)
    )
    assert rec.quality_flag == "OUT_OF_BOUNDS"
    assert rec.source_status == "INVALID"


def test_idempotent_ingestion_and_provenance(test_db_session: Session):
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
            {
                "datetime": {"utc": "2025-02-01T11:00:00Z"},
                "period": {
                    "datetimeFrom": {"utc": "2025-02-01T10:00:00Z"},
                    "datetimeTo": {"utc": "2025-02-01T11:00:00Z"},
                },
                "value": 24.5,
                "parameter": {"name": "pm25", "units": "µg/m³"},
            },
        ]
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_hourly_response)

    client = OpenAQClient(api_key="mock-key", transport=httpx.MockTransport(mock_handler))
    pipeline = IngestionPipeline(test_db_session)

    # 1. Register station and sensors
    station, sensors = pipeline.upsert_station_and_sensors(location_payload)
    assert station.id is not None
    assert len(sensors) == 1
    sensor = sensors[0]

    # 2. First ingestion run
    res1 = pipeline.ingest_sensor_hours(
        sensor_db_id=sensor.id,
        provider_sensor_id=sensor.provider_sensor_id,
        client=client,
        max_pages=1,
    )
    assert res1.records_fetched == 2
    assert res1.records_inserted == 2
    assert res1.records_deduplicated == 0
    assert res1.batches_saved == 1

    # Verify database state
    observations = test_db_session.scalars(select(Observation)).all()
    assert len(observations) == 2
    batches = test_db_session.scalars(select(RawIngestBatch)).all()
    assert len(batches) == 1
    assert len(batches[0].response_hash) == 64

    # 3. Second ingestion run with identical data -> Idempotent, deduplicated!
    res2 = pipeline.ingest_sensor_hours(
        sensor_db_id=sensor.id,
        provider_sensor_id=sensor.provider_sensor_id,
        client=client,
        max_pages=1,
    )
    assert res2.records_fetched == 2
    assert res2.records_inserted == 0
    assert res2.records_deduplicated == 2
    # Total observations unchanged
    assert len(test_db_session.scalars(select(Observation)).all()) == 2
