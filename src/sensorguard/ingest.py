"""Data validation, provenance tracking, and idempotent ingestion pipeline.

Validates upstream records against physical boundaries and units, stores immutable
raw batches with deterministic cryptographic checksums, and handles duplicate
observations idempotently.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sensorguard.db.schema import Observation, RawIngestBatch, Sensor, Station
from sensorguard.providers import OpenAQClient, parse_openaq_datetime

CANONICAL_PM25_UNIT = "µg/m³"
ACCEPTED_UNITS = {"µg/m³", "ug/m3", "µg/m3", "ug/m³"}

PM25_MIN_VALID = 0.0
PM25_MAX_VALID = 1000.0


@dataclass(frozen=True)
class ValidatedRecord:
    """Cleaned, validated observation record ready for database persistence."""

    sensor_id: int
    observed_at: datetime
    period_start: datetime | None
    period_end: datetime | None
    received_at: datetime | None
    fetched_at: datetime
    value: float | None
    unit: str
    quality_flag: str
    source_status: str


class ObservationValidator:
    """Validates raw sensor records and classifies data quality and availability."""

    @staticmethod
    def normalize_unit(unit_str: str | None) -> str | None:
        """Standardize PM2.5 unit representation.

        Returns None if unit is missing or empty, enforcing explicit rejection.
        """
        if not unit_str or not unit_str.strip():
            return None
        cleaned = unit_str.strip()
        if cleaned in ACCEPTED_UNITS:
            return CANONICAL_PM25_UNIT
        return cleaned

    @classmethod
    def validate_openaq_hourly_record(
        cls,
        raw_record: dict[str, Any],
        sensor_db_id: int,
        fetched_at: datetime,
    ) -> ValidatedRecord:
        """Validate and parse a single record from OpenAQ /v3/sensors/{id}/hours.

        Respects OpenAQ's convention: period.datetimeTo and datetime.utc indicate
        the END of the measurement period (observed_at).
        Never infers upstream publication timestamp if not explicitly present.
        """
        period_info = raw_record.get("period") or {}
        datetime_info = raw_record.get("datetime") or {}

        # Period end / observed_at
        period_end = parse_openaq_datetime(period_info.get("datetimeTo")) or parse_openaq_datetime(
            datetime_info
        )
        if period_end is None:
            raise ValueError("Observation record missing valid end-of-period timestamp")

        observed_at = period_end

        # Period start
        period_start = parse_openaq_datetime(period_info.get("datetimeFrom"))
        if period_start is None:
            period_start = observed_at - timedelta(hours=1)

        # Received_at: Only preserve if reported by upstream source metadata
        # OpenAQ sometimes provides updated/received in meta; if absent, strictly None.
        raw_received = raw_record.get("received_at") or raw_record.get("updatedAt")
        received_at = parse_openaq_datetime(raw_received) if raw_received else None

        # Value and unit
        raw_value = raw_record.get("value")
        value: float | None = None
        is_non_finite = False
        if raw_value is not None:
            try:
                v = float(raw_value)
                if math.isnan(v) or math.isinf(v):
                    is_non_finite = True
                else:
                    value = v
            except (ValueError, TypeError):
                is_non_finite = True

        param_info = raw_record.get("parameter") or {}
        raw_unit = param_info.get("units") or param_info.get("unit")
        canonical_unit = cls.normalize_unit(raw_unit)

        # Validation logic
        quality_flag = "VALID"
        source_status = "HEALTHY"

        if is_non_finite:
            quality_flag = "NON_FINITE_VALUE"
            source_status = "INVALID"
            value = None
            resolved_unit = canonical_unit or "UNKNOWN"
        elif value is None:
            quality_flag = "MISSING_VALUE"
            source_status = "NO_REPORT"
            resolved_unit = canonical_unit or CANONICAL_PM25_UNIT
        elif canonical_unit is None:
            quality_flag = "MISSING_UNIT"
            source_status = "INVALID"
            resolved_unit = "UNKNOWN"
        elif canonical_unit not in ACCEPTED_UNITS:
            quality_flag = "UNRECOGNIZED_UNIT"
            source_status = "INVALID"
            resolved_unit = canonical_unit
        elif value < PM25_MIN_VALID or value > PM25_MAX_VALID:
            quality_flag = "OUT_OF_BOUNDS"
            source_status = "INVALID"
            resolved_unit = canonical_unit
        else:
            resolved_unit = canonical_unit

        return ValidatedRecord(
            sensor_id=sensor_db_id,
            observed_at=observed_at,
            period_start=period_start,
            period_end=period_end,
            received_at=received_at,
            fetched_at=fetched_at,
            value=value,
            unit=resolved_unit,
            quality_flag=quality_flag,
            source_status=source_status,
        )


@dataclass
class IngestResult:
    """Summary of an ingestion run."""

    batches_saved: int = 0
    records_fetched: int = 0
    records_inserted: int = 0
    records_revised: int = 0
    records_deduplicated: int = 0
    records_invalid: int = 0


class IngestionPipeline:
    """Orchestrates station metadata persistence, batch storage, and idempotent ingestion."""

    def __init__(self, session: Session) -> None:
        """Initialize the pipeline with an active SQLAlchemy session."""
        self.session = session
        self.validator = ObservationValidator()

    def upsert_station_and_sensors(
        self,
        location_payload: dict[str, Any],
        provider: str = "openaq",
    ) -> tuple[Station, list[Sensor]]:
        """Idempotently register or update a station and its attached sensors.

        Rejects null island (0, 0) and unphysical/missing coordinates to protect
        spatial research integrity.
        """
        loc_id = location_payload.get("id")
        if loc_id is None:
            raise ValueError("Location payload missing 'id'")

        stmt = select(Station).where(
            Station.provider == provider,
            Station.provider_location_id == loc_id,
        )
        station = self.session.scalars(stmt).first()

        coords = location_payload.get("coordinates") or {}
        lat = coords.get("latitude") if isinstance(coords, dict) else None
        lon = coords.get("longitude") if isinstance(coords, dict) else None
        if lat is None:
            lat = location_payload.get("latitude")
        if lon is None:
            lon = location_payload.get("longitude")

        if lat is None or lon is None:
            raise ValueError(f"Location {loc_id} missing coordinates")

        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (ValueError, TypeError) as err:
            raise ValueError(f"Location {loc_id} coordinates unparseable: lat={lat}, lon={lon}") from err

        if not (-90.0 <= lat_f <= 90.0) or not (-180.0 <= lon_f <= 180.0) or (lat_f == 0.0 and lon_f == 0.0):
            raise ValueError(
                f"Location {loc_id} coordinates invalid or null island: lat={lat_f}, lon={lon_f}"
            )

        name = location_payload.get("name") or f"Station-{loc_id}"
        country_obj = location_payload.get("country")
        country_code = (
            country_obj.get("code")
            if isinstance(country_obj, dict)
            else str(country_obj)
            if country_obj
            else None
        )
        timezone_name = location_payload.get("timezone")

        if not station:
            station = Station(
                provider=provider,
                provider_location_id=loc_id,
                name=name,
                latitude=lat_f,
                longitude=lon_f,
                country=country_code,
                timezone_name=timezone_name,
                metadata_json=json.dumps(location_payload),
            )
            self.session.add(station)
            self.session.flush()
        else:
            station.name = name
            station.latitude = lat_f
            station.longitude = lon_f
            station.country = country_code
            station.timezone_name = timezone_name
            station.metadata_json = json.dumps(location_payload)
            station.updated_at = datetime.now(UTC)
            self.session.flush()

        # Process sensors
        raw_sensors = location_payload.get("sensors", [])
        registered_sensors: list[Sensor] = []
        for s_data in raw_sensors:
            p_sensor_id = s_data.get("id")
            if not p_sensor_id:
                continue
            param = s_data.get("parameter", {})
            param_name = param.get("name", "unknown") if isinstance(param, dict) else str(param)
            raw_unit = param.get("units", "µg/m³") if isinstance(param, dict) else "µg/m³"
            unit = self.validator.normalize_unit(raw_unit)

            s_stmt = select(Sensor).where(Sensor.provider_sensor_id == p_sensor_id)
            sensor = self.session.scalars(s_stmt).first()
            if not sensor:
                sensor = Sensor(
                    station_id=station.id,
                    provider_sensor_id=p_sensor_id,
                    parameter=param_name.lower(),
                    canonical_unit=unit,
                    averaging_period_hours=1.0,
                    metadata_json=json.dumps(s_data),
                )
                self.session.add(sensor)
                self.session.flush()
            registered_sensors.append(sensor)

        return station, registered_sensors

    def ingest_sensor_hours(
        self,
        sensor_db_id: int,
        provider_sensor_id: int,
        client: OpenAQClient,
        datetime_from: str | None = None,
        datetime_to: str | None = None,
        max_pages: int = 5,
        page_size: int = 100,
    ) -> IngestResult:
        """Fetch and persist hourly observations for a sensor with versioned revisions.

        Preserves verifiable raw HTTP response text and implements append-only
        versioning for later revised/corrected measurements.
        """
        result = IngestResult()

        for resp in client.paginate_sensor_hours(
            sensor_id=provider_sensor_id,
            datetime_from=datetime_from,
            datetime_to=datetime_to,
            page_size=page_size,
            max_pages=max_pages,
        ):
            results = resp.payload.get("results", [])
            result.records_fetched += len(results)

            now_utc = datetime.now(UTC)
            # Store immutable raw batch preserving exact response text, bytes, and cryptographic hash
            raw_bytes = (
                resp.raw_bytes
                if (hasattr(resp, "raw_bytes") and resp.raw_bytes)
                else resp.raw_text.encode("utf-8")
                if resp.raw_text
                else None
            )
            raw_batch = RawIngestBatch(
                provider="openaq",
                endpoint=f"/v3/sensors/{provider_sensor_id}/hours",
                params_json=json.dumps({"datetime_from": datetime_from, "datetime_to": datetime_to}),
                fetched_at=resp.fetched_at,
                persisted_at=now_utc,
                status_code=200,
                response_hash=resp.response_hash,
                payload_json=resp.raw_text,
                raw_bytes=raw_bytes,
                record_count=len(results),
            )
            self.session.add(raw_batch)
            self.session.flush()
            result.batches_saved += 1

            for record in results:
                try:
                    val = self.validator.validate_openaq_hourly_record(
                        record, sensor_db_id=sensor_db_id, fetched_at=resp.fetched_at
                    )
                except (ValueError, TypeError, KeyError):
                    result.records_invalid += 1
                    continue

                if val.quality_flag != "VALID":
                    result.records_invalid += 1

                # Validation of inconsistent timestamps: observation cannot be in the future relative to fetch
                if val.observed_at > resp.fetched_at:
                    val_flag = "FUTURE_TIMESTAMP_INCONSISTENCY"
                    val_status = "INVALID"
                    result.records_invalid += 1
                else:
                    val_flag = val.quality_flag
                    val_status = val.source_status

                # Check existing observations for this sensor_id and observed_at (order by version desc)
                obs_stmt = (
                    select(Observation)
                    .where(
                        Observation.sensor_id == sensor_db_id,
                        Observation.observed_at == val.observed_at,
                    )
                    .order_by(Observation.version.desc())
                )
                latest_existing = self.session.scalars(obs_stmt).first()

                if latest_existing is None:
                    # Initial observation: version 1
                    obs = Observation(
                        sensor_id=sensor_db_id,
                        raw_batch_id=raw_batch.id,
                        observed_at=val.observed_at,
                        period_start=val.period_start,
                        period_end=val.period_end,
                        received_at=val.received_at,
                        fetched_at=val.fetched_at,
                        persisted_at=now_utc,
                        value=val.value,
                        unit=val.unit,
                        quality_flag=val_flag,
                        source_status=val_status,
                        version=1,
                    )
                    self.session.add(obs)
                    result.records_inserted += 1
                else:
                    # Check if identical measurement or revised correction
                    same_val = (latest_existing.value == val.value) or (
                        latest_existing.value is None and val.value is None
                    )
                    same_status = (latest_existing.source_status == val_status) and (
                        latest_existing.quality_flag == val_flag
                    )
                    if same_val and same_status:
                        # Identical measurement: deduplicate
                        result.records_deduplicated += 1
                    else:
                        # Revised/corrected measurement: append with incremented version
                        new_version = latest_existing.version + 1
                        obs = Observation(
                            sensor_id=sensor_db_id,
                            raw_batch_id=raw_batch.id,
                            observed_at=val.observed_at,
                            period_start=val.period_start,
                            period_end=val.period_end,
                            received_at=val.received_at,
                            fetched_at=val.fetched_at,
                            persisted_at=now_utc,
                            value=val.value,
                            unit=val.unit,
                            quality_flag=val_flag,
                            source_status=val_status,
                            version=new_version,
                        )
                        self.session.add(obs)
                        result.records_revised += 1

        self.session.commit()
        return result
