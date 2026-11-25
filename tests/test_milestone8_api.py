"""Milestone 8 API governance, model-serving separation, and pilot readiness tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sensorguard.api import app, get_db
from sensorguard.db.schema import Base, PilotControlState, Sensor, Station
from sensorguard.models.registry import ALLOWED_DATA_MODES


def test_health_and_model_status_explicit_serving_separation() -> None:
    """Verify /health and /api/v1/model/status explicitly separate Static SAGE from shadow models."""
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        hd = health.json()
        assert hd["operational_data_mode"] == "archived_historical"
        assert hd["allowed_data_modes"] == list(ALLOWED_DATA_MODES)

        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["model_version"] == "1.0.0"

        model_status = client.get("/api/v1/model/status")
        assert model_status.status_code == 200
        ms = model_status.json()
        assert ms["model_id"] == "sage_pilot_q1_2024"
        assert ms["algorithm"] == "SAGE-LIGHTGBM"
        assert ms["model_class"] == "SAGEForecaster"
        assert ms["serving_role"] == "primary_serving"
        assert ms["operational_data_mode"] == "archived_historical"
        shadow_ids = {m["model_id"]: m for m in ms["shadow_models"]}
        assert "sage_adaptive_q1_2024" in shadow_ids
        assert shadow_ids["sage_adaptive_q1_2024"]["model_class"] == "AdaptiveSAGEForecaster"
        assert shadow_ids["sage_adaptive_q1_2024"]["promoted_to_primary"] is False

        forecasts_resp = client.get("/api/v1/stations/1/forecasts?limit=4")
        assert forecasts_resp.status_code == 200
        forecasts = forecasts_resp.json()["forecasts"]
        assert len(forecasts) > 0
        assert "SAGEForecaster" in forecasts[0]["model_version"]


def test_pilot_readiness_endpoint_fail_closed_default_and_migrated_db(tmp_path: Path) -> None:
    """Verify GET /api/v1/pilot/readiness works safely on both default SQLite and migrated 006 DB."""
    # 1. Against default app DB (fail-closed regardless of Alembic 004 vs 006 state)
    with TestClient(app) as client:
        resp = client.get("/api/v1/pilot/readiness")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["primary_model_id"] == "sage_pilot_q1_2024"
        assert payload["primary_model_class"] == "SAGEForecaster"
        assert payload["adaptive_sage_promoted"] is False
        assert payload["kill_switch_active"] is True
        assert payload["opt_in_enabled"] is False
        assert payload["allowed_data_modes"] == list(ALLOWED_DATA_MODES)

    # 2. Against isolated migrated 006 schema verifying DB control state and station metadata audit
    eng = create_engine(f"sqlite:///{tmp_path / 'api_006.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    sf = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)

    with sf() as sess:
        st = Station(
            provider="openaq",
            provider_location_id=154,
            name="Westminster - Marylebone Road",
            country="GB",
            latitude=51.5225,
            longitude=-0.1546,
            metadata_json='{"provider": {"name": "London Air Quality Network"}, "licenses": [{"name": "UK OGL"}], "datetimeLast": {"utc": "2026-11-25T11:00:00Z"}}',
            updated_at=now,
        )
        sess.add(st)
        sess.flush()
        sess.add(Sensor(station_id=st.id, provider_sensor_id=246, parameter="pm25", canonical_unit="µg/m³"))
        sess.add(
            PilotControlState(
                control_name="openaq_live_polling",
                kill_switch_active=True,
                opt_in_enabled=False,
                reason="Unit test locked state",
                updated_by="pytest",
                updated_at=now,
            )
        )
        sess.commit()

    def _override_db():
        with sf() as s:
            yield s

    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as client:
            r2 = client.get("/api/v1/pilot/readiness")
            assert r2.status_code == 200
            d2 = r2.json()
            assert d2["control_source"] == "database_006"
            assert d2["kill_switch_active"] is True
            assert d2["opt_in_enabled"] is False
            assert d2["requests_remaining"] == 48
            assert d2["station_metadata_audit"]["total_stations"] == 1
            assert d2["shadow_evaluation_summary"]["status"] == "NO_SHADOW_RUNS"
    finally:
        app.dependency_overrides.clear()
        eng.dispose()
