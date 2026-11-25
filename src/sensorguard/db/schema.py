"""SQLAlchemy 2.0 ORM models for SensorGuard data lineage and storage.

All models are designed for full compatibility with both SQLite (local development
and offline testing) and PostgreSQL (production MLOps deployment).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
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
    nominal_coverage: Mapped[float] = mapped_column(Float, nullable=False, default=0.90)
    issued_at: Mapped[datetime] = mapped_column(
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
    decision_cutoff: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    target_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    point_forecast: Mapped[float] = mapped_column(Float, nullable=False)
    lower_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    nominal_coverage: Mapped[float] = mapped_column(Float, nullable=False, default=0.90)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    abstain_reason: Mapped[str] = mapped_column(String(64), nullable=False, default="NONE")
    d_sensitivity: Mapped[float | None] = mapped_column(Float, nullable=True)
    newest_source_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    feature_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    scenario_bounds_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    run: Mapped[ForecastRun] = relationship("ForecastRun", back_populates="forecasts")
    station: Mapped[Station] = relationship("Station")
    sensor: Mapped[Sensor] = relationship("Sensor")
