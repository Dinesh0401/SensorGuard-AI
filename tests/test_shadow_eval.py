"""Milestone 8.6 tests for prospective timestamp invariants, backdating rejection, idempotency, and reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from sensorguard.db.schema import (
    Base,
    ForecastFeatureSnapshot,
    ForecastRecord,
    Observation,
    RawIngestBatch,
    Sensor,
    Station,
)
from sensorguard.models.data import build_spatiotemporal_features, load_pilot_dataset_from_db
from sensorguard.shadow_eval import (
    fit_shadow_suite,
    generate_prospective_shadow_forecasts,
    generate_retrospective_replay_forecasts,
    reconcile_shadow_evaluation,
)


def _seed_shadow_db(tmp_path, end_hour_utc: datetime, n_hours: int = 160):
    """Create isolated SQLite DB with n_hours ending cleanly before or at end_hour_utc."""
    db_path = tmp_path / "shadow_eval_m86.db"
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    t_start = end_hour_utc - timedelta(hours=n_hours - 1)
    rng = np.random.default_rng(77)

    with factory() as session:
        st1 = Station(
            provider="openaq",
            provider_location_id=154,
            name="Marylebone Shadow",
            latitude=51.5225,
            longitude=-0.1546,
            country="GB",
            timezone_name="Europe/London",
        )
        st2 = Station(
            provider="openaq",
            provider_location_id=157,
            name="Bexley Shadow",
            latitude=51.4660,
            longitude=0.1849,
            country="GB",
            timezone_name="Europe/London",
        )
        session.add_all([st1, st2])
        session.flush()

        s1 = Sensor(station_id=st1.id, provider_sensor_id=246, parameter="pm25")
        s2 = Sensor(station_id=st2.id, provider_sensor_id=254, parameter="pm25")
        batch = RawIngestBatch(
            provider="openaq",
            endpoint="/v3/sensors/mock/hours",
            fetched_at=end_hour_utc + timedelta(minutes=10),
            status_code=200,
            response_hash="c" * 64,
            payload_json="{}",
            record_count=n_hours * 2,
        )
        session.add_all([s1, s2, batch])
        session.flush()

        for h in range(n_hours):
            ts = t_start + timedelta(hours=h)
            val1 = float(10.0 + 3.0 * np.sin(h / 8.0) + rng.normal(0, 0.8))
            val2 = float(9.0 + 2.5 * np.cos(h / 8.0) + rng.normal(0, 0.8))
            session.add_all(
                [
                    Observation(
                        sensor_id=s1.id,
                        raw_batch_id=batch.id,
                        observed_at=ts,
                        fetched_at=ts + timedelta(minutes=10),
                        persisted_at=ts + timedelta(minutes=10),
                        value=val1,
                        quality_flag="VALID",
                        source_status="HEALTHY",
                        version=1,
                    ),
                    Observation(
                        sensor_id=s2.id,
                        raw_batch_id=batch.id,
                        observed_at=ts,
                        fetched_at=ts + timedelta(minutes=10),
                        persisted_at=ts + timedelta(minutes=10),
                        value=val2,
                        quality_flag="VALID",
                        source_status="HEALTHY",
                        version=1,
                    ),
                ]
            )
        session.commit()
        return engine, factory, t_start, s1.id, s2.id, batch.id


def test_backdated_prospective_forecasts_rejected_and_retrospective_isolated(tmp_path) -> None:
    """Prove post-target forecasts cannot be marked prospective and retrospective evidence stays separate."""
    hist_end = datetime(2026, 4, 7, 15, 0, tzinfo=UTC)
    engine, factory, t_start, _, _, _ = _seed_shadow_db(tmp_path, end_hour_utc=hist_end, n_hours=160)

    with factory() as session:
        df, coords = load_pilot_dataset_from_db(session)
        featured = build_spatiotemporal_features(df, coords)
        hist_feat = featured[featured["timestamp"] < (t_start + timedelta(hours=130))].copy()
        models = fit_shadow_suite(hist_feat, coords)

        # Historical cutoffs whose targets are ALREADY in past and stored in DB MUST FAIL prospective check
        past_cutoffs = [t_start + timedelta(hours=h) for h in range(140, 145)]
        with pytest.raises(ValueError, match="Backdated prospective violation"):
            generate_prospective_shadow_forecasts(
                session=session,
                decision_cutoffs=past_cutoffs,
                fitted_models=models,
                station_coords=coords,
                data_mode="shadow_live",
            )

        # Must run explicitly via retrospective replay generator
        retro_runs = generate_retrospective_replay_forecasts(
            session=session,
            decision_cutoffs=past_cutoffs,
            fitted_models=models,
            station_coords=coords,
            data_mode="archived_historical",
        )
        assert len(retro_runs) == 5
        assert all(r.evaluation_mode == "retrospective_replay" for r in retro_runs)
        # Actual wall-clock generation & persistence times are NOT backdated to April historical cutoff!
        assert all(r.generated_at > hist_end for r in retro_runs)
        assert all(r.persisted_at > hist_end for r in retro_runs)
        assert all(r.model_created_at is not None and r.artifact_immutable_ref for r in retro_runs)

        # Idempotency key prevents duplicate ForecastRuns cleanly
        with pytest.raises(ValueError, match="Duplicate ForecastRun"):
            generate_retrospective_replay_forecasts(
                session=session,
                decision_cutoffs=past_cutoffs,
                fitted_models=models,
                station_coords=coords,
                data_mode="archived_historical",
                idempotency_behavior="raise",
            )

        # Prospective evaluation MUST report 0 truly_prospective_count and segregate retrospective runs
        prosp_view = reconcile_shadow_evaluation(
            session, min_matched_outcomes=10, min_unique_hours=2, evaluation_mode="prospective_shadow"
        )
        assert prosp_view["truly_prospective_count"] == 0
        assert prosp_view["calibration_claim_eligible"] is False
        assert prosp_view["retrospective_run_count"] == 5
        assert prosp_view["retrospective_summary"]["status"] == "EVALUATED"
        assert prosp_view["retrospective_summary"]["models"]["ADAPTIVE-SAGE-LIGHTGBM"]["matched_count"] == 10

    engine.dispose()


def test_genuine_prospective_shadow_forecasts_before_future_target_arrival(tmp_path) -> None:
    """Persist genuine prospective forecasts BEFORE real wall-clock target arrives, then reconcile."""
    now_hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    # Seed 160 hours ending at now_hour; future target = now_hour + 1h is strictly in the future!
    engine, factory, t_start, s1_id, s2_id, batch_id = _seed_shadow_db(
        tmp_path, end_hour_utc=now_hour, n_hours=160
    )

    with factory() as session:
        df, coords = load_pilot_dataset_from_db(session)
        featured = build_spatiotemporal_features(df, coords)
        hist_feat = featured[featured["timestamp"] < (t_start + timedelta(hours=130))].copy()
        models = fit_shadow_suite(hist_feat, coords)

        future_target = now_hour + timedelta(hours=1)
        runs = generate_prospective_shadow_forecasts(
            session=session,
            decision_cutoffs=[now_hour],
            fitted_models=models,
            station_coords=coords,
            data_mode="shadow_live",
        )
        assert len(runs) == 5
        assert all(r.evaluation_mode == "prospective_shadow" for r in runs)
        assert all(r.generated_at <= r.persisted_at < future_target for r in runs)

        recs = list(session.scalars(select(ForecastRecord)).all())
        assert len(recs) == 10  # 2 stations x 5 models
        assert all(r.persisted_at < r.target_time for r in recs)
        assert session.scalar(select(func.count(ForecastFeatureSnapshot.id))) == 2

        # Repeated identical call returns same runs cleanly without duplicating records
        dup_runs = generate_prospective_shadow_forecasts(
            session=session,
            decision_cutoffs=[now_hour],
            fitted_models=models,
            station_coords=coords,
            data_mode="shadow_live",
            idempotency_behavior="return",
        )
        assert {r.id for r in dup_runs} == {r.id for r in runs}
        assert session.scalar(select(func.count(ForecastRecord.id))) == 10

        # DB uniqueness constraint blocks duplicate (run_id, station_id, target_time) insert
        with pytest.raises(IntegrityError):
            session.add(
                ForecastRecord(
                    run_id=runs[0].id,
                    station_id=recs[0].station_id,
                    sensor_id=recs[0].sensor_id,
                    decision_cutoff=recs[0].decision_cutoff,
                    target_time=recs[0].target_time,
                    point_forecast=9.9,
                    status="PUBLISH",
                )
            )
            session.flush()
        session.rollback()

        # Simulate target arriving AFTER forecast persistence -> evaluate subgroups & matched parity
        obs_persist = max(r.persisted_at for r in recs) + timedelta(seconds=5)
        # Note SQLite stores naive datetimes, normalize via utc replace if needed
        if obs_persist.tzinfo is None:
            obs_persist = obs_persist.replace(tzinfo=UTC)
        session.add_all(
            [
                Observation(
                    sensor_id=s1_id,
                    raw_batch_id=batch_id,
                    observed_at=future_target,
                    fetched_at=future_target + timedelta(hours=2),  # delayed arrival (>1.5h)
                    persisted_at=obs_persist,
                    value=22.5,  # high pollution subgroup (>=15)
                    quality_flag="VALID",
                    source_status="HEALTHY",
                    version=1,
                ),
                Observation(
                    sensor_id=s2_id,
                    raw_batch_id=batch_id,
                    observed_at=future_target,
                    fetched_at=future_target + timedelta(minutes=15),
                    persisted_at=obs_persist,
                    value=11.0,
                    quality_flag="VALID",
                    source_status="HEALTHY",
                    version=1,
                ),
            ]
        )
        session.commit()

        # Insufficient independent hours (1 < 24) & matched (2 < 100) MUST reject calibration claim
        res_gate = reconcile_shadow_evaluation(
            session, min_matched_outcomes=100, min_unique_hours=24, evaluation_mode="prospective_shadow"
        )
        assert res_gate["status"] == "INSUFFICIENT_OBSERVED_OUTCOMES"
        assert res_gate["calibration_claim_eligible"] is False
        assert res_gate["adaptive_sage_promotion_eligible"] is False
        assert res_gate["truly_prospective_count"] == 10
        assert res_gate["backdated_violation_count"] == 0

        # Check rich subgroup + matched-availability metrics under test thresholds (min=2, hours=1)
        res_ok = reconcile_shadow_evaluation(
            session, min_matched_outcomes=2, min_unique_hours=1, evaluation_mode="prospective_shadow"
        )
        assert res_ok["status"] == "EVALUATED"
        assert "LOCALLY-WEIGHTED-MAR" in res_ok["matched_availability_vs_baselines"]
        adap = res_ok["models"]["ADAPTIVE-SAGE-LIGHTGBM"]
        assert adap["matched_count"] == 2
        assert adap["delayed_arrival_count"] == 1
        assert adap["subgroups"]["high_pollution"]["n_matched"] >= 1
        assert adap["unique_target_hours"] == 1

        # Tamper test: backdated record persisted AFTER target_time is quarantined as violation!
        recs_reload = list(session.scalars(select(ForecastRecord)).all())
        recs_reload[0].persisted_at = recs_reload[0].target_time + timedelta(minutes=5)
        session.commit()
        res_tamper = reconcile_shadow_evaluation(
            session, min_matched_outcomes=2, min_unique_hours=1, evaluation_mode="prospective_shadow"
        )
        assert res_tamper["backdated_violation_count"] == 1
        assert res_tamper["calibration_claim_eligible"] is False

    engine.dispose()
