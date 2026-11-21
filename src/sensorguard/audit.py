"""Station discovery, PM2.5 coverage audit, and feasibility reporting.

Audits candidate monitoring networks for station count, temporal coverage,
measurement cadence, gap rates, and spatial network overlap without assuming
a city has enough sensors.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from sensorguard.providers import OpenAQClient, parse_openaq_datetime

PM25_MIN_VALID = 0.0
PM25_MAX_VALID = 1000.0


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
    valid_records: int = 0
    expected_hours: float = 0.0
    max_consecutive_gap_hours: float = 0.0
    observed_timestamps: list[str] = field(default_factory=list)


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


@dataclass(frozen=True)
class NetworkFeasibilityReport:
    """Rigorous feasibility report for a candidate sensor network."""

    network_name: str
    country: str
    bounding_box: tuple[float, float, float, float]
    datetime_from: str
    datetime_to: str
    total_expected_hours: int
    total_audited_stations: int
    eligible_stations_count: int
    station_reports: list[StationAuditReport]
    overlap_hours_any: int
    overlap_hours_4plus: int
    overlap_hours_6plus: int
    overlap_hours_all: int
    overlap_pct_4plus: float
    selection_decision: str  # "APPROVED" or "REJECTED"
    decision_rationale: str
    criteria: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert report to clean, JSON-serializable dictionary without secrets."""
        return asdict(self)


def audit_sensor_coverage(
    sensor_id: int,
    client: OpenAQClient,
    datetime_from: str | None = None,
    datetime_to: str | None = None,
    max_pages: int = 10,
    page_size: int = 1000,
) -> SensorCoverageStats:
    """Audit an individual sensor's coverage, cadence, and missingness across paginated date ranges.

    Deduplicates records by timestamp, enforces physical validity bounds [0, 1000],
    and computes maximum contiguous missing interval.
    """
    timestamp_map: dict[datetime, float | None] = {}
    unit = "µg/m³"
    param_name = "pm25"

    for resp in client.paginate_sensor_hours(
        sensor_id=sensor_id,
        datetime_from=datetime_from,
        datetime_to=datetime_to,
        page_size=page_size,
        max_pages=max_pages,
    ):
        results = resp.payload.get("results", [])
        for r in results:
            param_obj = r.get("parameter") or {}
            if isinstance(param_obj, dict):
                unit = param_obj.get("units", unit)
                param_name = param_obj.get("name", param_name)

            dt = parse_openaq_datetime(r.get("datetime"))
            if dt is None:
                period = r.get("period") or {}
                dt = parse_openaq_datetime(period.get("datetimeTo"))

            if dt is not None:
                dt_floor = dt.replace(minute=0, second=0, microsecond=0)
                raw_val = r.get("value")
                val_f: float | None = None
                if raw_val is not None:
                    try:
                        v = float(raw_val)
                        if not math.isnan(v) and not math.isinf(v) and PM25_MIN_VALID <= v <= PM25_MAX_VALID:
                            val_f = v
                    except (ValueError, TypeError):
                        val_f = None

                if dt_floor not in timestamp_map or (
                    val_f is not None and timestamp_map[dt_floor] is None
                ):
                    timestamp_map[dt_floor] = val_f

    if not timestamp_map:
        return SensorCoverageStats(
            sensor_id=sensor_id,
            parameter=param_name,
            unit=unit,
            total_records=0,
            first_observed_at=None,
            last_observed_at=None,
            hours_spanned=0.0,
            missing_gap_rate=1.0,
            cadence_hours=1.0,
            is_usable_history=False,
            valid_records=0,
            expected_hours=0.0,
            max_consecutive_gap_hours=0.0,
            observed_timestamps=[],
        )

    sorted_dts = sorted(timestamp_map.keys())
    total_unique = len(sorted_dts)
    valid_count = sum(1 for v in timestamp_map.values() if v is not None)

    first_dt = sorted_dts[0]
    last_dt = sorted_dts[-1]

    req_from = parse_openaq_datetime(datetime_from) if datetime_from else None
    req_to = parse_openaq_datetime(datetime_to) if datetime_to else None

    if req_from and req_to and req_to > req_from:
        hours_spanned = max(1.0, (req_to - req_from).total_seconds() / 3600.0)
        expected_hours = hours_spanned + 1.0
    else:
        hours_spanned = max(1.0, (last_dt - first_dt).total_seconds() / 3600.0)
        expected_hours = max(1.0, hours_spanned + 1.0)

    gap_rate = max(0.0, 1.0 - (valid_count / expected_hours))
    cadence = hours_spanned / max(1, total_unique - 1)

    # Calculate largest contiguous missing gap in hours
    if total_unique >= 2:
        consecutive_gaps = [
            max(0.0, (sorted_dts[i + 1] - sorted_dts[i]).total_seconds() / 3600.0 - 1.0)
            for i in range(len(sorted_dts) - 1)
        ]
        max_gap = max(consecutive_gaps) if consecutive_gaps else 0.0
    else:
        max_gap = hours_spanned

    if req_from and first_dt > req_from:
        max_gap = max(max_gap, (first_dt - req_from).total_seconds() / 3600.0)
    if req_to and req_to > last_dt:
        max_gap = max(max_gap, (req_to - last_dt).total_seconds() / 3600.0)

    # Dynamic usability classification based on window duration
    if expected_hours > 500:
        # Multi-month window: requires >= 500 valid records, <= 40% gap rate, and no contiguous 7-day outage
        usable = valid_count >= 500 and gap_rate <= 0.40 and max_gap <= 168.0
    else:
        usable = valid_count >= 5 and gap_rate <= 0.40

    return SensorCoverageStats(
        sensor_id=sensor_id,
        parameter=param_name,
        unit=unit,
        total_records=total_unique,
        first_observed_at=first_dt.isoformat(),
        last_observed_at=last_dt.isoformat(),
        hours_spanned=round(hours_spanned, 2),
        missing_gap_rate=round(gap_rate, 4),
        cadence_hours=round(cadence, 2),
        is_usable_history=usable,
        valid_records=valid_count,
        expected_hours=round(expected_hours, 1),
        max_consecutive_gap_hours=round(max_gap, 2),
        observed_timestamps=[dt.isoformat() for dt in sorted_dts],
    )


def audit_candidate_location(
    location_id: int,
    client: OpenAQClient,
    datetime_from: str | None = None,
    datetime_to: str | None = None,
    max_pages: int = 10,
    page_size: int = 1000,
) -> StationAuditReport:
    """Audit a specific location and all its PM2.5 sensors with coordinate verification."""
    resp = client.get_location(location_id)
    results = resp.payload.get("results", [])
    if not results:
        raise ValueError(f"Location {location_id} not found or returned empty results")

    loc_data = results[0] if isinstance(results, list) else results
    name = loc_data.get("name", f"Location-{location_id}")
    coords = loc_data.get("coordinates") or {}
    lat = coords.get("latitude") if isinstance(coords, dict) else None
    lon = coords.get("longitude") if isinstance(coords, dict) else None
    if lat is None:
        lat = loc_data.get("latitude")
    if lon is None:
        lon = loc_data.get("longitude")

    if lat is None or lon is None:
        raise ValueError(f"Location {location_id} missing coordinates")

    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (ValueError, TypeError) as err:
        raise ValueError(f"Location {location_id} coordinates unparseable: lat={lat}, lon={lon}") from err

    if not (-90.0 <= lat_f <= 90.0) or not (-180.0 <= lon_f <= 180.0) or (lat_f == 0.0 and lon_f == 0.0):
        raise ValueError(
            f"Location {location_id} coordinates invalid or null island: lat={lat_f}, lon={lon_f}"
        )

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
            stats = audit_sensor_coverage(
                sensor_id=sid,
                client=client,
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                max_pages=max_pages,
                page_size=page_size,
            )
            sensor_stats.append(stats)

    usable_pm25 = sum(1 for s in sensor_stats if s.is_usable_history)
    has_sufficient = usable_pm25 > 0

    summary = (
        f"Station '{name}' (ID: {location_id}): {len(pm25_sensors)} PM2.5 sensors found, "
        f"{usable_pm25} meet usability criteria."
    )

    return StationAuditReport(
        location_id=location_id,
        station_name=name,
        latitude=lat_f,
        longitude=lon_f,
        country=country,
        pm25_sensor_count=len(pm25_sensors),
        sensors=sensor_stats,
        has_sufficient_pm25=has_sufficient,
        summary=summary,
    )


def audit_network_feasibility(
    location_ids: list[int],
    client: OpenAQClient,
    datetime_from: str,
    datetime_to: str,
    network_name: str = "Candidate Air Quality Network",
    country: str = "GB",
    bounding_box: tuple[float, float, float, float] = (-0.85, 50.80, 1.60, 52.35),
    min_eligible_stations: int = 8,
    max_gap_rate: float = 0.40,
    min_overlap_4plus_pct: float = 0.60,
    max_pages: int = 10,
    page_size: int = 1000,
) -> NetworkFeasibilityReport:
    """Audit full network feasibility, spatial overlap, and derive evidence-based approval decision.

    Computes multi-station concurrency across a continuous hourly grid.
    Never hardcodes approval decisions; derives eligibility strictly from observed coverage.
    """
    req_from = parse_openaq_datetime(datetime_from)
    req_to = parse_openaq_datetime(datetime_to)
    if req_from is None or req_to is None or req_to <= req_from:
        raise ValueError("Valid datetime_from and datetime_to (with datetime_to > datetime_from) required")

    expected_hours_idx = pd.date_range(start=req_from, end=req_to, freq="1h", tz=UTC)
    total_expected_hours = len(expected_hours_idx)

    station_reports: list[StationAuditReport] = []
    station_active_hours: dict[int, set[datetime]] = {}

    for loc_id in location_ids:
        report = audit_candidate_location(
            location_id=loc_id,
            client=client,
            datetime_from=datetime_from,
            datetime_to=datetime_to,
            max_pages=max_pages,
            page_size=page_size,
        )
        station_reports.append(report)

        active_ts: set[datetime] = set()
        for s in report.sensors:
            for ts_str in s.observed_timestamps:
                dt = parse_openaq_datetime(ts_str)
                if dt:
                    active_ts.add(dt.replace(minute=0, second=0, microsecond=0))
        station_active_hours[loc_id] = active_ts

    eligible_stations = [r for r in station_reports if r.has_sufficient_pm25]
    eligible_count = len(eligible_stations)

    # Compute network concurrency overlap across the expected hourly grid
    hourly_concurrency: dict[datetime, int] = {}
    for h in expected_hours_idx:
        dt_h = h.to_pydatetime()
        count = sum(1 for loc_id in location_ids if dt_h in station_active_hours.get(loc_id, set()))
        hourly_concurrency[dt_h] = count

    overlap_any = sum(1 for c in hourly_concurrency.values() if c >= 1)
    overlap_4plus = sum(1 for c in hourly_concurrency.values() if c >= 4)
    overlap_6plus = sum(1 for c in hourly_concurrency.values() if c >= 6)
    overlap_all = sum(1 for c in hourly_concurrency.values() if c >= len(location_ids))
    overlap_pct_4plus = round(overlap_4plus / total_expected_hours, 4) if total_expected_hours > 0 else 0.0

    # Decision rule: requires sufficient eligible stations AND sufficient concurrent overlap for spatial ML
    is_approved = eligible_count >= min_eligible_stations and overlap_pct_4plus >= min_overlap_4plus_pct
    decision = "APPROVED" if is_approved else "REJECTED"

    if is_approved:
        rationale = (
            f"Approved: {eligible_count}/{len(location_ids)} stations meet data completeness criteria "
            f"(gap rate <= {max_gap_rate:.0%}). 4+ station spatial overlap is {overlap_pct_4plus:.1%} "
            f"across {total_expected_hours} hours, meeting the {min_overlap_4plus_pct:.0%} threshold "
            f"required for distance-weighted spatial estimation and controlled outage benchmarking."
        )
    else:
        rationale = (
            f"Rejected: {eligible_count}/{len(location_ids)} stations meet criteria (minimum {min_eligible_stations} required). "
            f"4+ station spatial overlap is {overlap_pct_4plus:.1%} (minimum {min_overlap_4plus_pct:.0%} required)."
        )

    criteria = {
        "min_eligible_stations": min_eligible_stations,
        "max_gap_rate": max_gap_rate,
        "min_overlap_4plus_pct": min_overlap_4plus_pct,
        "cadence_required": "1.0h",
        "parameter_required": "pm25",
        "unit_canonical": "µg/m³",
    }

    return NetworkFeasibilityReport(
        network_name=network_name,
        country=country,
        bounding_box=bounding_box,
        datetime_from=datetime_from,
        datetime_to=datetime_to,
        total_expected_hours=total_expected_hours,
        total_audited_stations=len(location_ids),
        eligible_stations_count=eligible_count,
        station_reports=station_reports,
        overlap_hours_any=overlap_any,
        overlap_hours_4plus=overlap_4plus,
        overlap_hours_6plus=overlap_6plus,
        overlap_hours_all=overlap_all,
        overlap_pct_4plus=overlap_pct_4plus,
        selection_decision=decision,
        decision_rationale=rationale,
        criteria=criteria,
    )
