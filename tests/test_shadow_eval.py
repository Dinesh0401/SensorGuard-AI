"""Milestone 8.6 tests for prospective timestamp invariants, backdating rejection, idempotency, and reconciliation."""

from __future__ import annotations

import concurrent.futures
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from sensorguard.db.schema import (
    Base,
    ForecastFeatureSnapshot,
    ForecastRecord,
    ForecastRun,
    Observation,
    RawIngestBatch,
    Sensor,
    Station,
)
from sensorguard.models.data import build_spatiotemporal_features, load_pilot_dataset_from_db
from sensorguard.models.shadow_artifacts import SHADOW_CODE_VERSION, verify_and_load_shadow_artifact
from sensorguard.shadow_eval import (
    SHADOW_MODEL_SPECS,
    _predict_single_shadow_model,
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
    wall_now = datetime.now(UTC)
    now_hour = wall_now.replace(minute=0, second=0, microsecond=0)
    # Keep deterministic >= 10m lead even if pytest executes at :55-:59 of the UTC hour
    if ((now_hour + timedelta(hours=1)) - wall_now).total_seconds() < 600:
        now_hour = now_hour + timedelta(hours=1)
    # Seed 160 hours ending at now_hour; future target = now_hour + 1h is strictly in the future!
    engine, factory, t_start, s1_id, s2_id, batch_id = _seed_shadow_db(
        tmp_path, end_hour_utc=now_hour, n_hours=160
    )

    with factory() as session:
        df, coords = load_pilot_dataset_from_db(session)
        featured = build_spatiotemporal_features(df, coords)
        hist_feat = featured[featured["timestamp"] < (t_start + timedelta(hours=130))].copy()
        models = fit_shadow_suite(hist_feat, coords, artifact_dir=tmp_path / "artifacts")

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
        assert all(
            r.training_completed_at is not None and r.code_version == SHADOW_CODE_VERSION for r in runs
        )

        recs = list(session.scalars(select(ForecastRecord)).all())
        assert len(recs) == 10  # 2 stations x 5 models
        assert all(r.persisted_at < r.target_time for r in recs)
        assert all(r.artifact_sha256 is not None and len(r.artifact_sha256) == 64 for r in recs)
        assert all(r.effective_lead_seconds is not None and r.effective_lead_seconds >= 300.0 for r in recs)
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
        assert res_tamper["lead_time_violation_count"] == 1
        assert res_tamper["calibration_claim_eligible"] is False

    engine.dispose()


def test_shadow_artifact_integrity_roundtrip_and_tamper_rejection(tmp_path) -> None:
    """Verify genuine JSON artifact SHA-256 verification, numeric parity, and strict rejection cases (Part A)."""
    end_hr = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    engine, factory, t_start, _, _, _ = _seed_shadow_db(tmp_path, end_hour_utc=end_hr, n_hours=140)
    with factory() as session:
        df, coords = load_pilot_dataset_from_db(session)
        featured = build_spatiotemporal_features(df, coords)
        hist_feat = featured[featured["timestamp"] < (t_start + timedelta(hours=110))].copy()
        art_dir = tmp_path / "artifacts_roundtrip"
        created_at = end_hr + timedelta(hours=2)
        suite = fit_shadow_suite(hist_feat, coords, artifact_dir=art_dir, model_created_at=created_at)

        # 1. Each model gets its own unique immutable file SHA-256 and matches prediction numerically
        seen_shas: set[str] = set()
        sample_row = featured.iloc[[-1]].copy()
        for m_key, spec in SHADOW_MODEL_SPECS.items():
            prov = suite["model_provenance"][m_key]
            art_file = Path(prov["artifact_path"])
            assert art_file.suffix == ".json"
            assert art_file.exists()
            file_sha = hashlib.sha256(art_file.read_bytes()).hexdigest()
            assert prov["artifact_sha256"] == file_sha
            assert file_sha not in seen_shas
            seen_shas.add(file_sha)

            loaded_model, meta = verify_and_load_shadow_artifact(
                provenance_entry=prov,
                expected_model_key=m_key,
                expected_spec=spec,
                expected_feature_columns=suite["feature_columns"],
                expected_nominal_coverage=suite["nominal_coverage"],
                evaluation_time=created_at + timedelta(minutes=1),
            )
            assert meta["artifact_sha256"] == file_sha
            live_out = _predict_single_shadow_model(m_key, suite, sample_row)
            loaded_out = _predict_single_shadow_model(m_key, {m_key: loaded_model}, sample_row)
            assert live_out[0] == pytest.approx(loaded_out[0], abs=1e-5)
            assert live_out[1] == pytest.approx(loaded_out[1], abs=1e-5)
            assert live_out[2] == pytest.approx(loaded_out[2], abs=1e-5)

        cutoff = end_hr + timedelta(hours=5)
        gen_time = end_hr + timedelta(hours=5, minutes=10)

        # 2. Reject untrusted non-JSON artifact (.joblib)
        bad_suite = {**suite, "model_provenance": {k: dict(v) for k, v in suite["model_provenance"].items()}}
        fake_pkl = tmp_path / "evil.joblib"
        fake_pkl.write_bytes(b"not-json")
        bad_suite["model_provenance"]["sage_static"]["artifact_path"] = str(fake_pkl)
        with pytest.raises(ValueError, match="non-JSON artifact"):
            generate_prospective_shadow_forecasts(
                session, [cutoff], bad_suite, coords, now_fn=lambda: gen_time
            )

        # 3. Reject tampered artifact bytes
        tamper_suite = {
            **suite,
            "model_provenance": {k: dict(v) for k, v in suite["model_provenance"].items()},
        }
        target_file = Path(tamper_suite["model_provenance"]["sage_adaptive"]["artifact_path"])
        orig_bytes = target_file.read_bytes()
        target_file.write_bytes(orig_bytes.replace(b'"nominal_coverage":0.9', b'"nominal_coverage":0.8'))
        with pytest.raises(ValueError, match="SHA-256 verification failed"):
            generate_prospective_shadow_forecasts(
                session, [cutoff], tamper_suite, coords, now_fn=lambda: gen_time
            )
        target_file.write_bytes(orig_bytes)

        # 4. Reject missing artifact file
        miss_suite = {**suite, "model_provenance": {k: dict(v) for k, v in suite["model_provenance"].items()}}
        miss_suite["model_provenance"]["persistence"]["artifact_path"] = str(tmp_path / "missing.json")
        with pytest.raises(ValueError, match="missing"):
            generate_prospective_shadow_forecasts(
                session, [cutoff], miss_suite, coords, now_fn=lambda: gen_time
            )

        # 5. Reject incompatible feature schema
        incompat_suite = {**suite, "feature_columns": suite["feature_columns"] + ["injected_leak_col"]}
        with pytest.raises(ValueError, match="Incompatible feature schema"):
            generate_prospective_shadow_forecasts(
                session, [cutoff], incompat_suite, coords, now_fn=lambda: gen_time
            )

        # 6. Reject training on labels unavailable before model_created_at
        hist_future_label = hist_feat.copy()
        hist_future_label["persisted_at"] = created_at + timedelta(hours=2)
        with pytest.raises(ValueError, match="Causal training violation"):
            fit_shadow_suite(
                hist_future_label,
                coords,
                artifact_dir=tmp_path / "bad_causal",
                model_created_at=created_at,
            )

    engine.dispose()


def test_prospective_lead_time_boundaries_delayed_commit_and_concurrency(tmp_path) -> None:
    """Verify min_lead_seconds enforcement, delayed commit aborts, post-cutoff honesty & worker races (Part C)."""
    end_hr = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    db_file = tmp_path / "timing_conc.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    # Seed into exact db_file cleanly via helper
    src_eng, _, t_start, s1_id, _, batch_id = _seed_shadow_db(tmp_path, end_hour_utc=end_hr, n_hours=150)
    src_eng.dispose()
    # Point at seeded shadow_eval_m86.db directly with WAL MVCC enabled so concurrent ingestion writers proceed cleanly
    seeded_db = tmp_path / "shadow_eval_m86.db"
    engine.dispose()
    engine = create_engine(f"sqlite:///{seeded_db}", connect_args={"check_same_thread": False})
    with engine.connect() as _conn:
        _conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with factory() as session:
        df, coords = load_pilot_dataset_from_db(session)
        featured = build_spatiotemporal_features(df, coords)
        hist_feat = featured[featured["timestamp"] < (t_start + timedelta(hours=110))].copy()
        cutoff = end_hr  # target = 13:00Z
        target = cutoff + timedelta(hours=1)
        suite = fit_shadow_suite(
            hist_feat,
            coords,
            artifact_dir=tmp_path / "timing_artifacts",
            model_created_at=end_hr - timedelta(hours=2),
        )

        # 1. Clock boundary: 299s (< 300s min_lead_seconds) MUST FAIL cleanly
        with pytest.raises(ValueError, match="Insufficient prospective lead time"):
            generate_prospective_shadow_forecasts(
                session,
                [cutoff],
                suite,
                coords,
                min_lead_seconds=300.0,
                now_fn=lambda: target - timedelta(seconds=299),
            )

        # 2. Delayed commit: 400s at generation, but clock slips to 120s (< 300s) before commit -> rollback!
        ticks = iter([target - timedelta(seconds=400), target - timedelta(seconds=120)])
        with pytest.raises(ValueError, match="Prospective commit aborted"):
            generate_prospective_shadow_forecasts(
                session,
                [cutoff],
                suite,
                coords,
                min_lead_seconds=300.0,
                now_fn=lambda: next(ticks),
            )
        assert session.scalar(select(func.count(ForecastRun.id))) == 0
        assert session.scalar(select(func.count(ForecastRecord.id))) == 0

        # 3. Post-cutoff pre-target run (generated 15m AFTER decision_cutoff, 45m BEFORE target)
        # MUST persist available_at_decision_cutoff=False & effective_lead_seconds=2700.0
        post_cutoff_ts = cutoff + timedelta(minutes=15)
        runs_post = generate_prospective_shadow_forecasts(
            session,
            [cutoff],
            suite,
            coords,
            min_lead_seconds=300.0,
            now_fn=lambda: post_cutoff_ts,
        )
        assert len(runs_post) == 5
        assert all(r.effective_lead_seconds == pytest.approx(2700.0, abs=0.5) for r in runs_post)
        recs_post = list(session.scalars(select(ForecastRecord)).all())
        assert all(r.available_at_decision_cutoff is False for r in recs_post)
        summary_post = reconcile_shadow_evaluation(
            session, min_matched_outcomes=2, min_unique_hours=1, min_lead_seconds=300.0
        )
        assert summary_post["available_at_decision_cutoff_count"] == 0
        assert summary_post["post_cutoff_pre_target_count"] == 10
        assert summary_post["min_effective_lead_minutes"] == pytest.approx(45.0, abs=0.1)

    # 4. Concurrent workers racing on next cutoff cleanly converge to 5 runs / 10 records via idempotency
    cutoff2 = end_hr + timedelta(hours=1)  # target2 = 14:00Z
    target2 = cutoff2 + timedelta(hours=1)
    # Persisted right AT decision_cutoff -> available_at_decision_cutoff must be True (lead=3600s)
    on_time_ts = cutoff2

    def _worker_call() -> int:
        w_eng = create_engine(f"sqlite:///{seeded_db}", connect_args={"check_same_thread": False})
        w_fac = sessionmaker(bind=w_eng, autoflush=False, autocommit=False)
        try:
            with w_fac() as ws:
                res_runs = generate_prospective_shadow_forecasts(
                    ws,
                    [cutoff2],
                    suite,
                    coords,
                    min_lead_seconds=300.0,
                    now_fn=lambda: on_time_ts,
                )
                return len(res_runs)
        finally:
            w_eng.dispose()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_worker_call)
        f2 = pool.submit(_worker_call)
        assert f1.result() == 5
        assert f2.result() == 5

    with factory() as verify_s:
        c2_recs = list(
            verify_s.scalars(select(ForecastRecord).where(ForecastRecord.decision_cutoff == cutoff2)).all()
        )
        assert len(c2_recs) == 10
        assert all(r.available_at_decision_cutoff is True for r in c2_recs)
        assert all(
            r.effective_lead_seconds == pytest.approx((target2 - on_time_ts).total_seconds(), abs=0.5)
            for r in c2_recs
        )

        # 5. Pre-existing target observation added mid-flight aborts commit cleanly
        cutoff3 = end_hr + timedelta(hours=2)
        target3 = cutoff3 + timedelta(hours=1)
        ticks_race = iter([target3 - timedelta(minutes=30), target3 - timedelta(minutes=29)])

        def _inject_truth_mid_run() -> datetime:
            val = next(ticks_race)
            if val > target3 - timedelta(minutes=30):
                # Simulate another ingestion worker inserting truth right before forecast commit
                with factory() as obs_s:
                    obs_s.add(
                        Observation(
                            sensor_id=s1_id,
                            raw_batch_id=batch_id,
                            observed_at=target3,
                            fetched_at=val,
                            persisted_at=val,
                            value=15.5,
                            quality_flag="VALID",
                            source_status="HEALTHY",
                            version=1,
                        )
                    )
                    obs_s.commit()
            return val

        with pytest.raises(ValueError, match="target observation arrived concurrently"):
            generate_prospective_shadow_forecasts(
                verify_s,
                [cutoff3],
                suite,
                coords,
                min_lead_seconds=300.0,
                now_fn=_inject_truth_mid_run,
            )

    engine.dispose()


def test_commit_completion_proof_stalled_commits_and_competing_inserts(tmp_path) -> None:
    """Adversarial Task B tests: pre-commit safety margin, stalled commits, threshold/target crossings & competing inserts."""
    end_hr = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    src_eng, _, t_start, s1_id, _, batch_id = _seed_shadow_db(tmp_path, end_hour_utc=end_hr, n_hours=150)
    src_eng.dispose()
    seeded_db = tmp_path / "shadow_eval_m86.db"
    engine = create_engine(f"sqlite:///{seeded_db}", connect_args={"check_same_thread": False})
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with factory() as session:
        df, coords = load_pilot_dataset_from_db(session)
        featured = build_spatiotemporal_features(df, coords)
        hist_feat = featured[featured["timestamp"] < (t_start + timedelta(hours=110))].copy()
        suite = fit_shadow_suite(
            hist_feat,
            coords,
            artifact_dir=tmp_path / "commit_proof_arts",
            model_created_at=end_hr - timedelta(hours=2),
        )

        # 1. Pre-commit safety margin rejection: 310s lead passes 300s min_lead alone,
        #    but MUST fail pre-commit check against 300s + 15s = 315s safety margin (0 rows committed)
        c1 = end_hr
        t1 = c1 + timedelta(hours=1)
        ticks_margin = iter([t1 - timedelta(seconds=320), t1 - timedelta(seconds=310)])
        with pytest.raises(ValueError, match="min_lead\\+safety_margin"):
            generate_prospective_shadow_forecasts(
                session,
                [c1],
                suite,
                coords,
                min_lead_seconds=300.0,
                commit_safety_margin_seconds=15.0,
                now_fn=lambda: next(ticks_margin),
            )
        assert session.scalar(select(func.count(ForecastRun.id))) == 0

        # 2. Stalled commit crossing min_lead_seconds AFTER Tx1 pre-commit passed:
        #    gen=330s, pre_commit=320s (>=315s!), Tx1 commit stalls 30s -> commit_completed_at=290s (<300s!)
        ticks_stall_lead = iter(
            [t1 - timedelta(seconds=330), t1 - timedelta(seconds=320), t1 - timedelta(seconds=290)]
        )
        with pytest.raises(ValueError, match="Prospective availability proof failed"):
            generate_prospective_shadow_forecasts(
                session,
                [c1],
                suite,
                coords,
                min_lead_seconds=300.0,
                commit_safety_margin_seconds=15.0,
                now_fn=lambda: next(ticks_stall_lead),
            )
        # Stored rows MUST reflect conservative post-commit time (290s lead) & quarantine cleanly
        recs_s1 = list(session.scalars(select(ForecastRecord)).all())
        assert len(recs_s1) == 10
        assert all(r.effective_lead_seconds == pytest.approx(290.0, abs=0.1) for r in recs_s1)
        assert all(r.persisted_at == r.commit_completed_at for r in recs_s1)
        eval_s1 = reconcile_shadow_evaluation(
            session, min_matched_outcomes=2, min_unique_hours=1, min_lead_seconds=300.0
        )
        assert eval_s1["lead_time_violation_count"] == 10
        assert eval_s1["truly_prospective_count"] == 0
        assert eval_s1["calibration_claim_eligible"] is False

    # 3. Stalled commit finishing AFTER target_time (commit_completed_at = target + 80s):
    #    MUST NEVER claim record was stored before target_time!
    with factory() as s2:
        c2 = end_hr + timedelta(hours=1)
        t2 = c2 + timedelta(hours=1)
        ticks_cross_target = iter(
            [t2 - timedelta(seconds=330), t2 - timedelta(seconds=320), t2 + timedelta(seconds=80)]
        )
        with pytest.raises(ValueError, match="Prospective availability proof failed"):
            generate_prospective_shadow_forecasts(
                s2,
                [c2],
                suite,
                coords,
                min_lead_seconds=300.0,
                commit_safety_margin_seconds=15.0,
                now_fn=lambda: next(ticks_cross_target),
            )
        c2_recs = list(s2.scalars(select(ForecastRecord).where(ForecastRecord.target_time == t2)).all())
        assert len(c2_recs) == 10
        assert all(
            (r.persisted_at.replace(tzinfo=UTC) if r.persisted_at.tzinfo is None else r.persisted_at) > t2
            for r in c2_recs
        )
        eval_s2 = reconcile_shadow_evaluation(
            s2, min_matched_outcomes=2, min_unique_hours=1, min_lead_seconds=300.0
        )
        assert eval_s2["backdated_violation_count"] == 10
        assert eval_s2["truly_prospective_count"] == 0

    # 4. Competing target Observation committed DURING Tx1 session.commit() (between pre_commit & completion)
    with factory() as s3:
        c3 = end_hr + timedelta(hours=2)
        t3 = c3 + timedelta(hours=1)
        injected_once = {"done": False}

        def _after_tx1_commit(_sess) -> None:
            if not injected_once["done"]:
                injected_once["done"] = True
                # Simulate competing ingestion worker persisting target outcome just before commit completion check
                with factory() as comp_s:
                    comp_s.add(
                        Observation(
                            sensor_id=s1_id,
                            raw_batch_id=batch_id,
                            observed_at=t3,
                            fetched_at=t3 - timedelta(seconds=310),
                            persisted_at=t3 - timedelta(seconds=310),
                            value=17.3,
                            quality_flag="VALID",
                            source_status="HEALTHY",
                            version=1,
                        )
                    )
                    comp_s.commit()

        event.listen(s3, "after_commit", _after_tx1_commit)
        try:
            ticks_comp = iter(
                [t3 - timedelta(seconds=330), t3 - timedelta(seconds=320), t3 - timedelta(seconds=305)]
            )
            with pytest.raises(ValueError, match="competing_obs=True"):
                generate_prospective_shadow_forecasts(
                    s3,
                    [c3],
                    suite,
                    coords,
                    min_lead_seconds=300.0,
                    commit_safety_margin_seconds=15.0,
                    now_fn=lambda: next(ticks_comp),
                )
        finally:
            event.remove(s3, "after_commit", _after_tx1_commit)

        eval_s3 = reconcile_shadow_evaluation(
            s3, min_matched_outcomes=2, min_unique_hours=1, min_lead_seconds=300.0
        )
        assert eval_s3["truly_prospective_count"] == 0
        assert eval_s3["backdated_violation_count"] >= 10
        assert eval_s3["calibration_claim_eligible"] is False

    engine.dispose()
