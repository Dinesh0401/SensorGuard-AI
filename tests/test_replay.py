"""Tests for PointInTimeReplayEngine preventing future data leakage and recovering point-in-time versions."""

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
    # Hour 1: observed at 10:00, fetched at 10:05 (contemporaneous, v1)
    # Hour 2: observed at 11:00, fetched at 11:05 (contemporaneous, v1)
    # Hour 3: observed at 12:00, fetched at 17:00 (BACKFILLED late!, v1)
    obs1 = Observation(
        sensor_id=sensor.id,
        observed_at=datetime(2025, 3, 1, 10, 0, tzinfo=UTC),
        fetched_at=datetime(2025, 3, 1, 10, 5, tzinfo=UTC),
        value=15.0,
        unit="µg/m³",
        quality_flag="VALID",
        source_status="HEALTHY",
        version=1,
    )
    obs2 = Observation(
        sensor_id=sensor.id,
        observed_at=datetime(2025, 3, 1, 11, 0, tzinfo=UTC),
        fetched_at=datetime(2025, 3, 1, 11, 5, tzinfo=UTC),
        value=18.0,
        unit="µg/m³",
        quality_flag="VALID",
        source_status="HEALTHY",
        version=1,
    )
    obs3 = Observation(
        sensor_id=sensor.id,
        observed_at=datetime(2025, 3, 1, 12, 0, tzinfo=UTC),
        fetched_at=datetime(2025, 3, 1, 17, 0, tzinfo=UTC),  # backfilled at 17:00!
        value=20.0,
        unit="µg/m³",
        quality_flag="VALID",
        source_status="HEALTHY",
        version=1,
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


def test_prospective_replay_enforces_both_fetched_and_observed_at_cutoff(populated_session: Session):
    engine = PointInTimeReplayEngine(populated_session)

    # Add a corrupt record fetched at 10:00 claiming observed_at = 14:00 (future relative to cutoff 11:00)
    future_obs = Observation(
        sensor_id=1,
        observed_at=datetime(2025, 3, 1, 14, 0, tzinfo=UTC),
        fetched_at=datetime(2025, 3, 1, 10, 0, tzinfo=UTC),
        value=99.0,
        unit="µg/m³",
        quality_flag="VALID",
        source_status="HEALTHY",
        version=1,
    )
    populated_session.add(future_obs)
    populated_session.commit()

    # Query as of 11:00:
    # future_obs has fetched_at (10:00) <= 11:00, BUT observed_at (14:00) > 11:00 and observed_at > fetched_at!
    # Prospective replay must reject it.
    cutoff = datetime(2025, 3, 1, 11, 0, tzinfo=UTC)
    df, _ = engine.query_as_of(sensor_ids=[1], decision_cutoff=cutoff, mode="prospective")

    assert 99.0 not in df["value"].values


def test_point_in_time_version_recovery(populated_session: Session):
    engine = PointInTimeReplayEngine(populated_session)

    # Insert a revised correction for Hour 2 (11:00) fetched at 15:00 UTC (version 2, value corrected to 24.0)
    revised_obs2 = Observation(
        sensor_id=1,
        observed_at=datetime(2025, 3, 1, 11, 0, tzinfo=UTC),
        fetched_at=datetime(2025, 3, 1, 15, 0, tzinfo=UTC),
        value=24.0,  # corrected from 18.0
        unit="µg/m³",
        quality_flag="VALID",
        source_status="HEALTHY",
        version=2,
    )
    populated_session.add(revised_obs2)
    populated_session.commit()

    # As-of Cutoff 12:00: Version 2 was NOT yet fetched (fetched at 15:00).
    # Replay MUST return Version 1 (value 18.0).
    df_early, _ = engine.query_as_of(
        sensor_ids=[1], decision_cutoff=datetime(2025, 3, 1, 12, 0, tzinfo=UTC), mode="prospective"
    )
    obs_at_11 = df_early[df_early["observed_at"] == datetime(2025, 3, 1, 11, 0, tzinfo=UTC)].iloc[0]
    assert obs_at_11["version"] == 1
    assert obs_at_11["value"] == 18.0

    # As-of Cutoff 16:00: Version 2 HAS been fetched by 15:00.
    # Replay MUST return Version 2 (value 24.0).
    df_late, _ = engine.query_as_of(
        sensor_ids=[1], decision_cutoff=datetime(2025, 3, 1, 16, 0, tzinfo=UTC), mode="prospective"
    )
    obs_at_11_latest = df_late[df_late["observed_at"] == datetime(2025, 3, 1, 11, 0, tzinfo=UTC)].iloc[0]
    assert obs_at_11_latest["version"] == 2
    assert obs_at_11_latest["value"] == 24.0


def test_build_replay_timeline_explicit_missing_and_late_slots(populated_session: Session):
    engine = PointInTimeReplayEngine(populated_session)

    # Timeline spanning 10:00 to 13:00 (4 hourly slots: 10, 11, 12, 13)
    # 10:00 -> On-time HEALTHY (obs1 fetched 10:05)
    # 11:00 -> On-time HEALTHY (obs2 fetched 11:05)
    # 12:00 -> Late reporting (obs3 fetched 17:00, 5 hours delay > 3h threshold!)
    # 13:00 -> Absent / unobserved slot -> NO_REPORT / MISSING_SLOT
    start = datetime(2025, 3, 1, 10, 0, tzinfo=UTC)
    end = datetime(2025, 3, 1, 18, 0, tzinfo=UTC)

    timeline = engine.build_replay_timeline(sensor_id=1, start_time=start, end_time=end)

    assert len(timeline) >= 4

    t10 = timeline[timeline["observed_at"] == datetime(2025, 3, 1, 10, 0, tzinfo=UTC)].iloc[0]
    assert t10["source_status"] == "HEALTHY"
    assert t10["value"] == 15.0

    t12 = timeline[timeline["observed_at"] == datetime(2025, 3, 1, 12, 0, tzinfo=UTC)].iloc[0]
    assert t12["source_status"] == "LATE"  # 5 hours delay detected!
    assert t12["value"] == 20.0

    t13 = timeline[timeline["observed_at"] == datetime(2025, 3, 1, 13, 0, tzinfo=UTC)].iloc[0]
    assert t13["source_status"] == "NO_REPORT"  # Missing slot explicitly identified
    assert t13["quality_flag"] == "MISSING_SLOT"


def test_point_in_time_forecast_features_no_target_leakage(populated_session: Session):
    engine = PointInTimeReplayEngine(populated_session)

    cutoff = datetime(2025, 3, 1, 11, 30, tzinfo=UTC)
    features = engine.build_point_in_time_forecast_features(sensor_id=1, decision_cutoff=cutoff)

    assert features["last_observed_pm25"] == 18.0
    assert features["age_hours"] == 0.5
    assert features["status"] == "publish"
    assert features["target_time"] == datetime(2025, 3, 1, 12, 30, tzinfo=UTC).isoformat()
