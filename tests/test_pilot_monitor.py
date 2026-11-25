"""Tests for Milestone 7.5 opt-in live pilot quota guard and delayed ground-truth tracking."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from sensorguard.db.schema import (
    Base,
    ForecastRecord,
    ForecastRun,
    Observation,
    RawIngestBatch,
    Sensor,
    Station,
)
from sensorguard.pilot_monitor import (
    BoundedPilotConfig,
    PilotQuotaTracker,
    reconcile_delayed_ground_truth,
    run_bounded_opt_in_poll,
)


def _setup_sqlite_pilot(tmp_path):
    db_path = tmp_path / "pilot_m75.db"
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        st = Station(
            provider="openaq",
            provider_location_id=141,
            name="London Harlington - Test",
            latitude=51.4888,
            longitude=-0.4416,
            country="GB",
            timezone_name="Europe/London",
        )
        session.add(st)
        session.flush()
        sensor = Sensor(
            station_id=st.id,
            provider_sensor_id=5001,
            parameter="pm25",
            canonical_unit="µg/m³",
            averaging_period_hours=1.0,
        )
        session.add(sensor)
        session.commit()
    return engine, factory


def test_opt_in_poll_blocks_default_and_enforces_daily_quota(tmp_path):
    """Default opt-out makes 0 network calls; opt-in caps requests by daily quota."""
    engine, factory = _setup_sqlite_pilot(tmp_path)
    calls: list[str] = []
    now_t = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "datetime": {"utc": "2026-03-01T11:00:00Z"},
                        "value": 12.5,
                        "parameter": {"name": "pm25", "units": "µg/m³"},
                    }
                ]
            },
        )

    transport = httpx.MockTransport(mock_handler)
    quota = PilotQuotaTracker(max_requests_per_day=1)

    # 1. Opt-out prevents any request
    res_off = run_bounded_opt_in_poll(
        session_factory=factory,
        config=BoundedPilotConfig(opt_in_enabled=False),
        quota=quota,
        transport=transport,
        now_utc=now_t,
    )
    assert res_off["status"] == "SKIPPED_OPT_OUT"
    assert len(calls) == 0

    # 2. First opt-in call succeeds within 1-request budget and passes bounded lookback window
    res_on = run_bounded_opt_in_poll(
        session_factory=factory,
        config=BoundedPilotConfig(opt_in_enabled=True, lookback_hours=48, max_requests_per_day=1),
        quota=quota,
        transport=transport,
        now_utc=now_t,
    )
    assert res_on["status"] == "SUCCESS"
    assert res_on["records_inserted"] == 1
    assert len(calls) == 1
    assert "datetime_from=2026-02-27T12%3A00%3A00Z" in calls[0]

    # 3. Second call same UTC day hits daily quota limit cleanly without issuing HTTP call
    res_exhausted = run_bounded_opt_in_poll(
        session_factory=factory,
        config=BoundedPilotConfig(opt_in_enabled=True, max_requests_per_day=1),
        quota=quota,
        transport=transport,
        now_utc=now_t + timedelta(minutes=30),
    )
    assert res_exhausted["status"] == "QUOTA_EXHAUSTED"
    assert len(calls) == 1
    engine.dispose()


def test_delayed_ground_truth_reconciliation_isolates_missing_labels(tmp_path):
    """Unmatched missing labels stay explicitly tracked and never inflate coverage."""
    engine, factory = _setup_sqlite_pilot(tmp_path)
    t0 = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 3, 1, 11, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)

    with factory() as session:
        st = session.scalars(select(Station)).one()
        sensor = session.scalars(select(Sensor)).one()
        run = ForecastRun(
            model_id="sage_pilot_q1_2024",
            model_version="1.0.0",
            model_sha256="a" * 64,
            algorithm="SAGE-LIGHTGBM",
            nominal_coverage=0.90,
            decision_cutoff=t0,
        )
        session.add(run)
        session.flush()

        # Forecast 1: arrived delayed (5h late) and covered [5, 15] vs truth 12.0
        # Forecast 2: abstained, truth arrived (18.0) -> tracked as abstained_matched
        # Forecast 3: missing label -> never arrives in Observations
        for idx, (cutoff, target, lo, hi, status) in enumerate(
            [
                (t0 - timedelta(hours=1), t0, 5.0, 15.0, "PUBLISH"),
                (t0, t1, None, None, "ABSTAIN"),
                (t1, t2, 4.0, 12.0, "PUBLISH"),
            ]
        ):
            session.add(
                ForecastRecord(
                    run_id=run.id,
                    station_id=st.id,
                    sensor_id=sensor.id,
                    decision_cutoff=cutoff,
                    target_time=target,
                    point_forecast=10.0 + idx,
                    lower_bound=lo,
                    upper_bound=hi,
                    nominal_coverage=0.90,
                    status=status,
                    abstain_reason="NONE" if status == "PUBLISH" else "stale_sensor_history",
                )
            )

        batch = RawIngestBatch(
            provider="openaq",
            endpoint="/mock",
            fetched_at=t0 + timedelta(hours=5),
            status_code=200,
            response_hash="b" * 64,
            payload_json="{}",
            record_count=2,
        )
        session.add(batch)
        session.flush()
        session.add_all(
            [
                Observation(
                    sensor_id=sensor.id,
                    raw_batch_id=batch.id,
                    observed_at=t0,
                    fetched_at=t0 + timedelta(hours=5),  # 5h late arrival
                    value=12.0,
                    unit="µg/m³",
                    quality_flag="VALID",
                    source_status="HEALTHY",
                    version=1,
                ),
                Observation(
                    sensor_id=sensor.id,
                    raw_batch_id=batch.id,
                    observed_at=t1,
                    fetched_at=t1 + timedelta(minutes=15),
                    value=18.0,
                    unit="µg/m³",
                    quality_flag="VALID",
                    source_status="HEALTHY",
                    version=1,
                ),
            ]
        )
        session.commit()

        report = reconcile_delayed_ground_truth(session, run_id=run.id, delay_threshold_hours=1.5)
        assert report["total_forecasts"] == 3
        assert report["matched_count"] == 2
        assert report["unmatched_missing_truth_count"] == 1
        assert report["delayed_arrival_count"] == 1
        assert report["published_matched_count"] == 1
        assert report["published_picp"] == 1.0
        assert report["abstained_matched_count"] == 1
        assert report["mnar_censoring_warning"] is True
    engine.dispose()
