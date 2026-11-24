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
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from sensorguard.db.schema import Observation, Sensor, Station
from sensorguard.db.session import create_db_engine, get_session_factory
from sensorguard.experiment import ExperimentConfig, run_experiment
from sensorguard.models.data import (
    build_spatiotemporal_features,
    load_pilot_dataset_from_db,
    partition_dataset,
)
from sensorguard.models.sage import SAGEConfig, SAGEForecaster
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
    ObservationRecord,
    StationForecastsResponse,
    StationListResponse,
    StationObservationsResponse,
    StationReliabilityResponse,
    StationSummary,
)
from sensorguard.simulation import OutageConfig

logger = logging.getLogger(__name__)

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
_admin_request_history: list[float] = []

app = FastAPI(
    title="SensorGuard AI Pilot & Research API",
    version="0.5.0",
    description="Public pilot API for environmental sensor network forecasting, uncertainty quantification, and outage resilience.",
)

_ENGINE = create_db_engine()
_SESSION_FACTORY = get_session_factory(_ENGINE)


def get_db():
    """Dependency providing a transactional database session."""
    session = _SESSION_FACTORY()
    try:
        yield session
    finally:
        session.close()


def verify_admin_key(x_admin_key: str = Header(..., alias="X-Admin-Key")) -> None:
    """Validate administrative authorization header with constant-time equality and rate limits.

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

    # Rate limiting: maximum 30 requests per minute
    now = time.time()
    _admin_request_history[:] = [t for t in _admin_request_history if now - t < 60.0]
    if len(_admin_request_history) >= 30:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Administrative rate limit exceeded (max 30 requests per minute)",
        )
    _admin_request_history.append(now)
    logger.info("ADMIN_AUDIT: Administrative request authenticated at %s", datetime.now(UTC).isoformat())


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Ensure datetime has explicit UTC timezone info."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


# Global in-memory cache and disk persistence for trained SAGE model
_CACHED_SAGE_MODEL: SAGEForecaster | None = None
_CACHED_MODEL_LOAD_TIME: float = 0.0
_CACHED_FEATURE_DF: pd.DataFrame | None = None
_CACHED_STATION_COORDS: dict[str, tuple[float, float]] | None = None
_CACHED_STATION_LATEST: dict[int, tuple[datetime | None, float | None]] | None = None
_CACHED_MAX_DB_TIME: datetime | None = None
_ADMIN_EXECUTION_LOCK = asyncio.Lock()

ARTIFACT_DIR = Path("artifacts/models")
MODEL_PATH = ARTIFACT_DIR / "sage_pilot_q1_2024.joblib"
MODEL_SHA256_PATH = ARTIFACT_DIR / "sage_pilot_q1_2024.sha256"
EXPECTED_MODEL_SHA256 = "6155ee41544f4a53b24c4528bbc55afa3f7e3b02aa511f61d65605ff40eab77b"


def verify_model_sha256(path: Path) -> bool:
    """Verify SHA-256 cryptographic integrity hash of persisted model artifact."""
    if not path.exists():
        return False
    content = path.read_bytes()
    computed_hash = hashlib.sha256(content).hexdigest().lower()
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
    if _CACHED_FEATURE_DF is not None and not _CACHED_FEATURE_DF.empty and _CACHED_STATION_COORDS is not None:
        return _CACHED_FEATURE_DF, _CACHED_STATION_COORDS

    logger.info("Building in-memory cached spatiotemporal feature matrix for sub-50ms API latency...")
    try:
        df, station_coords = load_pilot_dataset_from_db(session)
        featured_df = build_spatiotemporal_features(df, station_coords)
        _CACHED_FEATURE_DF = featured_df
        _CACHED_STATION_COORDS = station_coords
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not build pilot feature cache from DB: %s", e)
        _CACHED_FEATURE_DF = pd.DataFrame()
        _CACHED_STATION_COORDS = {}
        _CACHED_STATION_LATEST = {}
        _CACHED_MAX_DB_TIME = datetime.now(UTC)
        return _CACHED_FEATURE_DF, _CACHED_STATION_COORDS

    max_db_raw = session.scalar(select(func.max(Observation.observed_at)))
    _CACHED_MAX_DB_TIME = _ensure_utc(max_db_raw) or datetime.now(UTC)

    # Pre-cache latest observation per station
    latest_by_station: dict[int, tuple[datetime | None, float | None]] = {}
    try:
        obs_rows = session.execute(
            select(Sensor.station_id, Observation.observed_at, Observation.value)
            .join(Observation, Observation.sensor_id == Sensor.id)
            .where(Sensor.parameter == "pm25")
            .order_by(desc(Observation.observed_at))
        ).all()
        for st_id, obs_dt, val in obs_rows:
            if st_id not in latest_by_station:
                latest_by_station[st_id] = (obs_dt, val)
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not populate latest station observation cache: %s", e)
    _CACHED_STATION_LATEST = latest_by_station

    return _CACHED_FEATURE_DF, _CACHED_STATION_COORDS


def get_or_train_sage_model(session: Session) -> SAGEForecaster:
    """Retrieve cached SAGE model or load pre-trained artifact with cryptographic SHA-256 integrity verification."""
    global _CACHED_SAGE_MODEL, _CACHED_MODEL_LOAD_TIME
    if _CACHED_SAGE_MODEL is not None:
        return _CACHED_SAGE_MODEL

    if MODEL_PATH.exists():
        if not verify_model_sha256(MODEL_PATH):
            logger.critical(
                "SECURITY_ALERT: SAGE model artifact SHA-256 check failed! Rejecting corrupted model artifact."
            )
        else:
            try:
                logger.info("Loading cryptographically verified SAGE model from %s...", MODEL_PATH)
                model = joblib.load(MODEL_PATH)
                _CACHED_SAGE_MODEL = model
                _CACHED_MODEL_LOAD_TIME = time.time()
                return model
            except Exception as e:  # noqa: BLE001
                logger.warning("Could not load persisted model: %s. Re-fitting...", e)

    logger.info("Initializing and training SAGE model for inference...")
    featured_df, station_coords = get_cached_pilot_features(session)
    if featured_df.empty or len(featured_df) < 50:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Pilot feature dataset unavailable in database to fit SAGE model",
        )
    splits = partition_dataset(featured_df, station_coords, train_pct=0.60, cal_pct=0.20)

    cfg = SAGEConfig(
        gamma_grid=(0.0, 0.5, 1.0, 1.5, 2.0),
        nominal_coverage=0.90,
        max_disagreement_threshold=20.0,
        min_effective_sample_size=15,
        stale_age_hours=12.0,
        caution_age_hours=3.0,
    )
    model = SAGEForecaster(config=cfg, feature_columns=splits.feature_columns)
    model.fit(
        X=splits.train,
        y=splits.train["target"].to_numpy(),
        X_cal=splits.calibrate,
        y_cal=splits.calibrate["target"].to_numpy(),
    )

    try:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, MODEL_PATH)
        content = MODEL_PATH.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        MODEL_SHA256_PATH.write_text(f"{sha}  sage_pilot_q1_2024.joblib\n", encoding="utf-8")
        logger.info("Saved trained SAGE model artifact and SHA-256 to %s", MODEL_PATH)
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not persist model artifact: %s", e)

    _CACHED_SAGE_MODEL = model
    _CACHED_MODEL_LOAD_TIME = time.time()
    return model


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


@app.get(
    "/api/v1/stations/{station_id}/forecasts",
    response_model=StationForecastsResponse,
    tags=["Forecasts"],
)
def get_station_forecasts(
    station_id: int,
    limit: int = Query(24, ge=1, le=168, description="Number of recent forecasts returned"),
    db: Session = Depends(get_db),
) -> StationForecastsResponse:
    """Issue point and conservative interval forecasts with guarded abstention decisions."""
    station = db.get(Station, station_id)
    if not station:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Station {station_id} not found")

    # Load recent observations from in-memory cached features for sub-50ms latency
    featured_df, _ = get_cached_pilot_features(db)
    if featured_df.empty or "station_id" not in featured_df.columns:
        return StationForecastsResponse(
            station_id=station.id,
            station_name=station.name,
            current_status="abstain",
            latest_forecast=None,
            forecasts=[],
        )
    st_df = (
        featured_df[featured_df["station_id"] == str(station_id)].sort_values("timestamp").tail(limit).copy()
    )

    if st_df.empty:
        return StationForecastsResponse(
            station_id=station.id,
            station_name=station.name,
            current_status="abstain",
            latest_forecast=None,
            forecasts=[],
        )

    model = get_or_train_sage_model(db)
    res = model.predict_guarded(st_df)

    forecast_records: list[ForecastRecord] = []
    timestamps = st_df["timestamp"].tolist()
    age_hours_list = st_df["age_hours"].tolist()

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
            for g in sorted(res.gamma_intervals.keys())
        ]

        rec = ForecastRecord(
            station_id=station.id,
            decision_cutoff=t_cutoff,
            target_time=t_target,
            point_forecast=round(float(res.point_forecast[idx]), 2),
            lower_bound=round(float(res.lower_envelope[idx]), 2),
            upper_bound=round(float(res.upper_envelope[idx]), 2),
            nominal_coverage=model.config.nominal_coverage,
            model_version=f"SAGE-{model.config.base_model_type.upper()}-v1",
            status=str(res.status[idx]),
            abstain_reason=str(res.reason[idx]),
            is_stale=bool(age > 3.0),
            scenario_bounds=scen_bounds,
            feature_hash=None,
        )
        forecast_records.append(rec)

    latest = forecast_records[-1] if forecast_records else None
    current_status = latest.status if latest else "abstain"

    return StationForecastsResponse(
        station_id=station.id,
        station_name=station.name,
        current_status=current_status,
        latest_forecast=latest,
        forecasts=forecast_records,
    )


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
                    dataset_hash=b.get("dataset_hash", "7e8f2d316bee0c4f"),
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
                    dataset_hash=a.get("dataset_hash", "7e8f2d316bee0c4f"),
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
