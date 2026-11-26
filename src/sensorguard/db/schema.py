"""SQLAlchemy 2.0 ORM models for SensorGuard data lineage and storage.

All models are designed for full compatibility with both SQLite (local development
and offline testing) and PostgreSQL (production MLOps deployment).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all SensorGuard ORM models."""


class Station(Base):
    """Monitored atmospheric sensing location.

    Represents a physical ground-truth monitoring facility or deployment site.
    """

    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="openaq")
    provider_location_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    timezone_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    sensors: Mapped[list[Sensor]] = relationship(
        "Sensor", back_populates="station", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("provider", "provider_location_id", name="uq_station_provider_location"),
    )


class Sensor(Base):
    """Specific measurement instrument attached to a station (e.g., PM2.5 optical counter)."""

    __tablename__ = "sensors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_sensor_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    parameter: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    canonical_unit: Mapped[str] = mapped_column(String(32), nullable=False, default="µg/m³")
    averaging_period_hours: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    station: Mapped[Station] = relationship("Station", back_populates="sensors")
    observations: Mapped[list[Observation]] = relationship(
        "Observation", back_populates="sensor", cascade="all, delete-orphan"
    )


class RawIngestBatch(Base):
    """Immutable audit snapshot of a raw upstream provider HTTP payload.

    Provides end-to-end cryptographic lineage back to provider responses.
    """

    __tablename__ = "raw_ingest_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="openaq")
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    raw_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    observations: Mapped[list[Observation]] = relationship("Observation", back_populates="raw_batch")


class Observation(Base):
    """Normalized, point-in-time observation with explicit timestamp semantics.

    Preserves strict separation between:
    - observed_at: Physical observation timestamp (period end).
    - received_at: Upstream publication timestamp if reported by provider (None if unknown).
    - fetched_at: Local system retrieval timestamp (strictly recorded upon ingestion).
    """

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sensor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sensors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    raw_batch_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("raw_ingest_batches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="µg/m³")
    quality_flag: Mapped[str] = mapped_column(String(32), nullable=False, default="VALID")
    source_status: Mapped[str] = mapped_column(String(32), nullable=False, default="HEALTHY")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    sensor: Mapped[Sensor] = relationship("Sensor", back_populates="observations")
    raw_batch: Mapped[RawIngestBatch | None] = relationship("RawIngestBatch", back_populates="observations")

    __table_args__ = (
        UniqueConstraint("sensor_id", "observed_at", "version", name="uq_sensor_observed_version"),
        Index("ix_obs_sensor_observed_version", "sensor_id", "observed_at", "version"),
        Index("ix_obs_sensor_observed", "sensor_id", "observed_at"),
        Index("ix_obs_fetched_at", "fetched_at"),
        Index("ix_obs_persisted_at", "persisted_at"),
    )


class ForecastFeatureSnapshot(Base):
    """Immutable snapshot of point-in-time features served to a model inference run.

    Provides cryptographic proof of exact feature inputs visible at decision cutoff,
    preventing any ambiguity regarding database commit timing or subsequent updates.
    """

    __tablename__ = "forecast_feature_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sensor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sensors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    target_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    features_json: Mapped[str] = mapped_column(Text, nullable=False)
    feature_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    sensor: Mapped[Sensor] = relationship("Sensor")


class ForecastRun(Base):
    """Metadata record for a versioned batch or on-demand forecast execution.

    Tracks the exact cryptographic model manifest, dataset hash, issue timestamp,
    and causal decision cutoff used to generate persisted forecast intervals.
    """

    __tablename__ = "forecast_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    algorithm: Mapped[str] = mapped_column(String(64), nullable=False, default="SAGE-LIGHTGBM")
    execution_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="serving")
    evaluation_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="serving")
    data_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="archived_historical")
    model_created_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_immutable_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    training_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    training_data_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    calibration_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    code_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feature_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    nominal_coverage: Mapped[float] = mapped_column(Float, nullable=False, default=0.90)
    min_lead_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    effective_lead_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    decision_cutoff: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    forecasts: Mapped[list[ForecastRecord]] = relationship(
        "ForecastRecord", back_populates="run", cascade="all, delete-orphan"
    )


class ForecastRecord(Base):
    """Persisted station forecast record with guarded interval and provenance metadata."""

    __tablename__ = "forecasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("forecast_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    station_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sensor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sensors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    target_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    point_forecast: Mapped[float] = mapped_column(Float, nullable=False)
    lower_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    nominal_coverage: Mapped[float] = mapped_column(Float, nullable=False, default=0.90)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    abstain_reason: Mapped[str] = mapped_column(String(64), nullable=False, default="NONE")
    d_sensitivity: Mapped[float | None] = mapped_column(Float, nullable=True)
    newest_source_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    feature_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    scenario_bounds_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_immutable_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    training_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_lead_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_lead_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    available_at_decision_cutoff: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    persisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    run: Mapped[ForecastRun] = relationship("ForecastRun", back_populates="forecasts")
    station: Mapped[Station] = relationship("Station")
    sensor: Mapped[Sensor] = relationship("Sensor")

    __table_args__ = (
        UniqueConstraint("run_id", "station_id", "target_time", name="uq_forecasts_run_station_target"),
    )


class PilotQuotaState(Base):
    """Durable PostgreSQL/SQLite daily API request budget shared atomically across workers."""

    __tablename__ = "pilot_quota_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="openaq")
    budget_date_utc: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    requests_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_requests_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=48)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (UniqueConstraint("provider", "budget_date_utc", name="uq_pilot_quota_provider_date"),)


class PilotPollLease(Base):
    """Distributed leader lease preventing concurrent multi-worker OpenAQ polling."""

    __tablename__ = "pilot_poll_leases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lease_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    holder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class PilotControlState(Base):
    """Durable operator kill-switch and opt-in polling authorization gate."""

    __tablename__ = "pilot_control_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    control_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    opt_in_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class PilotIngestAuditRun(Base):
    """Durable operational audit trail for every live/shadow poll attempt and failure."""

    __tablename__ = "pilot_ingest_audit_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="openaq")
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    fencing_token: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    window_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sensors_polled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requests_consumed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    http_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_revised: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_deduplicated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_invalid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recovered_from_expired_lease: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class PilotApiRateLimit(Base):
    """Server-side durable fixed-window API rate limit shared independently across workers."""

    __tablename__ = "pilot_api_rate_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    window_minute_utc: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (UniqueConstraint("scope", "window_minute_utc", name="uq_pilot_api_rate_scope_window"),)
