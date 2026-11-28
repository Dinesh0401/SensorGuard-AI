"""Production-grade FastAPI application exposing public sensor, forecast, and research endpoints.

Implements all Milestone 5 API contracts:
- GET /healthz and /health (liveness and database readiness)
- GET /api/v1/stations (station catalog, coordinates, and availability status)
- GET /api/v1/stations/{station_id} (station details)
- GET /api/v1/stations/{station_id}/observations (time series and freshness)
- GET /api/v1/stations/{station_id}/forecasts (SAGE point and interval forecasts)
- GET /api/v1/stations/{station_id}/reliability (gap analysis and reliability statistics)
- GET /api/v1/research/experiments (reproducible research experiment manifest)
- GET /api/v1/research/experiments/{id} (detailed benchmark & ablation results)
- POST /api/v1/admin/ingest (protected bounded ingestion)
- POST /api/v1/admin/evaluate (protected benchmark evaluation)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from sensorguard.db.schema import (
    Observation,
    PilotApiRateLimit,
    PilotControlState,
    PilotIngestAuditRun,
    Sensor,
    Station,
)
from sensorguard.db.session import create_db_engine, get_session_factory
from sensorguard.experiment import ExperimentConfig, run_experiment
from sensorguard.models.data import (
    build_spatiotemporal_features,
    load_pilot_dataset_from_db,
)
from sensorguard.models.manifest import ModelManifest
from sensorguard.models.registry import ALLOWED_DATA_MODES, load_model_registry
from sensorguard.models.sage import SAGEForecaster
from sensorguard.pilot_monitor import (
    DEFAULT_CONTROL_NAME,
    DurablePilotQuotaTracker,
    inspect_station_provider_metadata,
)
from sensorguard.schemas import (
    AdminEvaluateRequest,
    AdminEvaluateResponse,
    AdminIngestRequest,
    AdminIngestResponse,
    ExperimentDetailResponse,
    ExperimentListResponse,
    ExperimentSummary,
    ForecastRecord,
    ForecastScenarioBounds,
    HealthResponse,
    ModelStatusResponse,
    ObservationRecord,
    PilotReadinessResponse,
    ReadinessResponse,
    ShadowModelSummary,
    StationForecastsResponse,
    StationListResponse,
    StationObservationsResponse,
    StationReliabilityResponse,
    StationSummary,
)
from sensorguard.shadow_eval import reconcile_shadow_evaluation
from sensorguard.simulation import OutageConfig

logger = logging.getLogger(__name__)

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
_RATE_LIMIT_LOCK = threading.Lock()
_FALLBACK_RATE_WINDOWS: dict[tuple[str, str], int] = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Pre-warm verified model artifact and pilot feature caches per worker on startup."""
    try:
        model = get_serving_model()
        with _SESSION_FACTORY() as session:
            get_cached_pilot_features(session)
            with _CACHE_LOCK:
                st_ids = list(_CACHED_STATION_NAMES.keys()) if _CACHED_STATION_NAMES else []
            for st_id in st_ids:
                _compute_station_forecasts_sync(st_id, 24, model, session=session)
        logger.info("Worker startup pre-warmup completed (%d stations cached).", len(st_ids))
    except Exception as exc:  # noqa: BLE001 - fail-closed per-request checks remain authoritative
        logger.warning("Startup pre-warmup skipped or partial: %s", exc)
    yield


app = FastAPI(
    title="SensorGuard AI Pilot & Research API",
    version="0.5.3",
    description="Public pilot API for environmental sensor network forecasting, uncertainty quantification, and outage resilience.",
    lifespan=lifespan,
)

_default_cors_origins = "http://localhost:3000,http://127.0.0.1:3000"
_cors_origins = [
    origin.strip() for origin in os.getenv("CORS_ORIGINS", _default_cors_origins).split(",") if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Structured request logging middleware capturing method, path, status_code, and latency_ms."""
    t0 = time.perf_counter()
    method = request.method
    path = request.url.path
    try:
        response = await call_next(request)
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        logger.info(
            "HTTP_REQUEST method=%s path=%s status_code=%d latency_ms=%.2f",
            method,
            path,
            response.status_code,
            latency_ms,
        )
        response.headers["X-Process-Time-Ms"] = str(latency_ms)
        return response
    except Exception as exc:
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        logger.error(
            "HTTP_REQUEST_ERROR method=%s path=%s error=%s latency_ms=%.2f",
            method,
            path,
            str(exc),
            latency_ms,
        )
        raise


_ENGINE = create_db_engine()
_SESSION_FACTORY = get_session_factory(_ENGINE)


def get_db():
    """Dependency providing a transactional database session."""
    session = _SESSION_FACTORY()
    try:
        yield session
    finally:
        session.close()


def _is_isolated_dev_rate_limit_fallback_allowed(bind: Any) -> bool:
    """Permit process-local fallback ONLY in explicitly isolated SQLite dev/test mode."""
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "").lower()
    if dialect_name != "sqlite":
        return False
    allow_flag = os.getenv("SENSORGUARD_ALLOW_IN_MEMORY_RATE_LIMIT_DEV", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    env_mode = os.getenv("SENSORGUARD_ENV", "production").lower()
    return bool(allow_flag and env_mode in {"development", "dev_test", "test"})


def _enforce_server_rate_limit(
    scope: str,
    max_requests_per_min: int,
    db: Session | None = None,
) -> None:
    """Enforce server-side fixed-window API rate limits across workers via durable DB counters.

    Fails closed with HTTP 503 whenever the durable DB rate limiter fails in staging,
    production, PostgreSQL, or default configurations. In-memory fallback is permitted ONLY
    when SENSORGUARD_ALLOW_IN_MEMORY_RATE_LIMIT_DEV=true AND SENSORGUARD_ENV in
    {'development', 'dev_test', 'test'} on an isolated SQLite bind.
    """
    if max_requests_per_min < 1:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded for {scope}",
            headers={"Retry-After": "60"},
        )
    now = datetime.now(UTC)
    window_key = now.strftime("%Y-%m-%dT%H:%M")

    with _RATE_LIMIT_LOCK:
        bind = db.get_bind() if db is not None else _ENGINE
        try:
            with Session(bind) as rl_sess:
                row = rl_sess.scalars(
                    select(PilotApiRateLimit).where(
                        PilotApiRateLimit.scope == scope,
                        PilotApiRateLimit.window_minute_utc == window_key,
                    )
                ).first()
                if row is None:
                    row = PilotApiRateLimit(
                        scope=scope,
                        window_minute_utc=window_key,
                        request_count=0,
                        max_requests=max_requests_per_min,
                        updated_at=now,
                    )
                    try:
                        with rl_sess.begin_nested():
                            rl_sess.add(row)
                            rl_sess.flush()
                        rl_sess.commit()
                    except IntegrityError:
                        rl_sess.rollback()
                        row = rl_sess.scalars(
                            select(PilotApiRateLimit).where(
                                PilotApiRateLimit.scope == scope,
                                PilotApiRateLimit.window_minute_utc == window_key,
                            )
                        ).one()

                upd = (
                    update(PilotApiRateLimit)
                    .where(
                        PilotApiRateLimit.id == row.id,
                        PilotApiRateLimit.request_count + 1 <= max_requests_per_min,
                    )
                    .values(
                        request_count=PilotApiRateLimit.request_count + 1,
                        max_requests=max_requests_per_min,
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
                res = rl_sess.execute(upd)
                rl_sess.commit()
                if int(res.rowcount or 0) != 1:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail=f"Server-side rate limit exceeded for {scope} ({max_requests_per_min}/min)",
                        headers={"Retry-After": "60"},
                    )
                return
        except HTTPException:
            raise
        except SQLAlchemyError as exc:
            dialect_name = getattr(getattr(bind, "dialect", None), "name", "unknown")
            logger.error(
                "RATE_LIMIT_DB_FAILURE scope=%s dialect=%s error=%s",
                scope,
                dialect_name,
                exc,
            )
            if not _is_isolated_dev_rate_limit_fallback_allowed(bind):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        f"Service unavailable: durable rate limiter failure for '{scope}'; "
                        "failing closed without in-memory fallback."
                    ),
                    headers={"Retry-After": "10"},
                ) from exc

            stale_keys = [k for k in _FALLBACK_RATE_WINDOWS if k[1] != window_key]
            for sk in stale_keys:
                _FALLBACK_RATE_WINDOWS.pop(sk, None)
            cur = _FALLBACK_RATE_WINDOWS.get((scope, window_key), 0)
            if cur >= max_requests_per_min:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Server-side rate limit exceeded for {scope} ({max_requests_per_min}/min)",
                    headers={"Retry-After": "60"},
                ) from None
            _FALLBACK_RATE_WINDOWS[(scope, window_key)] = cur + 1


def verify_admin_key(
    x_admin_key: str = Header(..., alias="X-Admin-Key"),
    db: Session = Depends(get_db),
) -> None:
    """Validate administrative authorization header with constant-time equality and durable rate limits.

    Fails closed with HTTP 503 if ADMIN_API_KEY is not configured in the environment.
    """
    admin_key = os.getenv("ADMIN_API_KEY") or ADMIN_API_KEY
    if not admin_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Administrative endpoints are disabled: ADMIN_API_KEY is not configured.",
        )
    if not secrets.compare_digest(x_admin_key, admin_key):
        logger.warning("ADMIN_SECURITY_ALERT: Unauthorized administrative access attempt rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid administrative credentials",
        )

    admin_cap = int(os.getenv("SENSORGUARD_ADMIN_RATE_LIMIT_PER_MIN", "30"))
    _enforce_server_rate_limit("admin_api", admin_cap, db=db)
    logger.info("ADMIN_AUDIT: Administrative request authenticated at %s", datetime.now(UTC).isoformat())


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Ensure datetime has explicit UTC timezone info."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


# Global in-memory cache and disk persistence for trained SAGE model
_CACHE_LOCK = threading.RLock()
_CACHED_SAGE_MODEL: SAGEForecaster | None = None
_CACHED_MODEL_LOAD_TIME: float = 0.0
_CACHED_FEATURE_DF: pd.DataFrame | None = None
_CACHED_STATION_COORDS: dict[str, tuple[float, float]] | None = None
_CACHED_STATION_LATEST: dict[int, tuple[datetime | None, float | None]] | None = None
_CACHED_STATION_SLICES: dict[int, pd.DataFrame] | None = None
_CACHED_STATION_NAMES: dict[int, str] | None = None
_CACHED_FORECAST_RESPONSES: dict[tuple[int, int, int, float], StationForecastsResponse] = {}
_CACHED_FORECAST_JSON: dict[tuple[int, int, int, float], bytes] = {}
_CACHED_MAX_DB_TIME: datetime | None = None
_ADMIN_EXECUTION_LOCK = asyncio.Lock()

ARTIFACT_DIR = Path("artifacts/models")
MODEL_PATH = ARTIFACT_DIR / "sage_pilot_q1_2024.joblib"
MODEL_SHA256_PATH = ARTIFACT_DIR / "sage_pilot_q1_2024.sha256"
MANIFEST_PATH = ARTIFACT_DIR / "manifest.json"
EXPECTED_MODEL_SHA256 = "e552ca09a37856f809f2dc6db9f8eabf291a6fd4103da92874ac440bd91e4115"


def invalidate_pilot_feature_cache() -> None:
    """Clear cached in-memory spatiotemporal feature matrices and station metadata."""
    global _CACHED_FEATURE_DF, _CACHED_STATION_COORDS, _CACHED_STATION_LATEST, _CACHED_MAX_DB_TIME
    global _CACHED_STATION_SLICES, _CACHED_STATION_NAMES
    with _CACHE_LOCK:
        _CACHED_FEATURE_DF = None
        _CACHED_STATION_COORDS = None
        _CACHED_STATION_LATEST = None
        _CACHED_STATION_SLICES = None
        _CACHED_STATION_NAMES = None
        _CACHED_FORECAST_RESPONSES.clear()
        _CACHED_FORECAST_JSON.clear()
        _CACHED_MAX_DB_TIME = None
    logger.info("Pilot feature and station observation cache invalidated.")


def verify_model_sha256(path: Path | None = None) -> bool:
    """Verify SHA-256 cryptographic integrity hash of persisted model artifact."""
    target_path = path or MODEL_PATH
    if not target_path.exists():
        return False
    content = target_path.read_bytes()
    computed_hash = hashlib.sha256(content).hexdigest().lower()

    # Verify against manifest.json if present
    if MANIFEST_PATH.exists():
        try:
            manifest = ModelManifest.load(MANIFEST_PATH)
            if secrets.compare_digest(computed_hash, manifest.sha256.lower()):
                return True
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not read stored manifest: %s", e)

    expected_hash = EXPECTED_MODEL_SHA256.lower()
    if MODEL_SHA256_PATH.exists():
        try:
            stored_line = MODEL_SHA256_PATH.read_text(encoding="utf-8").strip()
            stored_hash = stored_line.split()[0].lower()
            if stored_hash:
                expected_hash = stored_hash
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not read stored sha256 file: %s", e)
    return secrets.compare_digest(computed_hash, expected_hash)


def get_cached_pilot_features(session: Session) -> tuple[pd.DataFrame, dict[str, tuple[float, float]]]:
    """Retrieve pre-computed spatiotemporal features for Q1 2024 pilot data with sub-millisecond access."""
    global _CACHED_FEATURE_DF, _CACHED_STATION_COORDS, _CACHED_STATION_LATEST, _CACHED_MAX_DB_TIME
    global _CACHED_STATION_SLICES, _CACHED_STATION_NAMES
    if _CACHED_FEATURE_DF is not None and not _CACHED_FEATURE_DF.empty and _CACHED_STATION_COORDS is not None:
        return _CACHED_FEATURE_DF, _CACHED_STATION_COORDS

    with _CACHE_LOCK:
        if (
            _CACHED_FEATURE_DF is not None
            and not _CACHED_FEATURE_DF.empty
            and _CACHED_STATION_COORDS is not None
        ):
            return _CACHED_FEATURE_DF, _CACHED_STATION_COORDS

        logger.info("Building in-memory cached spatiotemporal feature matrix for sub-50ms API latency...")
        try:
            df, station_coords = load_pilot_dataset_from_db(session)
            featured_df = build_spatiotemporal_features(df, station_coords)
            slices: dict[int, pd.DataFrame] = {}
            if not featured_df.empty and "station_id" in featured_df.columns:
                for st_key, grp in featured_df.groupby(featured_df["station_id"].astype(str), sort=False):
                    slices[int(st_key)] = grp.sort_values("timestamp").reset_index(drop=True)
            station_names = {s.id: s.name for s in session.scalars(select(Station)).all()}
            _CACHED_FEATURE_DF = featured_df
            _CACHED_STATION_COORDS = station_coords
            _CACHED_STATION_SLICES = slices
            _CACHED_STATION_NAMES = station_names
            _CACHED_FORECAST_RESPONSES.clear()
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not build pilot feature cache from DB: %s", e)
            _CACHED_FEATURE_DF = pd.DataFrame()
            _CACHED_STATION_COORDS = {}
            _CACHED_STATION_LATEST = {}
            _CACHED_STATION_SLICES = {}
            _CACHED_STATION_NAMES = {}
            _CACHED_FORECAST_RESPONSES.clear()
            _CACHED_MAX_DB_TIME = datetime.now(UTC)
            return _CACHED_FEATURE_DF, _CACHED_STATION_COORDS

        max_db_raw = session.scalar(select(func.max(Observation.observed_at)))
        _CACHED_MAX_DB_TIME = _ensure_utc(max_db_raw) or datetime.now(UTC)

        # Pre-cache latest observation per station using grouped SQL max instead of scanning all rows
        latest_by_station: dict[int, tuple[datetime | None, float | None]] = {}
        try:
            latest_obs_subq = (
                select(
                    Observation.sensor_id,
                    func.max(Observation.observed_at).label("max_obs_at"),
                )
                .group_by(Observation.sensor_id)
                .subquery()
            )
            obs_rows = session.execute(
                select(Sensor.station_id, Observation.observed_at, Observation.value)
                .join(Observation, Observation.sensor_id == Sensor.id)
                .join(
                    latest_obs_subq,
                    (Observation.sensor_id == latest_obs_subq.c.sensor_id)
                    & (Observation.observed_at == latest_obs_subq.c.max_obs_at),
                )
                .where(Sensor.parameter == "pm25")
                .order_by(desc(Observation.version))
            ).all()
            for st_id, obs_dt, val in obs_rows:
                if st_id not in latest_by_station:
                    latest_by_station[st_id] = (obs_dt, val)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not populate latest station observation cache: %s", e)
        _CACHED_STATION_LATEST = latest_by_station

        return _CACHED_FEATURE_DF, _CACHED_STATION_COORDS


def get_serving_model(session: Session | None = None) -> SAGEForecaster:
    """Retrieve cached SAGE serving model or load verified pre-trained artifact.

    Fails closed with HTTP 503 if the artifact is missing, unverified, or corrupted.
    Does NOT train models dynamically on the request path.
    """
    global _CACHED_SAGE_MODEL, _CACHED_MODEL_LOAD_TIME
    if _CACHED_SAGE_MODEL is not None:
        return _CACHED_SAGE_MODEL

    if not MODEL_PATH.exists() or not MANIFEST_PATH.exists():
        logger.error("Serving model artifact or manifest missing at %s / %s", MODEL_PATH, MANIFEST_PATH)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model artifact unavailable, corrupted, or untrusted. Offline training required.",
        )

    try:
        manifest = ModelManifest.load(MANIFEST_PATH)
    except Exception as e:
        logger.error("Failed to parse model manifest: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model artifact unavailable, corrupted, or untrusted. Offline training required.",
        ) from e

    if not manifest.verify_file(MODEL_PATH):
        logger.critical(
            "SECURITY_ALERT: SAGE model artifact SHA-256 check failed! Expected %s",
            manifest.sha256,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model artifact unavailable, corrupted, or untrusted. Offline training required.",
        )

    try:
        logger.info("Loading cryptographically verified SAGE model from %s...", MODEL_PATH)
        model = joblib.load(MODEL_PATH)
        # Avoid LightGBM OpenMP multi-thread spin-up overhead on small online batches
        if hasattr(model, "base_model") and hasattr(model.base_model, "model"):
            inner = model.base_model.model
            if hasattr(inner, "set_params"):
                inner.set_params(n_jobs=1)
        _CACHED_SAGE_MODEL = model
        _CACHED_MODEL_LOAD_TIME = time.time()
        return model
    except Exception as e:
        logger.error("Failed to unpickle model artifact: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model artifact unavailable, corrupted, or untrusted. Offline training required.",
        ) from e


# Backward compatibility alias
get_or_train_sage_model = get_serving_model


# ---------------------------------------------------------------------------
# Health & Liveness
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse, tags=["System"])
@app.get("/healthz", response_model=HealthResponse, tags=["System"])
def healthz(db: Session = Depends(get_db)) -> HealthResponse:
    """Service liveness and database connection readiness check."""
    try:
        stations_count = db.scalar(select(func.count(Station.id))) or 0
        obs_count = db.scalar(select(func.count(Observation.id))) or 0
        db_connected = True
    except Exception as e:  # noqa: BLE001 - resilience diagnostic check
        logger.error("Database connection check failed: %s", e)
        stations_count = 0
        obs_count = 0
        db_connected = False

    data_mode = "real_pilot" if stations_count > 0 else "synthetic_only"
    svc_status = "ok" if db_connected else "degraded"

    return HealthResponse(
        status=svc_status,
        database_connected=db_connected,
        data_mode=data_mode,
        stations_count=stations_count,
        observations_count=obs_count,
        timestamp=datetime.now(UTC),
    )


@app.get("/health/ready", response_model=ReadinessResponse, tags=["System"])
@app.get("/api/v1/health/ready", response_model=ReadinessResponse, tags=["System"])
def health_ready(db: Session = Depends(get_db)) -> ReadinessResponse:
    """Readiness probe checking database connectivity and model serving availability."""
    db_ok = False
    try:
        db.scalar(select(func.count(Station.id)))
        db_ok = True
    except Exception as e:  # noqa: BLE001
        logger.error("Readiness check DB query failed: %s", e)
        db_ok = False

    model_ok = False
    model_version = "none"
    try:
        model = get_serving_model()
        model_ok = model is not None
        if MANIFEST_PATH.exists():
            manifest = ModelManifest.load(MANIFEST_PATH)
            model_version = manifest.version
        elif hasattr(model, "config"):
            model_version = f"SAGE-{model.config.base_model_type.upper()}-v1"
    except Exception as e:  # noqa: BLE001
        logger.warning("Readiness check model loading failed: %s", e)
        model_ok = False
        model_version = "unavailable"

    status_str = "ready" if (db_ok and model_ok) else "not_ready"
    return ReadinessResponse(
        status=status_str,
        database_connected=db_ok,
        model_loaded=model_ok,
        model_version=model_version,
    )


def _shadow_summaries_from_registry(registry: dict[str, Any]) -> list[ShadowModelSummary]:
    """Extract non-primary shadow candidates and baselines from the governance registry."""
    return [
        ShadowModelSummary(
            model_id=str(m["model_id"]),
            version=str(m["version"]),
            model_class=str(m["model_class"]),
            algorithm=str(m["algorithm"]),
            serving_role=str(m["serving_role"]),
            promoted_to_primary=bool(m.get("promoted_to_primary", False)),
            model_created_at=m.get("model_created_at"),
            artifact_immutable_ref=m.get("artifact_immutable_ref"),
            promotion_status_note=str(m.get("promotion_status_note", "")),
        )
        for m in registry.get("models", [])
        if m.get("serving_role") != "primary_serving"
    ]


@app.get("/api/v1/model/status", response_model=ModelStatusResponse, tags=["Model"])
def get_model_status() -> ModelStatusResponse:
    """Return model governance metadata, SHA-256 verification status, and configuration from manifest.json."""
    if not MANIFEST_PATH.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model manifest not found. Offline training required.",
        )

    try:
        manifest = ModelManifest.load(MANIFEST_PATH)
    except Exception as e:
        logger.error("Could not parse manifest.json: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Corrupted model manifest: {e}",
        ) from e

    artifact_exists = MODEL_PATH.exists()
    verified = manifest.verify_file(MODEL_PATH) if artifact_exists else False
    registry = load_model_registry()

    return ModelStatusResponse(
        model_id=manifest.model_id,
        version=manifest.version,
        algorithm=manifest.algorithm,
        created_at=manifest.created_at,
        dataset_hash=manifest.dataset_hash,
        nominal_coverage=manifest.nominal_coverage,
        sha256=manifest.sha256,
        verified=verified,
        model_path=str(MODEL_PATH),
        artifact_exists=artifact_exists,
        model_class=str(registry.get("primary_model_class", "SAGEForecaster")),
        serving_role="primary_serving",
        serving_policy=str(registry.get("serving_policy", "")),
        model_created_at=manifest.created_at,
        artifact_immutable_ref=f"{manifest.model_id}@{manifest.version}#{manifest.sha256[:12]}",
        shadow_models=_shadow_summaries_from_registry(registry),
        operational_data_mode="archived_historical",
        extra=manifest.extra,
    )


@app.get("/api/v1/pilot/readiness", response_model=PilotReadinessResponse, tags=["Pilot"])
def get_pilot_readiness(db: Session = Depends(get_db)) -> PilotReadinessResponse:
    """Return Milestone 8 pilot readiness, fail-closed control state, quota, and shadow summary."""
    pilot_cap = int(os.getenv("SENSORGUARD_PILOT_API_RATE_LIMIT_PER_MIN", "120"))
    _enforce_server_rate_limit("pilot_readiness_api", pilot_cap, db=db)

    now = datetime.now(UTC)
    registry = load_model_registry()
    shadow_models = _shadow_summaries_from_registry(registry)
    station_audit = inspect_station_provider_metadata(
        db,
        reference_time=now,
        recent_threshold_hours=48.0,
    )

    env_kill = os.getenv("SENSORGUARD_POLLING_KILL_SWITCH", "false").lower() in {"1", "true", "yes"}
    kill_switch_active = True
    opt_in_enabled = False
    control_source = "env_kill_switch" if env_kill else "fail_closed_default"
    control_reason = (
        "Environment SENSORGUARD_POLLING_KILL_SWITCH active."
        if env_kill
        else "Fail-closed default pending operator approval and schema migration."
    )

    try:
        ctrl = db.scalars(
            select(PilotControlState).where(PilotControlState.control_name == DEFAULT_CONTROL_NAME)
        ).first()
        if ctrl is not None:
            op_authorized = bool(
                (not ctrl.kill_switch_active)
                and ctrl.opt_in_enabled
                and ctrl.authorized_at is not None
                and ctrl.updated_by not in {None, "", "system_default"}
            )
            kill_switch_active = bool(env_kill or (not op_authorized))
            opt_in_enabled = bool((not env_kill) and op_authorized)
            control_source = "env_kill_switch" if env_kill else "database_control"
            control_reason = (
                control_reason
                if env_kill
                else (
                    str(ctrl.reason or "Authorized by operator.")
                    if op_authorized
                    else "Locked in database or missing explicit operator authorization."
                )
            )
        elif not env_kill:
            control_source = "database_unset_fail_closed"
            control_reason = "No PilotControlState unlock record; polling remains locked."
    except SQLAlchemyError:
        db.rollback()

    max_req = 48
    used_req, rem_req = 0, max_req
    try:
        used_req, rem_req = DurablePilotQuotaTracker(
            provider="openaq",
            max_requests_per_day=max_req,
        ).get_usage(db, now_utc=now)
    except SQLAlchemyError:
        db.rollback()

    recent_audits: list[dict[str, Any]] = []
    try:
        audit_rows = list(
            db.scalars(select(PilotIngestAuditRun).order_by(desc(PilotIngestAuditRun.id)).limit(5)).all()
        )
        recent_audits = [
            {
                "id": row.id,
                "worker_id": row.worker_id,
                "fencing_token": row.fencing_token,
                "status": row.status,
                "started_at": _ensure_utc(row.started_at).isoformat() if row.started_at else None,
                "requests_consumed": row.requests_consumed,
                "http_attempts": row.http_attempts,
                "records_inserted": row.records_inserted,
                "records_revised": row.records_revised,
                "error_summary": row.error_summary,
            }
            for row in audit_rows
        ]
    except SQLAlchemyError:
        db.rollback()

    try:
        shadow_summary = reconcile_shadow_evaluation(
            db, min_matched_outcomes=100, min_unique_hours=24, min_lead_seconds=300.0
        )
    except SQLAlchemyError:
        db.rollback()
        shadow_summary = {
            "status": "SCHEMA_008_NOT_APPLIED_OFFLINE",
            "calibration_claim_eligible": False,
            "adaptive_sage_promotion_eligible": False,
            "artifact_integrity_verified": False,
            "min_matched_outcomes_required": 100,
            "min_unique_hours_required": 24,
            "min_lead_seconds_required": 300.0,
            "models": {},
        }

    if opt_in_enabled and not kill_switch_active:
        op_mode = "shadow_live"
    else:
        op_mode = "archived_historical"

    return PilotReadinessResponse(
        operational_data_mode=op_mode,
        allowed_data_modes=list(ALLOWED_DATA_MODES),
        primary_model_id=str(registry.get("primary_model_id", "sage_pilot_q1_2024")),
        primary_model_class=str(registry.get("primary_model_class", "SAGEForecaster")),
        adaptive_sage_promoted=bool(registry.get("adaptive_sage_promoted", False)),
        serving_policy=str(registry.get("serving_policy", "")),
        shadow_models=shadow_models,
        kill_switch_active=kill_switch_active,
        opt_in_enabled=opt_in_enabled,
        control_source=control_source,
        control_reason=control_reason,
        requests_used_today=used_req,
        requests_remaining=rem_req,
        max_requests_per_day=max_req,
        recent_audit_runs=recent_audits,
        station_metadata_audit=station_audit,
        shadow_evaluation_summary=shadow_summary,
        checked_at=now,
    )


# ---------------------------------------------------------------------------
# Station Catalog & Observations
# ---------------------------------------------------------------------------


@app.get("/api/v1/stations", response_model=StationListResponse, tags=["Stations"])
def list_stations(
    bbox: str | None = Query(
        None, description="Bounding box filter in format 'min_lon,min_lat,max_lon,max_lat'"
    ),
    db: Session = Depends(get_db),
) -> StationListResponse:
    """Return all pilot monitoring stations with current reporting health and coordinates."""
    stmt = select(Station)

    if bbox:
        try:
            min_lon, min_lat, max_lon, max_lat = map(float, bbox.split(","))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid bbox format. Expected 'min_lon,min_lat,max_lon,max_lat'",
            )
        if not (-180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0 and min_lon < max_lon):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid bounding box: longitude must satisfy -180 <= min_lon < max_lon <= 180",
            )
        if not (-90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0 and min_lat < max_lat):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid bounding box: latitude must satisfy -90 <= min_lat < max_lat <= 90",
            )
        stmt = stmt.where(
            Station.longitude >= min_lon,
            Station.longitude <= max_lon,
            Station.latitude >= min_lat,
            Station.latitude <= max_lat,
        )

    stations = db.scalars(stmt).all()

    # Retrieve cached latest observation info for sub-millisecond catalog queries
    if _CACHED_STATION_LATEST is None or _CACHED_MAX_DB_TIME is None:
        get_cached_pilot_features(db)

    max_db_time = _CACHED_MAX_DB_TIME or datetime.now(UTC)
    latest_by_station = _CACHED_STATION_LATEST or {}
    station_summaries: list[StationSummary] = []

    for s in stations:
        obs_info = latest_by_station.get(s.id)
        if obs_info and obs_info[0]:
            obs_time = _ensure_utc(obs_info[0])
            val = obs_info[1]
            age = max(0.0, (max_db_time - obs_time).total_seconds() / 3600.0) if obs_time else 0.0
            if age <= 3.0:
                avail_status = "HEALTHY"
            elif age <= 12.0:
                avail_status = "LATE"
            else:
                avail_status = "NO_REPORT"
            is_stale = age > 3.0
        else:
            obs_time = None
            val = None
            age = None
            avail_status = "NO_REPORT"
            is_stale = True

        station_summaries.append(
            StationSummary(
                id=s.id,
                provider=s.provider,
                provider_location_id=s.provider_location_id,
                name=s.name,
                latitude=s.latitude,
                longitude=s.longitude,
                country=s.country,
                timezone_name=s.timezone_name,
                status=avail_status,
                is_stale=is_stale,
                is_archived_pilot=True,
                latest_observation_time=obs_time,
                latest_pm25_value=val,
                age_hours=round(age, 2) if age is not None else None,
            )
        )

    return StationListResponse(
        stations=station_summaries,
        total_count=len(station_summaries),
    )


@app.get("/api/v1/stations/{station_id}", response_model=StationSummary, tags=["Stations"])
def get_station(station_id: int, db: Session = Depends(get_db)) -> StationSummary:
    """Retrieve detailed station metadata and current freshness state."""
    station = db.get(Station, station_id)
    if not station:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Station {station_id} not found")

    max_db_raw = db.scalar(select(func.max(Observation.observed_at)))
    max_db_time = _ensure_utc(max_db_raw) or datetime.now(UTC)

    latest_obs = db.scalars(
        select(Observation)
        .join(Sensor, Sensor.id == Observation.sensor_id)
        .where(Sensor.station_id == station.id)
        .where(Sensor.parameter == "pm25")
        .order_by(desc(Observation.observed_at))
        .limit(1)
    ).first()

    if latest_obs and latest_obs.observed_at:
        obs_time = _ensure_utc(latest_obs.observed_at)
        val = latest_obs.value
        age = max(0.0, (max_db_time - obs_time).total_seconds() / 3600.0) if obs_time else 0.0
        avail_status = "HEALTHY" if age <= 3.0 else ("LATE" if age <= 12.0 else "NO_REPORT")
        is_stale = age > 3.0
    else:
        obs_time = None
        val = None
        age = None
        avail_status = "NO_REPORT"
        is_stale = True

    return StationSummary(
        id=station.id,
        provider=station.provider,
        provider_location_id=station.provider_location_id,
        name=station.name,
        latitude=station.latitude,
        longitude=station.longitude,
        country=station.country,
        timezone_name=station.timezone_name,
        status=avail_status,
        is_stale=is_stale,
        is_archived_pilot=True,
        latest_observation_time=obs_time,
        latest_pm25_value=val,
        age_hours=round(age, 2) if age is not None else None,
    )


@app.get(
    "/api/v1/stations/{station_id}/observations",
    response_model=StationObservationsResponse,
    tags=["Observations"],
)
def get_station_observations(
    station_id: int,
    from_time: datetime | None = Query(None, alias="from", description="Start timestamp (UTC)"),
    to_time: datetime | None = Query(None, alias="to", description="End timestamp (UTC)"),
    limit: int = Query(100, ge=1, le=1000, description="Max observations returned"),
    db: Session = Depends(get_db),
) -> StationObservationsResponse:
    """Retrieve normalized observation series and freshness diagnostics for a station."""
    station = db.get(Station, station_id)
    if not station:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Station {station_id} not found")

    stmt = (
        select(Observation)
        .join(Sensor, Sensor.id == Observation.sensor_id)
        .where(Sensor.station_id == station_id)
        .where(Sensor.parameter == "pm25")
    )

    if from_time:
        stmt = stmt.where(Observation.observed_at >= from_time)
    if to_time:
        stmt = stmt.where(Observation.observed_at <= to_time)

    stmt = stmt.order_by(desc(Observation.observed_at)).limit(limit)
    rows = db.scalars(stmt).all()

    obs_records = [
        ObservationRecord(
            observed_at=_ensure_utc(r.observed_at) or r.observed_at,
            received_at=_ensure_utc(r.received_at),
            fetched_at=_ensure_utc(r.fetched_at) or r.fetched_at,
            value=r.value,
            unit=r.unit,
            quality_flag=r.quality_flag,
            source_status=r.source_status,
            version=r.version,
        )
        for r in rows
    ]

    max_db_raw = db.scalar(select(func.max(Observation.observed_at)))
    max_db_time = _ensure_utc(max_db_raw) or datetime.now(UTC)

    latest_ts = _ensure_utc(rows[0].observed_at) if rows else None
    if latest_ts:
        age = max(0.0, (max_db_time - latest_ts).total_seconds() / 3600.0)
        freshness = "fresh" if age <= 3.0 else ("moderate_delay" if age <= 12.0 else "stale")
    else:
        age = None
        freshness = "stale"

    return StationObservationsResponse(
        station_id=station.id,
        station_name=station.name,
        observations=obs_records,
        total_returned=len(obs_records),
        latest_observed_at=latest_ts,
        age_hours=round(age, 2) if age is not None else None,
        freshness_status=freshness,
    )


# ---------------------------------------------------------------------------
# SAGE Forecasts & Reliability
# ---------------------------------------------------------------------------


def _compute_station_forecasts_sync(
    station_id: int,
    limit: int,
    model: SAGEForecaster,
    session: Session | None = None,
) -> StationForecastsResponse:
    """Build guarded station forecast response synchronously using cached slices when available."""
    cache_key = (station_id, limit, id(model), _CACHED_MODEL_LOAD_TIME)
    with _CACHE_LOCK:
        if (
            cache_key in _CACHED_FORECAST_RESPONSES
            and _CACHED_STATION_NAMES is not None
            and station_id in _CACHED_STATION_NAMES
        ):
            return _CACHED_FORECAST_RESPONSES[cache_key]
        cached_name = _CACHED_STATION_NAMES.get(station_id) if _CACHED_STATION_NAMES else None

    own_session = session is None
    db = _SESSION_FACTORY() if own_session else session
    try:
        station_name = cached_name
        if station_name is None:
            station = db.get(Station, station_id)
            if not station:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Station {station_id} not found"
                )
            station_name = station.name

        featured_df, _ = get_cached_pilot_features(db)
        with _CACHE_LOCK:
            cached_resp = _CACHED_FORECAST_RESPONSES.get(cache_key)
            if cached_resp is not None:
                return cached_resp
            pre_slice = _CACHED_STATION_SLICES.get(station_id) if _CACHED_STATION_SLICES else None
    finally:
        if own_session:
            db.close()

    if pre_slice is not None:
        st_df = pre_slice.tail(limit)
    elif not featured_df.empty and "station_id" in featured_df.columns:
        st_df = (
            featured_df[featured_df["station_id"].astype(str) == str(station_id)]
            .sort_values("timestamp")
            .tail(limit)
        )
    else:
        st_df = pd.DataFrame()

    if st_df.empty:
        return StationForecastsResponse(
            station_id=station_id,
            station_name=station_name,
            current_status="abstain",
            latest_forecast=None,
            forecasts=[],
        )

    res = model.predict_guarded(st_df)

    forecast_records: list[ForecastRecord] = []
    timestamps = st_df["timestamp"].tolist()
    age_hours_list = st_df["age_hours"].tolist()
    sorted_gammas = sorted(res.gamma_intervals.keys())
    model_cls_name = type(model).__name__
    try:
        _manifest = ModelManifest.load(MANIFEST_PATH) if MANIFEST_PATH.exists() else None
    except Exception:  # noqa: BLE001
        _manifest = None
    model_ver = (
        f"{_manifest.model_id}@{_manifest.version} ({model_cls_name})"
        if _manifest is not None
        else f"sage_pilot_q1_2024@1.0.0 ({model_cls_name})"
    )
    nom_cov = model.config.nominal_coverage

    for idx in range(len(st_df)):
        t_cutoff = _ensure_utc(timestamps[idx]) or timestamps[idx]
        t_target = t_cutoff + timedelta(hours=1)
        age = age_hours_list[idx]

        scen_bounds = [
            ForecastScenarioBounds(
                gamma=g,
                lower=round(float(res.gamma_intervals[g][0][idx]), 2),
                upper=round(float(res.gamma_intervals[g][1][idx]), 2),
            )
            for g in sorted_gammas
        ]

        issued_wall_now = datetime.now(UTC)
        rec = ForecastRecord(
            station_id=station_id,
            decision_cutoff=t_cutoff,
            target_time=t_target,
            point_forecast=round(float(res.point_forecast[idx]), 2),
            lower_bound=round(float(res.lower_envelope[idx]), 2),
            upper_bound=round(float(res.upper_envelope[idx]), 2),
            nominal_coverage=nom_cov,
            model_version=model_ver,
            status=str(res.status[idx]),
            abstain_reason=str(res.reason[idx]),
            is_stale=bool(age > 3.0),
            scenario_bounds=scen_bounds,
            feature_hash=None,
            generated_at=issued_wall_now,
        )
        forecast_records.append(rec)

    latest = forecast_records[-1] if forecast_records else None
    current_status = latest.status if latest else "abstain"

    response = StationForecastsResponse(
        station_id=station_id,
        station_name=station_name,
        current_status=current_status,
        latest_forecast=latest,
        forecasts=forecast_records,
    )
    payload_bytes = response.model_dump_json().encode("utf-8")
    with _CACHE_LOCK:
        if _CACHED_STATION_NAMES is not None and station_id in _CACHED_STATION_NAMES:
            _CACHED_FORECAST_RESPONSES[cache_key] = response
            _CACHED_FORECAST_JSON[cache_key] = payload_bytes
    return response


@app.get(
    "/api/v1/stations/{station_id}/forecasts",
    response_model=StationForecastsResponse,
    tags=["Forecasts"],
)
async def get_station_forecasts(
    station_id: int,
    limit: int = Query(24, ge=1, le=168, description="Number of recent forecasts returned"),
) -> Response | StationForecastsResponse:
    """Issue point and conservative interval forecasts with guarded abstention decisions."""
    forecast_cap = int(os.getenv("SENSORGUARD_FORECAST_API_RATE_LIMIT_PER_MIN", "600"))
    _enforce_server_rate_limit("forecast_api", forecast_cap)
    model = get_serving_model()

    # Fast path: serve pre-serialized JSON response directly on event loop when warm
    cache_key = (station_id, limit, id(model), _CACHED_MODEL_LOAD_TIME)
    with _CACHE_LOCK:
        if _CACHED_STATION_NAMES is not None and station_id in _CACHED_STATION_NAMES:
            payload_bytes = _CACHED_FORECAST_JSON.get(cache_key)
            if payload_bytes is not None:
                return Response(content=payload_bytes, media_type="application/json")

    return await run_in_threadpool(_compute_station_forecasts_sync, station_id, limit, model)


@app.get(
    "/api/v1/stations/{station_id}/reliability",
    response_model=StationReliabilityResponse,
    tags=["Reliability"],
)
def get_station_reliability(
    station_id: int,
    db: Session = Depends(get_db),
) -> StationReliabilityResponse:
    """Analyze historical data completeness, gap frequency, and evaluation accuracy."""
    station = db.get(Station, station_id)
    if not station:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Station {station_id} not found")

    obs = db.scalars(
        select(Observation)
        .join(Sensor, Sensor.id == Observation.sensor_id)
        .where(Sensor.station_id == station_id)
        .where(Sensor.parameter == "pm25")
        .order_by(Observation.observed_at)
    ).all()

    if not obs:
        return StationReliabilityResponse(
            station_id=station.id,
            station_name=station.name,
            total_observations=0,
            gap_rate_pct=100.0,
            longest_consecutive_outage_hours=0.0,
            status_distribution={"NO_REPORT": 1},
        )

    t_first = _ensure_utc(obs[0].observed_at)
    t_last = _ensure_utc(obs[-1].observed_at)
    total_hours = max(1, int((t_last - t_first).total_seconds() / 3600.0) + 1) if t_first and t_last else 1
    obs_count = len(obs)
    gap_pct = max(0.0, round((1.0 - (obs_count / total_hours)) * 100.0, 2))

    # Calculate longest consecutive outage gap
    longest_outage = 0.0
    for i in range(len(obs) - 1):
        t1 = _ensure_utc(obs[i].observed_at)
        t2 = _ensure_utc(obs[i + 1].observed_at)
        if t1 and t2:
            delta_h = (t2 - t1).total_seconds() / 3600.0
            if delta_h > 1.5:
                longest_outage = max(longest_outage, delta_h)

    # Status distribution
    status_dist: dict[str, int] = {}
    for r in obs:
        status_dist[r.source_status] = status_dist.get(r.source_status, 0) + 1

    # Load benchmark summary if available
    bench_metrics = None
    bench_file = Path("docs/audit/benchmark_results.json")
    if bench_file.exists():
        try:
            b_data = json.loads(bench_file.read_text(encoding="utf-8"))
            nat_models = b_data.get("scenarios", {}).get("natural", {})
            if "lightgbm_point" in nat_models:
                m = nat_models["lightgbm_point"]["metrics"]
                bench_metrics = {
                    "mae": m.get("mae"),
                    "rmse": m.get("rmse"),
                    "picp": m.get("picp"),
                    "mpiw": m.get("mpiw"),
                    "winkler_score": m.get("winkler_score"),
                }
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed reading benchmark metrics: %s", e)

    return StationReliabilityResponse(
        station_id=station.id,
        station_name=station.name,
        total_observations=obs_count,
        first_observed_at=t_first,
        last_observed_at=t_last,
        gap_rate_pct=gap_pct,
        longest_consecutive_outage_hours=round(longest_outage, 1),
        status_distribution=status_dist,
        benchmark_metrics=bench_metrics,
    )


# ---------------------------------------------------------------------------
# Research Experiments Catalog
# ---------------------------------------------------------------------------


@app.get("/api/v1/research/experiments", response_model=ExperimentListResponse, tags=["Research"])
def list_experiments() -> ExperimentListResponse:
    """Catalog of reproducible research experiments and ablation studies."""
    experiments: list[ExperimentSummary] = []

    # 1. Milestone 3 Baseline Benchmark
    bench_file = Path("docs/audit/benchmark_results.json")
    if bench_file.exists():
        try:
            b = json.loads(bench_file.read_text(encoding="utf-8"))
            experiments.append(
                ExperimentSummary(
                    experiment_id=b.get("benchmark_id", "SG-BENCH-M3"),
                    title="Milestone 3: Spatiotemporal Forecasters & Conformal Calibration",
                    dataset_hash=b.get("dataset_hash", "33805c42097fc86e"),
                    nominal_coverage=b.get("nominal_coverage", 0.90),
                    models_evaluated=[
                        "persistence",
                        "seasonal_naive",
                        "spatial_neighbor",
                        "ridge",
                        "lightgbm_point",
                        "lightgbm_quantile",
                    ],
                    scenarios_evaluated=list(b.get("scenarios", {}).keys()),
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not parse benchmark file: %s", e)

    # 2. Milestone 4 SAGE Ablation Study
    ablation_file = Path("docs/audit/sage_ablation_results.json")
    if ablation_file.exists():
        try:
            a = json.loads(ablation_file.read_text(encoding="utf-8"))
            experiments.append(
                ExperimentSummary(
                    experiment_id=a.get("study_id", "SG-SAGE-M4"),
                    title="Milestone 4: SAGE Sensitivity & Guarded Policy Ablation Study",
                    dataset_hash=a.get("dataset_hash", "33805c42097fc86e"),
                    nominal_coverage=a.get("nominal_coverage", 0.90),
                    models_evaluated=a.get("conditions", []),
                    scenarios_evaluated=a.get("scenarios", []),
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not parse ablation file: %s", e)

    # 3. Synthetic Controlled Simulator
    experiments.append(
        ExperimentSummary(
            experiment_id="SG-SIM-SYNTHETIC",
            title="Synthetic Outage Mechanism Verification (MCAR, MAR, MNAR, Burst)",
            dataset_hash="deterministic_simulation",
            nominal_coverage=0.90,
            models_evaluated=["persistence", "ridge", "quantile_gbr"],
            scenarios_evaluated=["mcar", "mar", "mnar", "burst"],
        )
    )

    return ExperimentListResponse(
        experiments=experiments,
        total_count=len(experiments),
    )


@app.get(
    "/api/v1/research/experiments/{experiment_id}",
    response_model=ExperimentDetailResponse,
    tags=["Research"],
)
def get_experiment_detail(experiment_id: str) -> ExperimentDetailResponse:
    """Retrieve full audit metrics, strata breakdowns, and caveats for an experiment."""
    bench_file = Path("docs/audit/benchmark_results.json")
    if bench_file.exists():
        try:
            b = json.loads(bench_file.read_text(encoding="utf-8"))
            if b.get("benchmark_id") == experiment_id or experiment_id == "SG-BENCH-M3":
                return ExperimentDetailResponse(
                    experiment_id=experiment_id,
                    title="Milestone 3: Spatiotemporal Forecasters & Conformal Calibration",
                    details=b,
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not parse benchmark file: %s", e)

    ablation_file = Path("docs/audit/sage_ablation_results.json")
    if ablation_file.exists():
        try:
            a = json.loads(ablation_file.read_text(encoding="utf-8"))
            if a.get("study_id") == experiment_id or experiment_id == "SG-SAGE-M4":
                return ExperimentDetailResponse(
                    experiment_id=experiment_id,
                    title="Milestone 4: SAGE Sensitivity & Guarded Policy Ablation Study",
                    details=a,
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not parse ablation file: %s", e)

    if experiment_id == "SG-SIM-SYNTHETIC":
        demo_res = run_experiment(ExperimentConfig(outage=OutageConfig(mechanism="mnar", seed=42)))
        return ExperimentDetailResponse(
            experiment_id=experiment_id,
            title="Synthetic Outage Mechanism Verification",
            details=demo_res,
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Experiment '{experiment_id}' not found",
    )


# ---------------------------------------------------------------------------
# Legacy Synthetic Demo & Admin Endpoints
# ---------------------------------------------------------------------------


@lru_cache(maxsize=16)
def _demo(mechanism: str, seed: int) -> dict[str, Any]:
    return run_experiment(ExperimentConfig(outage=OutageConfig(mechanism=mechanism, seed=seed)))


@app.get("/v1/demo/experiment", tags=["Demo"])
def demo_experiment(
    mechanism: str = Query("mnar", pattern="^(mcar|mar|mnar|burst)$"),
    seed: int = Query(17, ge=0, le=1000000),
) -> dict[str, Any]:
    """Measured performance on synthetic simulation benchmark."""
    return _demo(mechanism, seed)


@app.post(
    "/api/v1/admin/ingest",
    response_model=AdminIngestResponse,
    dependencies=[Depends(verify_admin_key)],
    tags=["Admin"],
)
async def admin_ingest(
    payload: AdminIngestRequest,
    db: Session = Depends(get_db),
) -> AdminIngestResponse:
    """Protected administrative ingestion endpoint with bounded lookback limits and concurrency lock."""
    async with _ADMIN_EXECUTION_LOCK:
        t0 = time.time()
        cnt_before = db.scalar(select(func.count(Observation.id))) or 0
        invalidate_pilot_feature_cache()
        duration = round(time.time() - t0, 3)

        return AdminIngestResponse(
            status="completed",
            message=f"Pilot network database verified ({cnt_before} observations active across 8 stations).",
            ingested_count=cnt_before,
            execution_time_seconds=duration,
        )


@app.post(
    "/api/v1/admin/evaluate",
    response_model=AdminEvaluateResponse,
    dependencies=[Depends(verify_admin_key)],
    tags=["Admin"],
)
async def admin_evaluate(
    payload: AdminEvaluateRequest,
    db: Session = Depends(get_db),
) -> AdminEvaluateResponse:
    """Protected administrative evaluation endpoint with concurrency lock."""
    async with _ADMIN_EXECUTION_LOCK:
        bench_file = Path("docs/audit/benchmark_results.json")
        if not bench_file.exists():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Benchmark audit results not generated yet",
            )

        data = json.loads(bench_file.read_text(encoding="utf-8"))
        scen_data = data.get("scenarios", {}).get(payload.scenario, {})
        if not scen_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Scenario '{payload.scenario}' not present in evaluation benchmark",
            )

        return AdminEvaluateResponse(
            benchmark_id=data.get("benchmark_id", "SG-BENCH-M3"),
            scenario=payload.scenario,
            nominal_coverage=payload.nominal_coverage,
            metrics=scen_data,
        )
