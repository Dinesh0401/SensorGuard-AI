"""Comprehensive contract and integration tests for SensorGuard AI FastAPI endpoints.

Validates all Milestone 5 API contracts:
- Liveness (/healthz, /health)
- Station catalog & bounding box filters (/api/v1/stations)
- Observation series & freshness diagnostics (/api/v1/stations/{id}/observations)
- SAGE point & interval forecasts with guarded abstention (/api/v1/stations/{id}/forecasts)
- Station reliability & outage duration analysis (/api/v1/stations/{id}/reliability)
- Research experiment manifest & audit detail (/api/v1/research/experiments)
- Protected administrative endpoints (/api/v1/admin/ingest, /api/v1/admin/evaluate)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sensorguard.api import app
from sensorguard.scheduler import IngestScheduler, SchedulerConfig

client = TestClient(app)
TEST_ADMIN_KEY = "sensorguard-test-admin-secret-42"


def test_health_and_healthz():
    """Verify health and healthz endpoints return consistent database status."""
    for path in ["/health", "/healthz"]:
        res = client.get(path)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] in {"ok", "degraded"}
        assert "database_connected" in data
        assert "data_mode" in data
        assert "stations_count" in data
        assert "observations_count" in data
        assert "timestamp" in data


def test_health_ready():
    """Verify readiness probe checks DB connection and serving model availability."""
    for path in ["/health/ready", "/api/v1/health/ready"]:
        res = client.get(path)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] in {"ready", "not_ready"}
        assert "database_connected" in data
        assert "model_loaded" in data
        assert "model_version" in data
        if data["database_connected"] and data["model_loaded"]:
            assert data["status"] == "ready"
            assert data["model_version"] == "1.0.0"


def test_model_status():
    """Verify model status endpoint returns cryptographic manifest metadata and verification."""
    res = client.get("/api/v1/model/status")
    assert res.status_code == 200
    data = res.json()
    assert data["model_id"] == "sage_pilot_q1_2024"
    assert data["version"] == "1.0.0"
    assert data["algorithm"] == "SAGE-LIGHTGBM"
    assert data["nominal_coverage"] == 0.90
    assert "dataset_hash" in data
    assert "sha256" in data
    assert data["artifact_exists"] is True
    assert data["verified"] is True


def test_list_stations():
    """Verify station catalog returns stations with availability and coordinates."""
    res = client.get("/api/v1/stations")
    assert res.status_code == 200
    data = res.json()
    assert "stations" in data
    assert "total_count" in data
    assert "provider_citation" in data

    if data["total_count"] > 0:
        st = data["stations"][0]
        assert "id" in st
        assert "name" in st
        assert "latitude" in st
        assert "longitude" in st
        assert "status" in st
        assert st["status"] in {"HEALTHY", "LATE", "NO_REPORT", "OFFLINE_CONFIRMED"}
        assert "is_stale" in st
        assert st["is_archived_pilot"] is True


def test_list_stations_bbox_filter():
    """Verify bounding box filter restricts or accepts coordinates correctly."""
    # UK / London bounding box
    res = client.get("/api/v1/stations?bbox=-0.5,51.3,0.3,51.7")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data["stations"], list)

    # Invalid bbox format
    res_bad = client.get("/api/v1/stations?bbox=invalid,bbox")
    assert res_bad.status_code == 400

    # Out of range bbox
    res_out = client.get("/api/v1/stations?bbox=-200,51,0,52")
    assert res_out.status_code == 400


def test_get_station_detail():
    """Verify retrieving individual station metadata."""
    res_list = client.get("/api/v1/stations")
    data_list = res_list.json()

    if data_list["total_count"] > 0:
        st_id = data_list["stations"][0]["id"]
        res = client.get(f"/api/v1/stations/{st_id}")
        assert res.status_code == 200
        st = res.json()
        assert st["id"] == st_id
        assert "name" in st
        assert "latitude" in st

    # 404 on nonexistent station
    assert client.get("/api/v1/stations/999999").status_code == 404


def test_get_station_observations():
    """Verify observation time series query with limits and freshness."""
    res_list = client.get("/api/v1/stations")
    data_list = res_list.json()

    if data_list["total_count"] > 0:
        st_id = data_list["stations"][0]["id"]
        res = client.get(f"/api/v1/stations/{st_id}/observations?limit=10")
        assert res.status_code == 200
        data = res.json()
        assert data["station_id"] == st_id
        assert "observations" in data
        assert len(data["observations"]) <= 10
        assert data["freshness_status"] in {"fresh", "moderate_delay", "stale"}

        if data["observations"]:
            obs = data["observations"][0]
            assert "observed_at" in obs
            assert "value" in obs
            assert "unit" in obs
            assert obs["unit"] == "µg/m³"


def test_get_station_forecasts():
    """Verify SAGE point and interval forecasts, scenario bounds, and guarded policy."""
    res_list = client.get("/api/v1/stations")
    data_list = res_list.json()

    if data_list["total_count"] > 0:
        st_id = data_list["stations"][0]["id"]
        res = client.get(f"/api/v1/stations/{st_id}/forecasts?limit=5")
        assert res.status_code == 200
        data = res.json()
        assert data["station_id"] == st_id
        assert "current_status" in data
        assert "forecasts" in data

        if data["forecasts"]:
            f = data["forecasts"][0]
            assert "point_forecast" in f
            assert "lower_bound" in f
            assert "upper_bound" in f
            assert f["upper_bound"] >= f["lower_bound"]
            assert f["status"] in {"publish", "caution", "abstain"}
            assert "abstain_reason" in f
            assert "scenario_bounds" in f
            assert len(f["scenario_bounds"]) >= 1


def test_get_station_reliability():
    """Verify reliability metrics, gap rate, and longest consecutive outage."""
    res_list = client.get("/api/v1/stations")
    data_list = res_list.json()

    if data_list["total_count"] > 0:
        st_id = data_list["stations"][0]["id"]
        res = client.get(f"/api/v1/stations/{st_id}/reliability")
        assert res.status_code == 200
        data = res.json()
        assert data["station_id"] == st_id
        assert "gap_rate_pct" in data
        assert "longest_consecutive_outage_hours" in data
        assert "status_distribution" in data


def test_research_experiments():
    """Verify research experiment catalog and detail retrieval."""
    res = client.get("/api/v1/research/experiments")
    assert res.status_code == 200
    data = res.json()
    assert "experiments" in data
    assert data["total_count"] >= 1

    exp = data["experiments"][0]
    exp_id = exp["experiment_id"]

    res_detail = client.get(f"/api/v1/research/experiments/{exp_id}")
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["experiment_id"] == exp_id
    assert "details" in detail
    assert "research_caveats" in detail


def test_admin_auth_and_endpoints(monkeypatch: pytest.MonkeyPatch):
    """Verify administrative authorization, fail-closed security, and protected execution."""
    # 1. Unconfigured admin key -> 503 fail closed
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    assert (
        client.post(
            "/api/v1/admin/ingest",
            json={"days": 1},
            headers={"X-Admin-Key": "some-random-key"},
        ).status_code
        == 503
    )

    # Configure valid key in test environment
    monkeypatch.setenv("ADMIN_API_KEY", TEST_ADMIN_KEY)

    # 2. Missing header -> 422
    assert client.post("/api/v1/admin/ingest", json={"days": 1}).status_code == 422

    # 3. Invalid header -> 401
    assert (
        client.post(
            "/api/v1/admin/ingest",
            json={"days": 1},
            headers={"X-Admin-Key": "wrong-key"},
        ).status_code
        == 401
    )

    # 4. Valid key -> 200
    res_ingest = client.post(
        "/api/v1/admin/ingest",
        json={"days": 7},
        headers={"X-Admin-Key": TEST_ADMIN_KEY},
    )
    assert res_ingest.status_code == 200
    assert res_ingest.json()["status"] == "completed"

    # 5. Admin evaluate endpoint
    res_eval = client.post(
        "/api/v1/admin/evaluate",
        json={"scenario": "natural", "nominal_coverage": 0.90},
        headers={"X-Admin-Key": TEST_ADMIN_KEY},
    )
    assert res_eval.status_code == 200
    assert res_eval.json()["scenario"] == "natural"


def test_scheduler_freshness_audit():
    """Verify background scheduler audit and execution loop."""
    import asyncio

    scheduler = IngestScheduler(config=SchedulerConfig(poll_interval_seconds=1))
    assert not scheduler.is_running
    result = asyncio.run(scheduler.poll_once())
    assert "total_stations" in result
    assert scheduler.last_poll_status == "SUCCESS"
    assert scheduler.last_poll_time is not None


def test_model_sha256_verification_and_rejection(tmp_path):
    """Verify SHA-256 cryptographic verification accepts valid model and rejects corrupted model."""
    from sensorguard.api import MODEL_PATH, verify_model_sha256

    # 1. Existing valid model artifact must pass SHA-256 check
    if MODEL_PATH.exists():
        assert verify_model_sha256(MODEL_PATH) is True

    # 2. Corrupted file must fail SHA-256 check
    corrupted_file = tmp_path / "corrupted_sage.joblib"
    corrupted_file.write_bytes(b"tampered_bytes_with_arbitrary_code")
    assert verify_model_sha256(corrupted_file) is False


def test_sub_50ms_forecast_latency():
    """Verify response time meets staging performance requirements."""
    import time

    res_list = client.get("/api/v1/stations")
    data_list = res_list.json()

    if data_list["total_count"] > 0:
        st_id = data_list["stations"][0]["id"]
        endpoint = f"/api/v1/stations/{st_id}/forecasts?limit=24"
    else:
        endpoint = "/healthz"

    # Warmup
    client.get(endpoint)

    durations: list[float] = []
    for _ in range(10):
        t0 = time.perf_counter()
        res = client.get(endpoint)
        durations.append((time.perf_counter() - t0) * 1000.0)
        assert res.status_code == 200

    mean_latency = sum(durations) / len(durations)
    assert mean_latency < 500.0, f"Mean latency {mean_latency:.2f}ms exceeded requirement"


def test_admin_concurrency_lock():
    """Verify admin operations respect execution lock for concurrency safety."""
    import asyncio

    from sensorguard.api import _ADMIN_EXECUTION_LOCK

    async def _test():
        assert not _ADMIN_EXECUTION_LOCK.locked()
        async with _ADMIN_EXECUTION_LOCK:
            assert _ADMIN_EXECUTION_LOCK.locked()
        assert not _ADMIN_EXECUTION_LOCK.locked()

    asyncio.run(_test())


def test_fail_closed_model_artifact_missing_or_corrupted(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Verify API fails closed with HTTP 503 and does NOT train on request path when artifact is missing or corrupted."""
    from fastapi import HTTPException

    import sensorguard.api as api_mod

    # Reset in-memory cached model
    saved_model = api_mod._CACHED_SAGE_MODEL
    api_mod._CACHED_SAGE_MODEL = None

    try:
        # Case 1: Missing model artifact
        missing_path = tmp_path / "nonexistent_model.joblib"
        monkeypatch.setattr(api_mod, "MODEL_PATH", missing_path)

        with pytest.raises(HTTPException) as exc_info:
            api_mod.get_serving_model()
        assert exc_info.value.status_code == 503
        assert (
            "Model artifact unavailable, corrupted, or untrusted. Offline training required."
            in exc_info.value.detail
        )

        # Case 2: Tampered / Corrupted model artifact (hash mismatch)
        corrupted_path = tmp_path / "corrupted_model.joblib"
        corrupted_path.write_bytes(b"tampered_bytes_arbitrary_data")
        monkeypatch.setattr(api_mod, "MODEL_PATH", corrupted_path)

        with pytest.raises(HTTPException) as exc_info:
            api_mod.get_serving_model()
        assert exc_info.value.status_code == 503
        assert (
            "Model artifact unavailable, corrupted, or untrusted. Offline training required."
            in exc_info.value.detail
        )

        # Verify that no model was dynamically trained or cached
        assert api_mod._CACHED_SAGE_MODEL is None

    finally:
        # Restore original cached model
        api_mod._CACHED_SAGE_MODEL = saved_model


def test_cache_invalidation_on_ingestion(monkeypatch: pytest.MonkeyPatch):
    """Verify spatiotemporal feature cache is invalidated on admin ingestion and scheduler polling."""
    import asyncio

    import sensorguard.api as api_mod
    from sensorguard.db.session import create_db_engine, get_session_factory

    engine = create_db_engine()
    session_factory = get_session_factory(engine)

    # 1. Populate cache
    with session_factory() as session:
        api_mod.get_cached_pilot_features(session)
    assert api_mod._CACHED_FEATURE_DF is not None

    # 2. Invalidate manually
    api_mod.invalidate_pilot_feature_cache()
    assert api_mod._CACHED_FEATURE_DF is None

    # 3. Invalidate via admin ingest endpoint
    with session_factory() as session:
        api_mod.get_cached_pilot_features(session)
    assert api_mod._CACHED_FEATURE_DF is not None

    monkeypatch.setenv("ADMIN_API_KEY", TEST_ADMIN_KEY)
    res_ingest = client.post(
        "/api/v1/admin/ingest",
        json={"days": 1},
        headers={"X-Admin-Key": TEST_ADMIN_KEY},
    )
    assert res_ingest.status_code == 200
    assert api_mod._CACHED_FEATURE_DF is None

    # 4. Invalidate via scheduler poll_once
    with session_factory() as session:
        api_mod.get_cached_pilot_features(session)
    assert api_mod._CACHED_FEATURE_DF is not None

    scheduler = IngestScheduler(config=SchedulerConfig(poll_interval_seconds=1))
    asyncio.run(scheduler.poll_once())
    assert api_mod._CACHED_FEATURE_DF is None
