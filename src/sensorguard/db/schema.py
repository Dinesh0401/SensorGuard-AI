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
