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
    PilotControlState,
    PilotIngestAuditRun,
    PilotPollLease,
    PilotQuotaState,
    RawIngestBatch,
    Sensor,
    Station,
)
from sensorguard.pilot_monitor import (
    BoundedPilotConfig,
    DurablePilotQuotaTracker,
    PilotQuotaTracker,
    acquire_poll_lease,
    inspect_station_provider_metadata,
    reconcile_delayed_ground_truth,
    release_poll_lease,
    run_bounded_opt_in_poll,
    set_polling_kill_switch,
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


def test_durable_quota_across_restarts_and_worker_concurrency(tmp_path) -> None:
    """Verify DurablePilotQuotaTracker persists across engine restarts and prevents concurrent overshoot."""
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import create_engine, event, select
    from sqlalchemy.orm import sessionmaker

    from sensorguard.db.schema import Base, PilotQuotaState

    db_file = tmp_path / "durable_quota.db"
    db_url = f"sqlite:///{db_file}"

    def _make_engine():
        eng = create_engine(
            db_url,
            connect_args={"check_same_thread": False, "timeout": 30.0},
            future=True,
        )

        @event.listens_for(eng, "connect")
        def _set_wal(dbapi_conn, _conn_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        return eng

    eng1 = _make_engine()
    Base.metadata.create_all(eng1)
    sf1 = sessionmaker(bind=eng1, autoflush=False, autocommit=False)
    t0 = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)

    tracker1 = DurablePilotQuotaTracker(provider="openaq", max_requests_per_day=3)
    with sf1() as sess:
        assert tracker1.get_usage(sess, now_utc=t0) == (0, 3)
        assert tracker1.try_consume(sess, count=1, now_utc=t0) is True
        assert tracker1.get_usage(sess, now_utc=t0) == (1, 2)
    eng1.dispose()

    # Simulate process restart with brand-new engine and concurrent worker sessions
    eng2 = _make_engine()
    sf2 = sessionmaker(bind=eng2, autoflush=False, autocommit=False)
    tracker2 = DurablePilotQuotaTracker(provider="openaq", max_requests_per_day=3)

    with sf2() as sess:
        assert tracker2.get_usage(sess, now_utc=t0) == (1, 2)

    def _worker_consume(_worker_idx: int) -> bool:
        worker_tracker = DurablePilotQuotaTracker(provider="openaq", max_requests_per_day=3)
        with sf2() as worker_sess:
            return worker_tracker.try_consume(worker_sess, count=1, now_utc=t0)

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(_worker_consume, range(5)))

    assert outcomes.count(True) == 2
    assert outcomes.count(False) == 3

    with sf2() as sess:
        assert tracker2.get_usage(sess, now_utc=t0) == (3, 0)
        assert tracker2.try_consume(sess, count=1, now_utc=t0) is False
        rows = list(sess.scalars(select(PilotQuotaState)).all())
        assert len(rows) == 1 and rows[0].requests_used == 3
        # Rollover to next UTC day restores fresh daily quota
        next_day = t0 + timedelta(days=1)
        assert tracker2.get_usage(sess, now_utc=next_day) == (0, 3)
    eng2.dispose()


def test_distributed_lease_crash_recovery_kill_switch_429_and_revisions(tmp_path, monkeypatch) -> None:
    """Verify DB/env kill switches, lease TTL recovery, HTTP 429 handling, and append-only revisions."""
    import json

    from sqlalchemy import create_engine, desc, select
    from sqlalchemy.orm import sessionmaker

    from sensorguard.db.schema import Base, Observation, Sensor, Station
    from sensorguard.pilot_monitor import ensure_default_control_state

    eng = create_engine(f"sqlite:///{tmp_path / 'lease_audit.db'}", future=True)
    Base.metadata.create_all(eng)
    sf = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    h1 = now - timedelta(hours=2)
    h2 = now - timedelta(hours=1)

    with sf() as sess:
        st = Station(
            provider="openaq",
            provider_location_id=154,
            name="Westminster - Marylebone Road",
            country="GB",
            latitude=51.5225,
            longitude=-0.1546,
            metadata_json=json.dumps(
                {
                    "provider": {"name": "London Air Quality Network"},
                    "licenses": [{"name": "UK OGL"}],
                }
            ),
            updated_at=now,
        )
        sess.add(st)
        sess.flush()
        sess.add(
            Sensor(
                station_id=st.id,
                provider_sensor_id=246,
                parameter="pm25",
                canonical_unit="µg/m³",
            )
        )
        ctrl = ensure_default_control_state(sess, now_utc=now)
        assert ctrl.kill_switch_active is True
        assert ctrl.opt_in_enabled is False
        sess.commit()

    # 1. Fail-closed DB kill switch blocks poll even when config.opt_in_enabled=True
    def _ok_transport(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP transport must not execute while kill-switch is active")

    cfg = BoundedPilotConfig(
        opt_in_enabled=True,
        allowed_location_ids=(154,),
        lookback_hours=24,
        max_requests_per_day=10,
        lease_ttl_seconds=15,
        worker_id="worker-active",
        require_db_unlock_for_live=True,
    )
    blocked = run_bounded_opt_in_poll(
        sf,
        cfg,
        transport=httpx.MockTransport(_ok_transport),
        now_utc=now,
    )
    assert blocked["status"] == "SKIPPED_KILL_SWITCH"

    # 2. Unlock DB control, verify environment kill switch still overrides fail-closed
    with sf() as sess:
        set_polling_kill_switch(
            sess,
            kill_switch_active=False,
            opt_in_enabled=True,
            reason="Offline mock transport test unlocked",
            updated_by="pytest",
            now_utc=now,
        )
    monkeypatch.setenv("SENSORGUARD_POLLING_KILL_SWITCH", "true")
    env_blocked = run_bounded_opt_in_poll(
        sf,
        cfg,
        transport=httpx.MockTransport(_ok_transport),
        now_utc=now,
    )
    assert env_blocked["status"] == "SKIPPED_KILL_SWITCH"
    monkeypatch.delenv("SENSORGUARD_POLLING_KILL_SWITCH", raising=False)

    # 3. Active leader lease blocks competing worker; expired lease recovers automatically
    with sf() as sess:
        acquired, recovered = acquire_poll_lease(
            sess,
            holder_id="crashed-worker",
            lease_name=cfg.lease_name,
            ttl_seconds=15,
            now_utc=now - timedelta(seconds=5),
        )
        assert (acquired, recovered) == (True, False)

    contended = run_bounded_opt_in_poll(
        sf,
        cfg,
        transport=httpx.MockTransport(_ok_transport),
        now_utc=now,
    )
    assert contended["status"] == "SKIPPED_NO_LEASE"

    # 4. After lease expires (+30s), worker recovers lease and handles HTTP 429 cleanly
    def _rate_limit_transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0.01"}, json={"detail": "rate limit"})

    after_ttl = now + timedelta(seconds=30)
    res_429 = run_bounded_opt_in_poll(
        sf,
        cfg,
        transport=httpx.MockTransport(_rate_limit_transport),
        now_utc=after_ttl,
    )
    assert res_429["status"] == "HTTP_429_BACKOFF"
    assert res_429["recovered_from_expired_lease"] is True

    # 5. Bounded historical reconciliation + append-only revision on upstream value update
    payload_v1 = {
        "results": [
            {
                "value": 12.5,
                "parameter": {"name": "pm25", "units": "µg/m³"},
                "period": {
                    "datetimeFrom": {"utc": (h1 - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")},
                    "datetimeTo": {"utc": h1.strftime("%Y-%m-%dT%H:%M:%SZ")},
                },
            },
            {
                "value": 15.0,
                "parameter": {"name": "pm25", "units": "µg/m³"},
                "period": {
                    "datetimeFrom": {"utc": h1.strftime("%Y-%m-%dT%H:%M:%SZ")},
                    "datetimeTo": {"utc": h2.strftime("%Y-%m-%dT%H:%M:%SZ")},
                },
            },
        ]
    }
    res_v1 = run_bounded_opt_in_poll(
        sf,
        cfg,
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=payload_v1)),
        now_utc=now,
    )
    assert res_v1["status"] == "SUCCESS"
    assert res_v1["records_inserted"] == 2
    assert res_v1["records_revised"] == 0

    payload_v2 = {
        "results": [
            payload_v1["results"][0],  # Unchanged -> deduplicated
            {
                **payload_v1["results"][1],
                "value": 17.5,  # Revised -> append-only version=2
            },
        ]
    }
    res_v2 = run_bounded_opt_in_poll(
        sf,
        cfg,
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=payload_v2)),
        now_utc=now,
    )
    assert res_v2["status"] == "SUCCESS"
    assert res_v2["records_deduplicated"] == 1
    assert res_v2["records_revised"] == 1

    with sf() as sess:
        obs_h2 = list(
            sess.scalars(
                select(Observation).where(Observation.observed_at == h2).order_by(Observation.version.asc())
            ).all()
        )
        assert [o.version for o in obs_h2] == [1, 2]
        assert [float(o.value or 0.0) for o in obs_h2] == [15.0, 17.5]

        ctrl_row = sess.scalars(select(PilotControlState)).first()
        assert ctrl_row is not None and ctrl_row.kill_switch_active is False

        lease_row = sess.scalars(select(PilotPollLease)).first()
        assert lease_row is not None and lease_row.holder_id is None
        acquired_m, _ = acquire_poll_lease(sess, holder_id="manual-op", lease_name="manual", now_utc=now)
        assert acquired_m is True
        assert release_poll_lease(sess, holder_id="manual-op", lease_name="manual", now_utc=now) is True

        audits = list(sess.scalars(select(PilotIngestAuditRun).order_by(desc(PilotIngestAuditRun.id))).all())
        statuses = {a.status for a in audits}
        assert {"SKIPPED_KILL_SWITCH", "SKIPPED_NO_LEASE", "HTTP_429_BACKOFF", "SUCCESS"}.issubset(statuses)
    eng.dispose()


def test_inspect_station_provider_metadata_offline(tmp_path) -> None:
    """Verify station license and snapshot recency audit never calls external HTTP or assumes live state."""
    import json

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from sensorguard.db.schema import Base, Sensor, Station

    eng = create_engine(f"sqlite:///{tmp_path / 'meta_audit.db'}", future=True)
    Base.metadata.create_all(eng)
    sf = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    snap_time = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)

    with sf() as sess:
        st_recent = Station(
            provider="openaq",
            provider_location_id=154,
            name="Westminster - Marylebone Road",
            country="GB",
            latitude=51.52,
            longitude=-0.15,
            metadata_json=json.dumps(
                {
                    "provider": {"name": "London Air Quality Network"},
                    "licenses": [{"name": "UK OGL"}],
                    "datetimeFirst": {"utc": "2016-01-30T01:00:00Z"},
                    "datetimeLast": {"utc": "2026-11-22T11:00:00Z"},
                }
            ),
            updated_at=snap_time,
        )
        st_stale = Station(
            provider="openaq",
            provider_location_id=141,
            name="London Harlington - UKA00472",
            country="GB",
            latitude=51.49,
            longitude=-0.44,
            metadata_json=json.dumps(
                {
                    "provider": {"name": "EEA"},
                    "licenses": [{"name": "ODC-BY"}],
                    "datetimeFirst": {"utc": "2016-01-30T01:00:00Z"},
                    "datetimeLast": {"utc": "2026-05-26T13:00:00Z"},
                }
            ),
            updated_at=snap_time,
        )
        sess.add_all([st_recent, st_stale])
        sess.flush()
        sess.add_all(
            [
                Sensor(
                    station_id=st_recent.id, provider_sensor_id=246, parameter="pm25", canonical_unit="µg/m³"
                ),
                Sensor(
                    station_id=st_stale.id, provider_sensor_id=261, parameter="pm25", canonical_unit="µg/m³"
                ),
            ]
        )
        sess.commit()

        audit = inspect_station_provider_metadata(sess, reference_time=snap_time, recent_threshold_hours=48.0)
        assert audit["total_stations"] == 2
        assert audit["recently_active_at_snapshot"] == 1
        assert audit["stale_at_snapshot"] == 1
        by_loc = {item["provider_location_id"]: item for item in audit["stations"]}
        assert by_loc[154]["licenses"] == ["UK OGL"]
        assert by_loc[154]["was_active_at_fetch_snapshot"] is True
        assert by_loc[154]["current_live_status"] == "UNVERIFIED_OFFLINE_MODE"
        assert by_loc[141]["licenses"] == ["ODC-BY"]
        assert by_loc[141]["was_active_at_fetch_snapshot"] is False
        assert by_loc[141]["current_live_status"] == "UNVERIFIED_OFFLINE_MODE"
    eng.dispose()
