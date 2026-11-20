"""Station discovery, PM2.5 coverage audit, and feasibility reporting.

Audits candidate monitoring networks for station count, temporal coverage,
measurement cadence, and gap rates without assuming a city has enough sensors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sensorguard.providers import OpenAQClient, parse_openaq_datetime


@dataclass(frozen=True)
class SensorCoverageStats:
    """Coverage statistics for an individual sensor."""

    sensor_id: int
    parameter: str
    unit: str
    total_records: int
    first_observed_at: str | None
    last_observed_at: str | None
    hours_spanned: float
    missing_gap_rate: float
    cadence_hours: float
    is_usable_history: bool


@dataclass(frozen=True)
class StationAuditReport:
    """Comprehensive feasibility audit of a candidate monitoring location."""

    location_id: int
    station_name: str
    latitude: float
    longitude: float
    country: str | None
    pm25_sensor_count: int
    sensors: list[SensorCoverageStats]
    has_sufficient_pm25: bool
    summary: str


def audit_sensor_coverage(
    sensor_id: int,
    client: OpenAQClient,
    sample_limit: int = 100,
) -> SensorCoverageStats:
    """Audit an individual sensor's coverage, cadence, and missingness."""
    payload, _, _ = client.get_sensor_hours_raw(sensor_id=sensor_id, limit=sample_limit)
    results = payload.get("results", [])

    if not results:
        return SensorCoverageStats(
            sensor_id=sensor_id,
            parameter="pm25",
            unit="µg/m³",
            total_records=0,
            first_observed_at=None,
            last_observed_at=None,
            hours_spanned=0.0,
            missing_gap_rate=1.0,
            cadence_hours=1.0,
            is_usable_history=False,
        )

    timestamps: list[datetime] = []
    unit = "µg/m³"
    param_name = "pm25"

    for r in results:
        param_obj = r.get("parameter") or {}
        if isinstance(param_obj, dict):
            unit = param_obj.get("units", unit)
            param_name = param_obj.get("name", param_name)
        dt = parse_openaq_datetime(r.get("datetime"))
        if dt:
            timestamps.append(dt)

    timestamps.sort()
    total = len(timestamps)

    if total < 2:
        return SensorCoverageStats(
            sensor_id=sensor_id,
            parameter=param_name,
            unit=unit,
            total_records=total,
            first_observed_at=timestamps[0].isoformat() if timestamps else None,
            last_observed_at=timestamps[-1].isoformat() if timestamps else None,
            hours_spanned=1.0,
            missing_gap_rate=0.0,
            cadence_hours=1.0,
            is_usable_history=False,
        )

    first_dt = timestamps[0]
    last_dt = timestamps[-1]
    hours_spanned = max(1.0, (last_dt - first_dt).total_seconds() / 3600.0)

    # Expected hourly records in that span
    expected_hours = max(1.0, hours_spanned + 1.0)
    gap_rate = max(0.0, 1.0 - (total / expected_hours))
    cadence = hours_spanned / max(1, total - 1)

    # Usable history criterion: at least 24 records and gap rate < 40%
    usable = total >= 24 and gap_rate < 0.40

    return SensorCoverageStats(
        sensor_id=sensor_id,
        parameter=param_name,
        unit=unit,
        total_records=total,
        first_observed_at=first_dt.isoformat(),
        last_observed_at=last_dt.isoformat(),
        hours_spanned=round(hours_spanned, 2),
        missing_gap_rate=round(gap_rate, 4),
        cadence_hours=round(cadence, 2),
        is_usable_history=usable,
    )


def audit_candidate_location(
    location_id: int,
    client: OpenAQClient,
    sample_limit: int = 100,
) -> StationAuditReport:
    """Audit a specific location and all its PM2.5 sensors."""
    payload, _, _ = client.get_location(location_id)
    results = payload.get("results", [])
    if not results:
        raise ValueError(f"Location {location_id} not found or returned empty results")

    loc_data = results[0] if isinstance(results, list) else results
    name = loc_data.get("name", f"Location-{location_id}")
    coords = loc_data.get("coordinates") or {}
    lat = coords.get("latitude", 0.0) if isinstance(coords, dict) else 0.0
    lon = coords.get("longitude", 0.0) if isinstance(coords, dict) else 0.0
    country = loc_data.get("country", {}).get("code") if isinstance(loc_data.get("country"), dict) else None

    raw_sensors = loc_data.get("sensors", [])
    pm25_sensors = [
        s
        for s in raw_sensors
        if (
            s.get("parameter", {}).get("name") if isinstance(s.get("parameter"), dict) else s.get("parameter")
        )
        == "pm25"
    ]

    sensor_stats: list[SensorCoverageStats] = []
    for s in pm25_sensors:
        sid = s.get("id")
        if sid:
            stats = audit_sensor_coverage(sensor_id=sid, client=client, sample_limit=sample_limit)
            sensor_stats.append(stats)

    usable_pm25 = sum(1 for s in sensor_stats if s.is_usable_history)
    has_sufficient = usable_pm25 > 0

    summary = (
        f"Station '{name}' (ID: {location_id}): {len(pm25_sensors)} PM2.5 sensors found, "
        f"{usable_pm25} meet usability criteria (>=24h span, <40% gap rate)."
    )

    return StationAuditReport(
        location_id=location_id,
        station_name=name,
        latitude=float(lat),
        longitude=float(lon),
        country=country,
        pm25_sensor_count=len(pm25_sensors),
        sensors=sensor_stats,
        has_sufficient_pm25=has_sufficient,
        summary=summary,
    )
