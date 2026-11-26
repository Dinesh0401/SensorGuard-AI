"""Milestone 8.7 end-to-end no-network live-pilot dry-run acceptance suite (Part D).

Exercises the complete sequence against an Alembic-migrated (008_m87_artifact_timing_gate)
database without making any external network connections:
1. Fail-closed PilotControlState & /api/v1/pilot/readiness lock verification
2. Expired-lease crash recovery + monotonic fencing token + per-attempt OpenAQ retry quota
3. Immutable JSON shadow model serialization (SHA-256 over disk bytes, zero pickle/joblib)
4. Genuine prospective shadow forecast generation with min_lead_seconds >= 300s
5. Preempted worker lease fencing abort (`SKIPPED_STALE_FENCING_TOKEN`) & kill-switch halt
6. Delayed truth arrival + v2 revision via mocked OpenAQ -> reconcile_shadow_evaluation()
7. Live FastAPI dashboard readiness/forecast endpoints + fail-closed 503 on DB rate limiter loss
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from alembic import command
from sensorguard.api import (
    _compute_station_forecasts_sync,
    app,
    get_db,
    get_serving_model,
    invalidate_pilot_feature_cache,
)
from sensorguard.db.schema import (
    ForecastRecord,
    Observation,
    PilotApiRateLimit,
    PilotPollLease,
    RawIngestBatch,
    Sensor,
    Station,
)
from sensorguard.models.data import build_spatiotemporal_features, load_pilot_dataset_from_db
from sensorguard.pilot_monitor import (
    BoundedPilotConfig,
    DurablePilotQuotaTracker,
    PilotQuotaTracker,
    run_bounded_opt_in_poll,
    set_polling_kill_switch,
)
from sensorguard.shadow_eval import (
    generate_prospective_shadow_forecasts,
    reconcile_shadow_evaluation,
)

ROOT_DIR = Path(__file__).resolve().parent.parent


def _build_migrated_dry_run_db(
    db_path: Path, t_base: datetime, monkeypatch: pytest.MonkeyPatch, n_hours: int = 120
):
    db_url = f"sqlite:///{db_path.as_posix()}"
    alembic_cfg = Config(str(ROOT_DIR / "alembic.ini"))
    with monkeypatch.context() as mp:
        mp.setenv("DATABASE_URL", db_url)
        command.upgrade(alembic_cfg, "head")

    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with factory() as session:
        st1 = Station(
            provider="openaq",
            provider_location_id=154,
            name="Westminster - Marylebone Road",
            latitude=51.5225,
            longitude=-0.1546,
            country="GB",
            timezone_name="Europe/London",
            metadata_json='{"provider":{"name":"London Air Quality Network"},"licenses":[{"name":"UK OGL"}],"datetimeLast":{"utc":"2026-11-26T10:00:00Z"}}',
            updated_at=t_base + timedelta(hours=n_hours - 1),
        )
        st2 = Station(
            provider="openaq",
            provider_location_id=157,
            name="Bexley - Slade Green",
            latitude=51.4660,
            longitude=0.1849,
            country="GB",
            timezone_name="Europe/London",
            metadata_json='{"provider":{"name":"London Air Quality Network"},"licenses":[{"name":"UK OGL"}],"datetimeLast":{"utc":"2026-11-26T10:00:00Z"}}',
            updated_at=t_base + timedelta(hours=n_hours - 1),
        )
        session.add_all([st1, st2])
        session.flush()

        s1 = Sensor(station_id=st1.id, provider_sensor_id=246, parameter="pm25", canonical_unit="µg/m³")
        s2 = Sensor(station_id=st2.id, provider_sensor_id=254, parameter="pm25", canonical_unit="µg/m³")
        batch = RawIngestBatch(
            provider="openaq",
            endpoint="/v3/sensors/hist/hours",
            fetched_at=t_base + timedelta(hours=n_hours - 1, minutes=5),
            status_code=200,
            response_hash="d" * 64,
            payload_json="{}",
            record_count=n_hours * 2,
        )
        session.add_all([s1, s2, batch])
        session.flush()

        for h in range(n_hours):
            ts = t_base + timedelta(hours=h)
            session.add_all(
                [
                    Observation(
                        sensor_id=s1.id,
                        raw_batch_id=batch.id,
                        observed_at=ts,
                        fetched_at=ts + timedelta(minutes=6),
                        persisted_at=ts + timedelta(minutes=6),
                        value=14.0 + (h % 6) * 0.7,
                        quality_flag="VALID",
                        source_status="HEALTHY",
                        version=1,
                    ),
                    Observation(
                        sensor_id=s2.id,
                        raw_batch_id=batch.id,
                        observed_at=ts,
                        fetched_at=ts + timedelta(minutes=6),
                        persisted_at=ts + timedelta(minutes=6),
                        value=11.0 + (h % 5) * 0.5,
                        quality_flag="VALID",
                        source_status="HEALTHY",
                        version=1,
                    ),
                ]
            )
        session.commit()
        return engine, factory, st1.id, st2.id, s1.id, s2.id


def test_full_live_pilot_dry_run_sequence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify full offline mocked sequence: OpenAQ poll -> shadow artifacts/forecasts -> truth reconciliation -> FastAPI."""
    monkeypatch.setenv("SENSORGUARD_POLLING_KILL_SWITCH", "false")
    monkeypatch.setenv("SENSORGUARD_ALLOW_IN_MEMORY_RATE_LIMIT_DEV", "false")
    t_base = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
    n_hours = 120
    t_latest_obs = t_base + timedelta(hours=n_hours - 1)  # 2026-11-24T23:00Z
    t_poll1 = t_latest_obs + timedelta(hours=1, minutes=10)  # 2026-11-25T00:10Z (cutoff T0)
    t_cutoff = t_latest_obs + timedelta(hours=1)  # 2026-11-25T00:00Z
    t_target = t_cutoff + timedelta(hours=1)  # 2026-11-25T01:00Z (lead = 50 min when polled at 00:10Z)

    engine, factory, st1_id, _st2_id, s1_id, s2_id = _build_migrated_dry_run_db(
        tmp_path / "dry_run_m87.db", t_base=t_base, monkeypatch=monkeypatch, n_hours=n_hours
    )

    try:
        # Step 1: Fail-closed lock check (no polling or bypass permitted before authorization)
        locked_res = run_bounded_opt_in_poll(
            session_factory=factory,
            config=BoundedPilotConfig(
                opt_in_enabled=True,
                allowed_location_ids=(154, 157),
                max_requests_per_day=12,
                max_retries_per_request=1,
            ),
            now_utc=t_poll1,
        )
        assert locked_res["status"] == "SKIPPED_KILL_SWITCH"
        assert locked_res["http_attempts"] == 0

        # Simulate prior crashed worker holding an expired lease (fencing_token=1) before authorization
        with factory() as sess:
            sess.add(
                PilotPollLease(
                    lease_name="openaq_hourly_poll",
                    holder_id="crashed-worker-alpha",
                    fencing_token=1,
                    acquired_at=t_poll1 - timedelta(minutes=20),
                    heartbeat_at=t_poll1 - timedelta(minutes=19),
                    expires_at=t_poll1 - timedelta(minutes=10),
                    updated_at=t_poll1 - timedelta(minutes=19),
                )
            )
            set_polling_kill_switch(
                sess,
                kill_switch_active=False,
                opt_in_enabled=True,
                reason="Milestone 8.7 dry-run operator approval",
                updated_by="dinesh_ops_dryrun",
                now_utc=t_poll1 - timedelta(minutes=1),
            )

        # Step 2: Poll #1 delivers t_cutoff telemetry with simulated transient HTTP 500 retry
        attempts_by_sensor: dict[str, int] = {"246": 0, "254": 0}

        def handler_poll1(request: httpx.Request) -> httpx.Response:
            if "/246/hours" in request.url.path:
                attempts_by_sensor["246"] += 1
                if attempts_by_sensor["246"] == 1:
                    return httpx.Response(500, json={"error": "transient provider glitch"})
                val = 16.4
            else:
                attempts_by_sensor["254"] += 1
                val = 12.1
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "datetime": {"utc": t_cutoff.isoformat().replace("+00:00", "Z")},
                            "value": val,
                            "parameter": {"name": "pm25", "units": "µg/m³"},
                        }
                    ]
                },
            )

        quota_tracker = PilotQuotaTracker(max_requests_per_day=12)
        poll1 = run_bounded_opt_in_poll(
            session_factory=factory,
            config=BoundedPilotConfig(
                opt_in_enabled=True,
                allowed_location_ids=(154, 157),
                max_requests_per_day=12,
                max_retries_per_request=1,
                worker_id="dryrun-worker-1",
            ),
            quota=quota_tracker,
            transport=httpx.MockTransport(handler_poll1),
            now_utc=t_poll1,
        )
        assert poll1["status"] == "SUCCESS"
        assert poll1["recovered_from_expired_lease"] is True
        assert poll1["fencing_token"] == 2
        # 246 took 2 attempts (500 + 200) + 254 took 1 attempt (200) => 3 exact HTTP attempts charged
        assert poll1["http_attempts"] == 3
        assert poll1["requests_used_today"] == 3
        assert poll1["records_persisted"] == 2

        # Align simulated poll1 DB arrival timestamps with dry-run clock t_poll1 prior to fitting
        with factory() as norm_s:
            for obs in norm_s.scalars(select(Observation).where(Observation.observed_at == t_cutoff)).all():
                obs.fetched_at = t_poll1
                obs.persisted_at = t_poll1
            norm_s.commit()

        # Step 3: Fit + serialize immutable JSON shadow suite strictly from <= t_cutoff data
        art_dir = tmp_path / "shadow_artifacts"
        t_train_done = t_poll1 + timedelta(minutes=1)
        from sensorguard.shadow_eval import fit_shadow_suite

        with factory() as sess:
            df, coords = load_pilot_dataset_from_db(sess)
            featured = build_spatiotemporal_features(df, coords)
            train_feat = featured[featured["timestamp"] <= t_cutoff].copy()
            suite = fit_shadow_suite(
                train_feat,
                coords,
                artifact_dir=art_dir,
                session=sess,
                model_created_at=t_train_done,
            )

            # Verify every artifact is real JSON on disk with matching byte SHA-256
            for m_key, prov in suite["model_provenance"].items():
                art_file = Path(prov["artifact_path"])
                assert art_file.exists() and art_file.suffix == ".json"
                real_sha = hashlib.sha256(art_file.read_bytes()).hexdigest()
                assert prov["artifact_sha256"] == real_sha
                assert f"#{real_sha[:12]}" in prov["artifact_immutable_ref"]
                doc = json.loads(art_file.read_text(encoding="utf-8"))
                assert doc["schema_format"] == "sensorguard.shadow_artifact.v1"
                assert m_key == doc["model_key"]

            # Generate genuine prospective shadow forecasts AFTER cutoff (00:13Z) but 47m BEFORE target (01:00Z)
            t_gen = t_poll1 + timedelta(minutes=3)
            runs = generate_prospective_shadow_forecasts(
                session=sess,
                decision_cutoffs=[t_cutoff],
                fitted_models=suite,
                station_coords=coords,
                data_mode="shadow_live",
                min_lead_seconds=300.0,
                now_fn=lambda: t_gen,
            )
            assert len(runs) == 5
            recs = list(
                sess.scalars(
                    select(ForecastRecord).where(ForecastRecord.run_id.in_([r.id for r in runs]))
                ).all()
            )
            assert len(recs) == 10
            for r in recs:
                # Persisted at 00:13Z (after 00:00Z decision cutoff) -> NEVER claim available_at_decision_cutoff!
                assert r.available_at_decision_cutoff is False
                assert r.effective_lead_seconds == pytest.approx(2820.0, abs=1.0)  # 47 minutes before 01:00Z
                assert r.min_lead_seconds == pytest.approx(300.0)
                assert r.training_completed_at is not None
                assert r.artifact_sha256 is not None and len(r.artifact_sha256) == 64

        # Step 4: Verify mid-flight preemption / stale lease fence rollback during simulated worker race
        t_poll2 = t_target + timedelta(hours=2, minutes=10)  # delayed truth arrival (+2h 10m late)

        def handler_preempt_and_deliver(request: httpx.Request) -> httpx.Response:
            # While polling sensor 246, steal lease externally so commit must abort via stale fence
            with factory() as hijack_sess:
                row = hijack_sess.scalars(
                    select(PilotPollLease).where(PilotPollLease.lease_name == "openaq_hourly_poll")
                ).one()
                row.holder_id = "worker-preemptor"
                row.fencing_token += 1
                row.expires_at = t_poll2 + timedelta(minutes=10)
                hijack_sess.commit()
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "datetime": {"utc": t_target.isoformat().replace("+00:00", "Z")},
                            "value": 999.0,  # MUST NEVER COMMIT because worker is fenced!
                            "parameter": {"name": "pm25", "units": "µg/m³"},
                        }
                    ]
                },
            )

        fenced_poll = run_bounded_opt_in_poll(
            session_factory=factory,
            config=BoundedPilotConfig(
                opt_in_enabled=True,
                allowed_location_ids=(154,),
                max_requests_per_day=12,
                worker_id="slow-stalled-worker",
            ),
            transport=httpx.MockTransport(handler_preempt_and_deliver),
            now_utc=t_poll2,
        )
        assert fenced_poll["status"] == "SKIPPED_STALE_FENCING_TOKEN"
        assert fenced_poll["records_persisted"] == 0
        # Verify quota WAS consumed for the outbound HTTP attempt on UTC day 2026-11-25 (3 from poll1 + 1 fenced = 4)
        with factory() as check_s:
            used_after_fence, _ = DurablePilotQuotaTracker(max_requests_per_day=12).get_usage(
                check_s, now_utc=t_poll2
            )
            assert used_after_fence == 4
            assert (
                check_s.scalars(select(Observation).where(Observation.observed_at == t_target)).first()
                is None
            )

        # Release preemptor lease cleanly and run delayed observation arrival + revision v2 on next poll
        with factory() as rel_s:
            lease_row = rel_s.scalars(
                select(PilotPollLease).where(PilotPollLease.lease_name == "openaq_hourly_poll")
            ).one()
            lease_row.holder_id = None
            lease_row.expires_at = None
            rel_s.commit()

        def handler_delayed_truth_v1(request: httpx.Request) -> httpx.Response:
            val = 19.5 if "/246/hours" in request.url.path else 13.0
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "datetime": {"utc": t_target.isoformat().replace("+00:00", "Z")},
                            "value": val,
                            "parameter": {"name": "pm25", "units": "µg/m³"},
                        }
                    ]
                },
            )

        poll2 = run_bounded_opt_in_poll(
            session_factory=factory,
            config=BoundedPilotConfig(
                opt_in_enabled=True,
                allowed_location_ids=(154, 157),
                max_requests_per_day=12,
                worker_id="dryrun-worker-2",
            ),
            transport=httpx.MockTransport(handler_delayed_truth_v1),
            now_utc=t_poll2 + timedelta(minutes=5),
        )
        assert poll2["status"] == "SUCCESS"
        assert poll2["records_persisted"] == 2

        # Send upstream correction (v2 revision for sensor 246 at t_target)
        def handler_delayed_truth_v2(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "datetime": {"utc": t_target.isoformat().replace("+00:00", "Z")},
                            "value": 20.2,
                            "parameter": {"name": "pm25", "units": "µg/m³"},
                        }
                    ]
                },
            )

        poll3 = run_bounded_opt_in_poll(
            session_factory=factory,
            config=BoundedPilotConfig(
                opt_in_enabled=True,
                allowed_location_ids=(154,),
                max_requests_per_day=12,
                worker_id="dryrun-worker-3",
            ),
            transport=httpx.MockTransport(handler_delayed_truth_v2),
            now_utc=t_poll2 + timedelta(minutes=20),
        )
        assert poll3["status"] == "SUCCESS"
        assert poll3["records_revised"] == 1

        # Re-lock kill switch -> confirm next worker halts immediately
        with factory() as lock_s:
            set_polling_kill_switch(
                lock_s,
                kill_switch_active=True,
                opt_in_enabled=False,
                reason="Dry-run completed; restoring fail-closed lock",
                updated_by="dinesh_ops_dryrun",
                now_utc=t_poll2 + timedelta(minutes=25),
            )

        # Ensure obs persisted_at reflects arrival AFTER forecast persisted_at (00:13Z)
        with factory() as fix_s:
            arrived_obs = list(
                fix_s.scalars(
                    select(Observation).where(
                        Observation.sensor_id.in_([s1_id, s2_id]), Observation.observed_at == t_target
                    )
                ).all()
            )
            for ob in arrived_obs:
                ob.persisted_at = t_poll2 + timedelta(minutes=5)
                ob.fetched_at = t_poll2 + timedelta(minutes=5)
            fix_s.commit()

            # Step 5: Reconcile prospective shadow evaluation
            eval_summary = reconcile_shadow_evaluation(
                fix_s,
                min_matched_outcomes=2,
                min_unique_hours=1,
                min_lead_seconds=300.0,
                evaluation_mode="prospective_shadow",
            )
            assert eval_summary["status"] == "EVALUATED"
            assert eval_summary["artifact_integrity_verified"] is True
            assert eval_summary["backdated_violation_count"] == 0
            assert eval_summary["lead_time_violation_count"] == 0
            assert eval_summary["artifact_violation_count"] == 0
            assert eval_summary["post_cutoff_pre_target_count"] == 10
            assert eval_summary["available_at_decision_cutoff_count"] == 0
            assert eval_summary["min_effective_lead_minutes"] == pytest.approx(47.0, abs=0.1)
            adap_eval = eval_summary["models"]["ADAPTIVE-SAGE-LIGHTGBM"]
            assert adap_eval["matched_count"] == 2
            assert adap_eval["delayed_arrival_count"] == 2  # +2h > 1.5h delay threshold

        # Step 6: Verify FastAPI endpoints & dashboard readiness contract + DB rate-limit failure check
        def _override_db():
            with factory() as s:
                yield s

        invalidate_pilot_feature_cache()
        app.dependency_overrides[get_db] = _override_db
        try:
            with TestClient(app) as client:
                r_ready = client.get("/api/v1/pilot/readiness")
                assert r_ready.status_code == 200
                payload = r_ready.json()
                assert payload["kill_switch_active"] is True
                assert payload["opt_in_enabled"] is False
                assert payload["operational_data_mode"] == "archived_historical"
                assert payload["shadow_evaluation_summary"]["artifact_integrity_verified"] is True
                assert payload["shadow_evaluation_summary"]["min_lead_seconds_required"] == 300.0
                assert payload["shadow_evaluation_summary"]["post_cutoff_pre_target_count"] == 10
                assert len(payload["recent_audit_runs"]) >= 1

                # Serving forecasts endpoint stays Static SAGEForecaster
                with factory() as s_fc:
                    fc_res = _compute_station_forecasts_sync(st1_id, 4, get_serving_model(), session=s_fc)
                    assert len(fc_res.forecasts) > 0
                    assert "SAGEForecaster" in fc_res.forecasts[0].model_version
                    assert fc_res.forecasts[0].generated_at is not None

            # Drop durable rate-limit table and verify endpoint fails closed with HTTP 503
            PilotApiRateLimit.__table__.drop(bind=engine)
            with TestClient(app) as client:
                r_fail = client.get("/api/v1/pilot/readiness")
                assert r_fail.status_code == 503
                assert "durable rate limiter failure" in r_fail.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()
            invalidate_pilot_feature_cache()
    finally:
        engine.dispose()
