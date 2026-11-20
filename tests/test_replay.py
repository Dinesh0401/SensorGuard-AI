"""Tests for PointInTimeReplayEngine preventing future data leakage."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sensorguard.db.schema import Base, Observation, Sensor, Station
from sensorguard.replay import PointInTimeReplayEngine


@pytest.fixture
def populated_session():
    """Populate database with time-sequenced observations and backfilled records."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session = Session(bind=engine)

    station = Station(
        provider="openaq",
        provider_location_id=1,
        name="Station-A",
        latitude=35.0,
        longitude=139.0,
    )
    session.add(station)
    session.flush()

    sensor = Sensor(
        station_id=station.id,
        provider_sensor_id=10,
        parameter="pm25",
        canonical_unit="µg/m³",
    )
    session.add(sensor)
    session.flush()

    # Create observations:
    # Hour 1: observed at 10:00, fetched at 10:05 (contemporaneous)
    # Hour 2: observed at 11:00, fetched at 11:05 (contemporaneous)
    # Hour 3: observed at 12:00, fetched at 17:00 (BACKFILLED late!)
    obs1 = Observation(
        sensor_id=sensor.id,
        observed_at=datetime(2025, 3, 1, 10, 0, tzinfo=UTC),
        fetched_at=datetime(2025, 3, 1, 10, 5, tzinfo=UTC),
        value=15.0,
        unit="µg/m³",
        quality_flag="VALID",
        source_status="HEALTHY",
    )
    obs2 = Observation(
        sensor_id=sensor.id,
        observed_at=datetime(2025, 3, 1, 11, 0, tzinfo=UTC),
        fetched_at=datetime(2025, 3, 1, 11, 5, tzinfo=UTC),
        value=18.0,
        unit="µg/m³",
        quality_flag="VALID",
        source_status="HEALTHY",
    )
    obs3 = Observation(
        sensor_id=sensor.id,
        observed_at=datetime(2025, 3, 1, 12, 0, tzinfo=UTC),
        fetched_at=datetime(2025, 3, 1, 17, 0, tzinfo=UTC),  # backfilled at 17:00!
        value=20.0,
        unit="µg/m³",
        quality_flag="VALID",
        source_status="HEALTHY",
    )
    session.add_all([obs1, obs2, obs3])
    session.commit()
    yield session
    session.close()


def test_prospective_replay_rejects_backfilled_future_data(populated_session: Session):
    engine = PointInTimeReplayEngine(populated_session)

    # Decision cutoff is 12:05 UTC.
    # At 12:05 UTC, obs1 (fetched 10:05) and obs2 (fetched 11:05) were present.
    # But obs3 was only fetched at 17:00, so it MUST NOT be visible!
    cutoff = datetime(2025, 3, 1, 12, 5, tzinfo=UTC)
    df, summary = engine.query_as_of(sensor_ids=[1], decision_cutoff=cutoff, mode="prospective")

    assert summary.mode == "prospective"
    assert summary.is_simulated_availability is False
    assert len(df) == 2
    assert set(df["value"]) == {15.0, 18.0}
    assert 20.0 not in df["value"].values


def test_retrospective_replay_simulates_publication_delay(populated_session: Session):
    engine = PointInTimeReplayEngine(populated_session)

    # In retrospective mode with 1-hour delay at cutoff 12:00:
    # Effective cutoff is 11:00 UTC (12:00 - 1h delay).
    # obs1 (10:00) and obs2 (11:00) are <= 11:00, obs3 (12:00) is excluded.
    cutoff = datetime(2025, 3, 1, 12, 0, tzinfo=UTC)
    df, summary = engine.query_as_of(
        sensor_ids=[1], decision_cutoff=cutoff, mode="retrospective", delay_hours=1.0
    )

    assert summary.mode == "retrospective"
    assert summary.is_simulated_availability is True
    assert summary.assumed_delay_hours == 1.0
    assert len(df) == 2
    assert (df["simulated_availability"] == True).all()


def test_point_in_time_forecast_features_no_target_leakage(populated_session: Session):
    engine = PointInTimeReplayEngine(populated_session)

    # Cutoff at 11:30 -> Target is 12:30.
    # Features must use only the last available observation at 11:00 (value = 18.0)
    cutoff = datetime(2025, 3, 1, 11, 30, tzinfo=UTC)
    features = engine.build_point_in_time_forecast_features(sensor_id=1, decision_cutoff=cutoff)

    assert features["last_observed_pm25"] == 18.0
    assert features["age_hours"] == 0.5
    assert features["status"] == "publish"
    assert features["target_time"] == datetime(2025, 3, 1, 12, 30, tzinfo=UTC).isoformat()
