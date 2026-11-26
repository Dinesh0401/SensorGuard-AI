"""Offline tests for Phase 8.2 research-grade shadow forecast evaluation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import create_engine, func, select
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
    reconcile_shadow_evaluation,
)


def test_prospective_shadow_forecast_generation_and_delayed_reconciliation(tmp_path):
    """Shadow predictions store before target arrives and honor minimum sample guard."""
    db_path = tmp_path / "shadow_m82.db"
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    t0 = datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)
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
            fetched_at=t0 + timedelta(hours=180),
            status_code=200,
            response_hash="c" * 64,
            payload_json="{}",
            record_count=320,
        )
        session.add_all([s1, s2, batch])
        session.flush()

        # Seed 160 continuous historical hours available cleanly on arrival before decision window
        for h in range(160):
            ts = t0 + timedelta(hours=h)
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

        df, coords = load_pilot_dataset_from_db(session)
        featured = build_spatiotemporal_features(df, coords)
        # Train shadow suite solely on first 130 hours
        hist_feat = featured[featured["timestamp"] < (t0 + timedelta(hours=130))].copy()
        models = fit_shadow_suite(hist_feat, coords)

        # Generate prospective shadow forecasts at hours 140..145 BEFORE future arrival
        # Targets are 141..146
        cutoffs = [t0 + timedelta(hours=h) for h in range(140, 145)]
        runs = generate_prospective_shadow_forecasts(
            session=session,
            decision_cutoffs=cutoffs,
            fitted_models=models,
            station_coords=coords,
            data_mode="shadow_live",
        )
        assert len(runs) == 5
        assert all(r.execution_mode == "shadow" for r in runs)
        assert session.scalar(select(func.count(ForecastFeatureSnapshot.id))) == 10  # 5 cutoffs x 2 stations
        assert session.scalar(select(func.count(ForecastRecord.id))) == 50  # 10 x 5 models

        # Verify zero leakage: check snapshot created_at <= target_time - 1h
        snaps = list(session.scalars(select(ForecastFeatureSnapshot)).all())
        assert all(s.decision_cutoff < s.target_time for s in snaps)

        # Evaluate with min_matched_outcomes=100 -> must NOT claim sufficient calibration evidence yet!
        eval_insufficient = reconcile_shadow_evaluation(session, min_matched_outcomes=100)
        assert eval_insufficient["status"] == "INSUFFICIENT_OBSERVED_OUTCOMES"
        assert eval_insufficient["calibration_claim_eligible"] is False
        assert eval_insufficient["models"]["ADAPTIVE-SAGE-LIGHTGBM"]["matched_count"] == 10
        assert eval_insufficient["models"]["ADAPTIVE-SAGE-LIGHTGBM"]["calibration_claim_eligible"] is False

        # Verify threshold met once >=10 outcomes threshold passed
        eval_ok = reconcile_shadow_evaluation(session, min_matched_outcomes=10)
        assert eval_ok["status"] == "EVALUATED"
        assert eval_ok["calibration_claim_eligible"] is True
        for m_key in [
            "PERSISTENCE-SPLIT",
            "LOCALLY-WEIGHTED-MAR",
            "LIGHTGBM-CQR",
            "SAGE-STATIC-LIGHTGBM",
            "ADAPTIVE-SAGE-LIGHTGBM",
        ]:
            m_stats = eval_ok["models"][m_key]
            assert m_stats["matched_count"] == 10
            assert m_stats["published_picp"] is not None
            assert m_stats["published_winkler"] is not None

    engine.dispose()
