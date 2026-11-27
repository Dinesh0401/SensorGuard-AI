"""Offline unit and adversarial safety tests for the Stage 1 OpenAQ pre-flight CLI (Task A)."""

from __future__ import annotations

import json
import sys
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
    Station,
)
from sensorguard.pilot_monitor import DEFAULT_CONTROL_NAME
from sensorguard.preflight import (
    STAGE1_ARCHIVED_LOCATION_IDS,
    Stage1PreflightConfig,
    authorize_stage1_preflight_db,
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


def test_stage1_preflight_defaults_to_dry_run_and_blocks_unauthorized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Default CLI invocation is dry-run with zero HTTP calls and refuses unapproved execute-once."""
    db_path = tmp_path / "preflight_dry.db"
    engine, factory = _seed_8_uk_stations(db_path, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.delenv("OPENAQ_API_KEY", raising=False)
    monkeypatch.delenv("SENSORGUARD_STAGE1_PREFLIGHT_APPROVED", raising=False)
    monkeypatch.delenv("SENSORGUARD_STAGE1_AUTHORIZED_BY", raising=False)
    monkeypatch.setenv("SENSORGUARD_POLLING_KILL_SWITCH", "false")

    try:
        # 1. Default dry-run via CLI subprocess-equivalent invocation -> 0 attempts, exit 0
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

        # 3. Execute-once without Stage 1 authorization MUST fail closed without HTTP calls
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
        assert blocked["status"] == "BLOCKED_NOT_AUTHORIZED"
        assert blocked["attempts_consumed"] == 0
        assert calls == 0

        # 4. Environment kill switch MUST block even with Stage 1 DB authorization
        with factory() as s:
            authorize_stage1_preflight_db(s, authorized_by="dinesh_ops")
        monkeypatch.setenv("SENSORGUARD_POLLING_KILL_SWITCH", "true")
        kill_blocked = run_stage1_preflight(
            factory,
            Stage1PreflightConfig(execute_once=True, write_artifact=False),
            api_key="dummy-key-never-used",
            transport=httpx.MockTransport(_fail_if_called),
        )
        assert kill_blocked["status"] == "BLOCKED_KILL_SWITCH"
        assert calls == 0
    finally:
        engine.dispose()


def test_stage1_preflight_bounded_mock_checks_retries_quota_and_secret_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Test bounded <=16-attempt execution, retry cap exhaustion, DB failure, and zero secret leaks."""
    db_path = tmp_path / "preflight_live_mock.db"
    engine, factory = _seed_8_uk_stations(db_path, monkeypatch)
    now = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    secret_api_key = "SUPER_SECRET_OPENAQ_KEY_987654"
    secret_db_pwd = "SECRET_PG_PASS_43210"
    monkeypatch.setenv("SENSORGUARD_POLLING_KILL_SWITCH", "false")
    monkeypatch.setenv("OPENAQ_API_KEY", secret_api_key)
    monkeypatch.setenv("DATABASE_URL", f"postgresql://postgres:{secret_db_pwd}@127.0.0.1:5432/sensorguard")

    try:
        with factory() as s:
            authorize_stage1_preflight_db(s, authorized_by="dinesh_ops", now_utc=now)

        # Case 1: Clean full run across 8 stations (8 location metadata + up to 8 hourly checks <= 16 attempts!)
        request_log: list[str] = []

        def mock_clean_handler(request: httpx.Request) -> httpx.Response:
            request_log.append(request.url.path)
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
                sens_id = int(path.split("/")[3])
                loc_id = sens_id - 1000
                if loc_id in (154, 157):
                    ts_obs = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
                    return httpx.Response(
                        200,
                        json={"results": [{"period": {"datetimeTo": {"utc": ts_obs}}, "value": 12.4}]},
                    )
                return httpx.Response(200, json={"results": []})
            return httpx.Response(404, json={"results": []})

        art_file = tmp_path / "stage1_clean.json"
        res = run_stage1_preflight(
            factory,
            Stage1PreflightConfig(execute_once=True, max_attempts=16, output_path=art_file),
            api_key=secret_api_key,
            transport=httpx.MockTransport(mock_clean_handler),
            now_utc=now,
        )
        assert res["status"] == "COMPLETED"
        assert res["attempts_consumed"] == 16
        assert len(request_log) == 16
        assert res["recurring_polling_opt_in_enabled"] is False
        assert res["summary"] == {"total_locations": 8, "active": 2, "inactive": 6, "unknown": 0}
        # Recurring Stage 2 row MUST still be strictly locked & opt_in_enabled=False in DB
        with factory() as chk_s:
            live_ctrl = chk_s.scalars(
                select(PilotControlState).where(PilotControlState.control_name == DEFAULT_CONTROL_NAME)
            ).one()
            assert live_ctrl.opt_in_enabled is False
            assert live_ctrl.kill_switch_active is True
            q_row = chk_s.scalars(select(PilotQuotaState)).one()
            assert q_row.requests_used == 16

        # Case 2: Retry exhaustion stops strictly at hard ceiling 16 without emitting request #17
        retry_hits = 0

        def mock_500_storm(request: httpx.Request) -> httpx.Response:
            nonlocal retry_hits
            retry_hits += 1
            # Inject mock secret-like query/token echo into error context to verify redaction
            return httpx.Response(500, json={"detail": f"x-api-key={secret_api_key} upstream fail"})

        art_retry = tmp_path / "stage1_retry.json"
        res_retry = run_stage1_preflight(
            factory,
            Stage1PreflightConfig(execute_once=True, max_attempts=16, output_path=art_retry),
            api_key=secret_api_key,
            transport=httpx.MockTransport(mock_500_storm),
            now_utc=now + timedelta(days=1),
        )
        assert retry_hits == 16
        assert res_retry["attempts_consumed"] == 16
        assert res_retry["budget_exhausted"] is True
        assert res_retry["status"] == "QUOTA_EXHAUSTED"
        assert res_retry["summary"]["unknown"] == 8

        # Also test strict budget exhaustion under smaller cap (<16) halts BEFORE attempting request #7
        art_cap6 = tmp_path / "stage1_cap6.json"
        cap6_hits = 0

        def mock_cap6(request: httpx.Request) -> httpx.Response:
            nonlocal cap6_hits
            cap6_hits += 1
            return httpx.Response(500, json={"detail": f"x-api-key={secret_api_key} err"})

        res_cap6 = run_stage1_preflight(
            factory,
            Stage1PreflightConfig(execute_once=True, max_attempts=6, output_path=art_cap6),
            api_key=secret_api_key,
            transport=httpx.MockTransport(mock_cap6),
            now_utc=now + timedelta(days=3),
        )
        assert cap6_hits == 6
        assert res_cap6["attempts_consumed"] == 6
        assert res_cap6["budget_exhausted"] is True
        assert res_cap6["status"] == "QUOTA_EXHAUSTED"

        # Verify ZERO secret strings in artifacts, logs, or audit rows
        assert secret_api_key not in art_file.read_text(encoding="utf-8")
        assert secret_api_key not in art_retry.read_text(encoding="utf-8")
        assert secret_db_pwd not in art_retry.read_text(encoding="utf-8")
        assert secret_api_key not in caplog.text
        with factory() as aud_s:
            for aud in aud_s.scalars(select(PilotIngestAuditRun)).all():
                assert secret_api_key not in str(aud.error_summary or "")

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
