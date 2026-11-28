"""Offline unit and adversarial safety tests for single-use Stage 1 OpenAQ pre-flight CLI & recovery."""

from __future__ import annotations

import concurrent.futures
import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from alembic import command
from sensorguard.cli import main as cli_main
from sensorguard.db.schema import (
    PilotControlState,
    PilotIngestAuditRun,
    PilotQuotaState,
    Sensor,
    Stage1PreflightAuthorization,
    Station,
)
from sensorguard.pilot_monitor import DEFAULT_CONTROL_NAME
from sensorguard.preflight import (
    STAGE1_ARCHIVED_LOCATION_IDS,
    Stage1PreflightConfig,
    authorize_stage1_preflight_db,
    authorize_stage1_recovery_db,
    mark_stage1_incomplete_or_aborted,
    run_stage1_preflight,
)

ROOT_DIR = Path(__file__).resolve().parent.parent


def _seed_8_uk_stations(db_path: Path, monkeypatch: pytest.MonkeyPatch, apply_migrations: bool = True):
    db_url = f"sqlite:///{db_path.as_posix()}"
    if apply_migrations:
        cfg = Config(str(ROOT_DIR / "alembic.ini"))
        with monkeypatch.context() as mp:
            mp.setenv("DATABASE_URL", db_url)
            command.upgrade(cfg, "head")

    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA busy_timeout=5000")
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    if apply_migrations:
        meta_recent = '{"provider":{"name":"London Air Quality Network"},"licenses":[{"name":"UK OGL"}],"datetimeLast":{"utc":"2026-11-26T10:00:00Z"}}'
        meta_stale = '{"provider":{"name":"EEA"},"licenses":[{"name":"ODC-BY"}],"datetimeLast":{"utc":"2026-05-26T13:00:00Z"}}'
        with factory() as s:
            for idx, loc_id in enumerate(STAGE1_ARCHIVED_LOCATION_IDS):
                st = Station(
                    provider="openaq",
                    provider_location_id=loc_id,
                    name=f"UK Station {loc_id}",
                    latitude=51.5 + idx * 0.01,
                    longitude=-0.1,
                    country="GB",
                    timezone_name="Europe/London",
                    metadata_json=meta_recent if loc_id in (154, 157) else meta_stale,
                )
                s.add(st)
                s.flush()
                s.add(
                    Sensor(
                        station_id=st.id,
                        provider_sensor_id=1000 + loc_id,
                        parameter="pm25",
                        canonical_unit="µg/m³",
                    )
                )
            # Explicitly keep recurring polling locked
            s.add(
                PilotControlState(
                    control_name=DEFAULT_CONTROL_NAME,
                    kill_switch_active=True,
                    opt_in_enabled=False,
                    reason="Stage 2 held pending Stage 1",
                    updated_by="system_default",
                )
            )
            s.commit()
    return engine, factory


def test_stage1_preflight_defaults_to_dry_run_and_blocks_without_single_use_db_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Default CLI invocation is dry-run with zero HTTP calls; env flag alone cannot authorize execute-once."""
    db_path = tmp_path / "preflight_dry.db"
    engine, factory = _seed_8_uk_stations(db_path, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.delenv("OPENAQ_API_KEY", raising=False)
    monkeypatch.setenv("SENSORGUARD_POLLING_KILL_SWITCH", "false")

    try:
        # 1. Default dry-run via CLI -> 0 attempts, exit 0
        art_dry = tmp_path / "dry.json"
        monkeypatch.setattr(sys, "argv", ["sensorguard", "preflight-stations", "--output", str(art_dry)])
        cli_main()
        captured = json.loads(capsys.readouterr().out)
        assert captured["status"] == "DRY_RUN_NO_NETWORK"
        assert captured["dry_run"] is True
        assert captured["attempts_consumed"] == 0
        assert captured["recurring_polling_opt_in_enabled"] is False
        assert len(captured["locations"]) == 8
        assert art_dry.exists()

        # 2. Hard cap > 16 must fail validation immediately
        with pytest.raises(ValueError, match="between 1 and 16"):
            Stage1PreflightConfig(execute_once=True, max_attempts=17)

        # 3. Even if legacy env var is manually exported without a DB token -> MUST fail closed
        monkeypatch.setenv("SENSORGUARD_STAGE1_PREFLIGHT_APPROVED", "true")
        calls = 0

        def _fail_if_called(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={"results": []})

        blocked = run_stage1_preflight(
            factory,
            Stage1PreflightConfig(execute_once=True, write_artifact=False),
            api_key="dummy-key-never-used",
            transport=httpx.MockTransport(_fail_if_called),
        )
        assert blocked["status"] == "BLOCKED_NO_SINGLE_USE_APPROVAL"
        assert blocked["attempts_consumed"] == 0
        assert calls == 0

        # 4. Environment kill switch MUST block even WITH valid unused single-use DB approval (without consuming it)
        with factory() as s:
            tok = authorize_stage1_preflight_db(s, authorized_by="dinesh_ops")
        monkeypatch.setenv("SENSORGUARD_POLLING_KILL_SWITCH", "true")
        kill_blocked = run_stage1_preflight(
            factory,
            Stage1PreflightConfig(execute_once=True, approval_id=tok.approval_id, write_artifact=False),
            api_key="dummy-key-never-used",
            transport=httpx.MockTransport(_fail_if_called),
        )
        assert kill_blocked["status"] == "BLOCKED_KILL_SWITCH"
        assert calls == 0
        with factory() as chk_s:
            tok_after = chk_s.scalars(
                select(Stage1PreflightAuthorization).where(
                    Stage1PreflightAuthorization.approval_id == tok.approval_id
                )
            ).one()
            assert tok_after.status == "AUTHORIZED"
            assert tok_after.attempts_consumed == 0
    finally:
        engine.dispose()


def test_stage1_single_use_cli_duplicate_concurrency_and_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test authorize-stage1 CLI, single-use rejection, concurrent claim race, crash restart & recovery budget."""
    db_path = tmp_path / "preflight_single_use.db"
    engine, factory = _seed_8_uk_stations(db_path, monkeypatch)
    now = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("SENSORGUARD_POLLING_KILL_SWITCH", "false")
    monkeypatch.delenv("SENSORGUARD_STAGE1_APPROVAL_ID", raising=False)

    def _clean_mock(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/v3/locations/"):
            loc_id = int(path.rsplit("/", 1)[-1])
            sens_id = 1000 + loc_id
            dt_last = (
                (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
                if loc_id in (154, 157)
                else "2026-05-26T13:00:00Z"
            )
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": loc_id,
                            "name": f"Verified UK #{loc_id}",
                            "provider": {
                                "name": "London Air Quality Network" if loc_id in (154, 157) else "EEA"
                            },
                            "licenses": [{"name": "UK OGL" if loc_id in (154, 157) else "ODC-BY"}],
                            "datetimeLast": {"utc": dt_last},
                            "sensors": [{"id": sens_id, "parameter": {"name": "pm25"}}],
                        }
                    ]
                },
            )
        if path.startswith("/v3/sensors/") and path.endswith("/hours"):
            ts_obs = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            return httpx.Response(
                200,
                json={"results": [{"period": {"datetimeTo": {"utc": ts_obs}}, "value": 14.2}]},
            )
        return httpx.Response(404, json={"results": []})

    try:
        # 1. Mint single-use approval via authorize-stage1 CLI (requires --confirm-single-use)
        monkeypatch.setattr(
            sys,
            "argv",
            ["sensorguard", "authorize-stage1", "--authorized-by", "dinesh_ops", "--confirm-single-use"],
        )
        cli_main()
        minted = json.loads(capsys.readouterr().out)
        assert minted["status"] == "AUTHORIZED"
        assert minted["single_use"] is True
        assert minted["max_attempts"] == 16
        assert minted["remaining_attempts"] == 16
        approval_id = minted["approval_id"]

        # Dry-run shows pending unused approval without consuming it
        dry = run_stage1_preflight(factory, Stage1PreflightConfig(write_artifact=False), now_utc=now)
        assert approval_id in dry["pending_single_use_approvals"]

        # 2. Consume approval once
        res1 = run_stage1_preflight(
            factory,
            Stage1PreflightConfig(execute_once=True, approval_id=approval_id, write_artifact=False),
            api_key="dummy-key",
            transport=httpx.MockTransport(_clean_mock),
            now_utc=now,
        )
        assert res1["status"] == "COMPLETED"
        assert res1["attempts_consumed"] == 16
        assert res1["recurring_polling_opt_in_enabled"] is False

        # 3. Duplicate execution reusing the SAME approval_id MUST fail closed with 0 HTTP calls
        dup_hits = 0

        def _counting_mock(req: httpx.Request) -> httpx.Response:
            nonlocal dup_hits
            dup_hits += 1
            return _clean_mock(req)

        res_dup = run_stage1_preflight(
            factory,
            Stage1PreflightConfig(execute_once=True, approval_id=approval_id, write_artifact=False),
            api_key="dummy-key",
            transport=httpx.MockTransport(_counting_mock),
            now_utc=now,
        )
        assert res_dup["status"] == "BLOCKED_APPROVAL_ALREADY_USED"
        assert res_dup["attempts_consumed"] == 0
        assert dup_hits == 0

        # 4. Simulated CLI / process restart without minting a fresh DB token MUST fail closed
        fresh_eng = create_engine(
            f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False}
        )
        fresh_factory = sessionmaker(
            bind=fresh_eng, autoflush=False, autocommit=False, expire_on_commit=False
        )
        res_restart = run_stage1_preflight(
            fresh_factory,
            Stage1PreflightConfig(execute_once=True, write_artifact=False),
            api_key="dummy-key",
            transport=httpx.MockTransport(_counting_mock),
            now_utc=now,
        )
        fresh_eng.dispose()
        assert res_restart["status"] == "BLOCKED_NO_SINGLE_USE_APPROVAL"
        assert dup_hits == 0

        # 5. Concurrent worker race against a SINGLE approval_id: ONLY ONE claims & consumes <=16 HTTP calls
        with factory() as s:
            race_tok = authorize_stage1_preflight_db(
                s, authorized_by="dinesh_ops", now_utc=now + timedelta(days=1)
            )
        race_hits = 0
        race_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def _race_worker(w_idx: int) -> dict:
            w_eng = create_engine(
                f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False}
            )
            with w_eng.connect() as c:
                c.exec_driver_sql("PRAGMA busy_timeout=5000")
            w_factory = sessionmaker(bind=w_eng, autoflush=False, autocommit=False, expire_on_commit=False)

            def _w_transport(req: httpx.Request) -> httpx.Response:
                nonlocal race_hits
                with race_lock:
                    race_hits += 1
                return _clean_mock(req)

            try:
                barrier.wait(timeout=5)
                return run_stage1_preflight(
                    w_factory,
                    Stage1PreflightConfig(
                        execute_once=True,
                        approval_id=race_tok.approval_id,
                        worker_id=f"race-worker-{w_idx}",
                        write_artifact=False,
                    ),
                    api_key="dummy-key",
                    transport=httpx.MockTransport(_w_transport),
                    now_utc=now + timedelta(days=1),
                )
            finally:
                w_eng.dispose()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            r1, r2 = list(pool.map(_race_worker, [1, 2]))
        statuses = sorted([r1["status"], r2["status"]])
        assert statuses[1] == "COMPLETED"
        assert statuses[0] in {"BLOCKED_APPROVAL_ALREADY_USED", "BLOCKED_CONCURRENT_CLAIM"}
        assert race_hits <= 16

        # 6. Crash/incomplete run (FAILED_INCOMPLETE with 6 consumed): cannot reuse or reset budget;
        #    explicit recovery carries remaining budget (16 - 6 = 10) unless budget reset explicitly approved.
        with factory() as s:
            crash_tok = authorize_stage1_preflight_db(
                s, authorized_by="dinesh_ops", max_attempts=16, now_utc=now + timedelta(days=2)
            )
            crash_tok.status = "CLAIMED_IN_PROGRESS"
            crash_tok.execution_id = "exec_simulated_crash_001"
            crash_tok.attempts_consumed = 6
            s.commit()
            assert mark_stage1_incomplete_or_aborted(
                s,
                approval_id=crash_tok.approval_id,
                execution_id="exec_simulated_crash_001",
                error_summary="Simulated SIGKILL mid-preflight after 6 HTTP requests",
            )

        # Direct retry of incomplete crash approval MUST be blocked
        crash_retry_hits = 0

        def _crash_mock(req: httpx.Request) -> httpx.Response:
            nonlocal crash_retry_hits
            crash_retry_hits += 1
            return _clean_mock(req)

        blocked_crash = run_stage1_preflight(
            factory,
            Stage1PreflightConfig(execute_once=True, approval_id=crash_tok.approval_id, write_artifact=False),
            api_key="dummy-key",
            transport=httpx.MockTransport(_crash_mock),
            now_utc=now + timedelta(days=2),
        )
        assert blocked_crash["status"] == "BLOCKED_APPROVAL_ALREADY_USED"
        assert crash_retry_hits == 0

        # Audited recovery without budget reset grants ONLY remaining 10 attempts (16 - 6)
        with factory() as s:
            rec_tok = authorize_stage1_recovery_db(
                s,
                prior_approval_id=crash_tok.approval_id,
                authorized_by="dinesh_ops",
                reason="Audited operator recovery from incomplete probe after 6 requests",
            )
            assert rec_tok.max_attempts == 10
            assert rec_tok.recovered_from_approval_id == crash_tok.approval_id
            old_row = s.scalars(
                select(Stage1PreflightAuthorization).where(
                    Stage1PreflightAuthorization.approval_id == crash_tok.approval_id
                )
            ).one()
            assert old_row.status == "SUPERSEDED_RECOVERED"

        rec_res = run_stage1_preflight(
            factory,
            Stage1PreflightConfig(execute_once=True, approval_id=rec_tok.approval_id, write_artifact=False),
            api_key="dummy-key",
            transport=httpx.MockTransport(_crash_mock),
            now_utc=now + timedelta(days=2),
        )
        assert rec_res["hard_attempt_cap"] == 10
        assert rec_res["attempts_consumed"] <= 10
        assert crash_retry_hits <= 10

        # Attempting recovery on an exhausted token with allow_budget_reset=False MUST raise
        with factory() as s, pytest.raises(ValueError, match="already consumed"):
            authorize_stage1_recovery_db(
                s,
                prior_approval_id=rec_tok.approval_id,
                authorized_by="dinesh_ops",
                reason="Should reject without allow_budget_reset",
                allow_budget_reset=False,
            )
    finally:
        engine.dispose()


def test_stage1_preflight_bounded_mock_checks_retries_quota_and_secret_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Test retry cap exhaustion <=16, <16 cap, DB fail-closed behavior, and zero secret leaks."""
    db_path = tmp_path / "preflight_live_mock.db"
    engine, factory = _seed_8_uk_stations(db_path, monkeypatch)
    now = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    secret_api_key = "SUPER_SECRET_OPENAQ_KEY_987654"
    secret_db_pwd = "SECRET_PG_PASS_43210"
    monkeypatch.setenv("SENSORGUARD_POLLING_KILL_SWITCH", "false")
    monkeypatch.setenv("OPENAQ_API_KEY", secret_api_key)
    monkeypatch.setenv("DATABASE_URL", f"postgresql://postgres:{secret_db_pwd}@127.0.0.1:5432/sensorguard")

    try:
        # Case 1: Retry exhaustion stops strictly at hard ceiling 16 without emitting request #17
        with factory() as s:
            tok_retry = authorize_stage1_preflight_db(s, authorized_by="dinesh_ops", now_utc=now)
        retry_hits = 0

        def mock_500_storm(request: httpx.Request) -> httpx.Response:
            nonlocal retry_hits
            retry_hits += 1
            return httpx.Response(500, json={"detail": f"x-api-key={secret_api_key} upstream fail"})

        art_retry = tmp_path / "stage1_retry.json"
        res_retry = run_stage1_preflight(
            factory,
            Stage1PreflightConfig(
                execute_once=True, approval_id=tok_retry.approval_id, max_attempts=16, output_path=art_retry
            ),
            api_key=secret_api_key,
            transport=httpx.MockTransport(mock_500_storm),
            now_utc=now,
        )
        assert retry_hits == 16
        assert res_retry["attempts_consumed"] == 16
        assert res_retry["budget_exhausted"] is True
        assert res_retry["status"] == "QUOTA_EXHAUSTED"
        assert res_retry["summary"]["unknown"] == 8

        # Case 2: Strict budget exhaustion under smaller cap (<16) halts BEFORE request #7
        with factory() as s:
            tok_cap6 = authorize_stage1_preflight_db(
                s, authorized_by="dinesh_ops", max_attempts=6, now_utc=now + timedelta(days=1)
            )
        art_cap6 = tmp_path / "stage1_cap6.json"
        cap6_hits = 0

        def mock_cap6(request: httpx.Request) -> httpx.Response:
            nonlocal cap6_hits
            cap6_hits += 1
            return httpx.Response(500, json={"detail": f"x-api-key={secret_api_key} err"})

        res_cap6 = run_stage1_preflight(
            factory,
            Stage1PreflightConfig(
                execute_once=True, approval_id=tok_cap6.approval_id, max_attempts=6, output_path=art_cap6
            ),
            api_key=secret_api_key,
            transport=httpx.MockTransport(mock_cap6),
            now_utc=now + timedelta(days=1),
        )
        assert cap6_hits == 6
        assert res_cap6["attempts_consumed"] == 6
        assert res_cap6["budget_exhausted"] is True
        assert res_cap6["status"] == "QUOTA_EXHAUSTED"

        # Verify ZERO secret strings in artifacts, logs, or audit rows, and recurring polling stays locked
        assert secret_api_key not in art_retry.read_text(encoding="utf-8")
        assert secret_db_pwd not in art_retry.read_text(encoding="utf-8")
        assert secret_api_key not in art_cap6.read_text(encoding="utf-8")
        assert secret_api_key not in caplog.text
        with factory() as aud_s:
            for aud in aud_s.scalars(select(PilotIngestAuditRun)).all():
                assert secret_api_key not in str(aud.error_summary or "")
            live_ctrl = aud_s.scalars(
                select(PilotControlState).where(PilotControlState.control_name == DEFAULT_CONTROL_NAME)
            ).one()
            assert live_ctrl.opt_in_enabled is False
            assert live_ctrl.kill_switch_active is True

        # Case 3: Database failure (dropped PilotQuotaState) fails closed BEFORE issuing any HTTP request
        PilotQuotaState.__table__.drop(bind=engine)
        http_on_db_fail = 0

        def mock_should_never_run(_req: httpx.Request) -> httpx.Response:
            nonlocal http_on_db_fail
            http_on_db_fail += 1
            return httpx.Response(200, json={"results": []})

        res_dbfail = run_stage1_preflight(
            factory,
            Stage1PreflightConfig(execute_once=True, max_attempts=16, write_artifact=False),
            api_key=secret_api_key,
            transport=httpx.MockTransport(mock_should_never_run),
            now_utc=now + timedelta(days=2),
        )
        assert res_dbfail["status"] == "BLOCKED_DB_UNAVAILABLE"
        assert res_dbfail["attempts_consumed"] == 0
        assert http_on_db_fail == 0
    finally:
        engine.dispose()
