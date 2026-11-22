"""Typed Pydantic 2 schemas for SensorGuard AI public and research APIs.

All contracts enforce explicit timestamp semantics, unit validations,
honest missingness statuses, and distribution-free uncertainty intervals.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness, database readiness, and active data mode diagnostics."""

    status: str = Field(..., description="Service status ('ok' or 'degraded')")
    database_connected: bool = Field(..., description="True if database connection check succeeded")
    data_mode: str = Field(..., description="Active dataset mode ('real_pilot' or 'synthetic_only')")
    stations_count: int = Field(..., description="Number of monitored stations in database")
    observations_count: int = Field(..., description="Total observation count across all sensors")
    timestamp: datetime = Field(..., description="UTC server timestamp")


class StationSummary(BaseModel):
    """Metadata, geographic coordinates, and reporting health for a monitoring station."""

    id: int
    provider: str
    provider_location_id: int
    name: str
    latitude: float
    longitude: float
    country: str | None = None
    timezone_name: str | None = None
    status: str = Field(..., description="Availability status: HEALTHY, LATE, NO_REPORT, OFFLINE_CONFIRMED")
    is_stale: bool = Field(..., description="True if latest reading exceeds freshness threshold (3h)")
    latest_observation_time: datetime | None = None
    latest_pm25_value: float | None = None
    age_hours: float | None = None


class StationListResponse(BaseModel):
    """Collection of monitoring stations with count."""

    stations: list[StationSummary]
    total_count: int
    provider_citation: str = Field(default="OpenAQ v3 / UK AURN Network (Crown Copyright / OGL)")


class ObservationRecord(BaseModel):
    """Normalized point-in-time sensor observation."""

    observed_at: datetime = Field(..., description="Physical observation period end (UTC)")
    received_at: datetime | None = Field(default=None, description="Upstream provider receipt time (UTC)")
    fetched_at: datetime = Field(..., description="Local system ingestion timestamp (UTC)")
    value: float | None = Field(default=None, description="Measured PM2.5 concentration")
    unit: str = Field(default="µg/m³", description="Normalized measurement unit")
    quality_flag: str = Field(default="VALID", description="Data quality classification")
    source_status: str = Field(default="HEALTHY", description="Upstream source diagnostic state")
    version: int = Field(default=1, description="Observation revision version")


class StationObservationsResponse(BaseModel):
    """Historical observation time series and data freshness diagnostics for a station."""

    station_id: int
    station_name: str
    observations: list[ObservationRecord]
    total_returned: int
    latest_observed_at: datetime | None = None
    age_hours: float | None = None
    freshness_status: str = Field(..., description="'fresh', 'moderate_delay', or 'stale'")
    provider_citation: str = Field(default="OpenAQ v3 / UK AURN Network (Crown Copyright / OGL)")


class ForecastScenarioBounds(BaseModel):
    """Prediction interval bounds under a specific hypothesized MNAR gamma scenario."""

    gamma: float
    lower: float
    upper: float


class ForecastRecord(BaseModel):
    """Issued point and interval forecast with safety decisions and provenance."""

    station_id: int
    decision_cutoff: datetime = Field(..., description="Latest observation timestamp visible at forecast")
    target_time: datetime = Field(..., description="Forecast target timestamp (+1 hour ahead)")
    point_forecast: float = Field(..., description="Expected PM2.5 in µg/m³")
    lower_bound: float = Field(..., description="Conservative lower prediction interval bound (µg/m³)")
    upper_bound: float = Field(..., description="Conservative upper prediction interval bound (µg/m³)")
    nominal_coverage: float = Field(default=0.90, description="Target nominal coverage level (e.g. 90%)")
    model_version: str = Field(..., description="Model architecture and version")
    status: str = Field(..., description="Guarded safety status: 'publish', 'caution', 'abstain'")
    abstain_reason: str = Field(..., description="Explicit reason for status decision")
    is_stale: bool = Field(..., description="True if forecast is based on stale sensor history")
    scenario_bounds: list[ForecastScenarioBounds] = Field(default_factory=list)
    feature_hash: str | None = None


class StationForecastsResponse(BaseModel):
    """Recent issued forecasts and current prediction status for a station."""

    station_id: int
    station_name: str
    current_status: str = Field(..., description="Current status of the station forecast")
    latest_forecast: ForecastRecord | None = None
    forecasts: list[ForecastRecord]
    provider_citation: str = Field(default="OpenAQ v3 / UK AURN Network")


class StationReliabilityResponse(BaseModel):
    """Historical data completeness, outage duration, and evaluation reliability."""

    station_id: int
    station_name: str
    total_observations: int
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    gap_rate_pct: float
    longest_consecutive_outage_hours: float
    status_distribution: dict[str, int]
    benchmark_metrics: dict[str, Any] | None = None


class ExperimentSummary(BaseModel):
    """High-level summary of a logged benchmark or ablation experiment."""

    experiment_id: str
    title: str
    dataset_hash: str
    nominal_coverage: float
    models_evaluated: list[str]
    scenarios_evaluated: list[str]
    timestamp: str | None = None


class ExperimentListResponse(BaseModel):
    """Catalog of reproducible research benchmarks."""

    experiments: list[ExperimentSummary]
    total_count: int


class ExperimentDetailResponse(BaseModel):
    """Detailed benchmark or ablation study results and research caveats."""

    experiment_id: str
    title: str
    details: dict[str, Any]
    research_caveats: list[str] = Field(
        default_factory=lambda: [
            "Exchangeability cannot be unconditionally guaranteed under nonrandom missingness (MNAR).",
            "Observed natural gaps reflect upstream selection bias; ground-truth evaluation requires controlled masking.",
            "Never equate upstream provider communication delays with confirmed physical sensor failure.",
        ]
    )


class AdminIngestRequest(BaseModel):
    """Parameters for protected administrative ingestion."""

    location_ids: list[int] | None = Field(default=None, description="Optional subset of location IDs")
    days: int = Field(default=7, ge=1, le=90, description="Historical lookback window in days (max 90)")


class AdminIngestResponse(BaseModel):
    """Result of administrative ingestion."""

    status: str
    message: str
    ingested_count: int
    execution_time_seconds: float


class AdminEvaluateRequest(BaseModel):
    """Parameters for protected administrative evaluation."""

    scenario: str = Field(default="natural", description="Evaluation regime ('natural', 'mcar_20', 'mnar_gamma_1.5', 'burst_24h')")
    nominal_coverage: float = Field(default=0.90, ge=0.50, le=0.99)


class AdminEvaluateResponse(BaseModel):
    """Result of administrative benchmark evaluation."""

    benchmark_id: str
    scenario: str
    nominal_coverage: float
    metrics: dict[str, Any]
