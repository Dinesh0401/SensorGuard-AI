# SensorGuard AI — Data Dictionary

**Version:** 1.0  
**Date:** 2026-11-25

---

## Overview

This document defines every data entity, field, unit, and timestamp semantic used in SensorGuard AI.

> [!IMPORTANT]
> **Timestamp integrity rule:** `observed_at` ≤ `fetched_at` always. Any observation violating this is rejected with status `FUTURE_TIMESTAMP_INCONSISTENCY`. Never conflate `observed_at`, `fetched_at`, `received_at`, `issued_at`, and `target_at`.

---

## 1. Stations

| Field | Type | Description |
|---|---|---|
| `id` | int | Internal primary key |
| `provider` | str | Data provider name (e.g. `openaq`) |
| `provider_location_id` | int | Provider's location/station ID |
| `name` | str | Human-readable station name |
| `lat` | float | Latitude in decimal degrees (WGS84) |
| `lon` | float | Longitude in decimal degrees (WGS84) |
| `timezone` | str | IANA timezone identifier (e.g. `Europe/London`) |
| `metadata_json` | json | Provider-supplied metadata (verbatim) |
| `created_at` | datetime UTC | When the station record was first inserted |

### Pilot stations (Q1 2024)

| id | name | provider_location_id | lat | lon |
|---|---|---|---|---|
| 1 | London Harlington | 141 | 51.4884 | -0.4172 |
| 2 | London Hillingdon | 153 | 51.5209 | -0.4766 |
| 3 | Westminster Marylebone Road | 154 | 51.5222 | -0.1545 |
| 4 | Bexley Slade Green | 157 | 51.4712 | 0.1783 |
| 5 | Southampton Centre | 698 | 50.9097 | -1.4049 |
| 6 | Wicken Fen | 804 | 52.3000 | 0.2904 |
| 7 | St Osyth | 1795 | 51.7905 | 1.0792 |
| 8 | Manchester Piccadilly | 2312 | 53.4809 | -2.2374 |

---

## 2. Sensors

| Field | Type | Description |
|---|---|---|
| `id` | int | Internal primary key |
| `station_id` | int | FK → stations.id |
| `provider_sensor_id` | str | Provider's sensor identifier |
| `parameter` | str | Pollutant parameter (e.g. `pm25`) |
| `canonical_unit` | str | Normalised unit (always `µg/m³` for PM₂.₅) |
| `averaging_period` | str | Averaging window (e.g. `1h`) |

---

## 3. Raw Ingest Batches

| Field | Type | Description |
|---|---|---|
| `id` | int | Internal primary key |
| `provider` | str | Provider name |
| `fetched_at` | datetime UTC | Exact time the HTTP response was received |
| `status` | str | `SUCCESS`, `PARTIAL`, `FAILED` |
| `raw_path` | str | Path to stored raw payload (if any) |
| `response_hash` | str | SHA-256 of the raw HTTP response body |
| `error_code` | str/null | HTTP status or error type if failed |

---

## 4. Observations

| Field | Type | Description |
|---|---|---|
| `id` | int | Internal primary key |
| `sensor_id` | int | FK → sensors.id |
| `observed_at` | datetime UTC | Physical measurement timestamp (end of averaging interval) |
| `received_at` | datetime UTC / null | Provider publication time (NULL if not reported by upstream) |
| `fetched_at` | datetime UTC | Time SensorGuard fetched this observation |
| `persisted_at` | datetime UTC | Time this row was inserted into the database |
| `value` | float | PM₂.₅ concentration in µg/m³ |
| `unit` | str | Original provider unit (always normalised to µg/m³) |
| `quality_flag` | str | `VALID`, `INVALID`, `ESTIMATED`, `NO_REPORT`, `LATE`, `FUTURE_TIMESTAMP_INCONSISTENCY` |
| `raw_batch_id` | int | FK → raw_ingest_batches.id |
| `source_version` | int | Revision counter (1 = first ingest, 2+ = updated value from provider) |
| `reported` | bool | Whether the value was actually reported (False = derived absence record) |

### Unique constraint
`(sensor_id, observed_at, source_version)` — allows append-only versioning for provider corrections.

---

## 5. Forecasts

| Field | Type | Description |
|---|---|---|
| `id` | int | Internal primary key |
| `station_id` | int | FK → stations.id |
| `issued_at` | datetime UTC | When the forecast was computed |
| `target_at` | datetime UTC | The future timestamp being forecast |
| `horizon_hours` | int | Forecast horizon (always 1 for MVP) |
| `point_estimate` | float | Point PM₂.₅ forecast in µg/m³ |
| `lower` | float | Lower bound of prediction interval (µg/m³) |
| `upper` | float | Upper bound of prediction interval (µg/m³) |
| `nominal_coverage` | float | Nominal coverage level (0.9) |
| `decision` | str | `publish` or `abstain` |
| `decision_reason` | str | Human-readable explanation of decision |
| `d_sensitivity` | float | Scenario-disagreement signal value |
| `model_version_id` | str | Model artefact identifier |
| `newest_source_observed_at` | datetime UTC | Freshest observation used to compute this forecast |

---

## 6. Experiments

| Field | Type | Description |
|---|---|---|
| `id` | str | Experiment identifier (e.g. `SG-BENCH-20261125-040900`) |
| `dataset_manifest_hash` | str | 64-bit hex content hash of train/cal/test splits |
| `config_json` | json | Experiment configuration (seeds, gamma grid, thresholds) |
| `started_at` | datetime UTC | Experiment start time |
| `completed_at` | datetime UTC | Experiment end time |
| `artifacts_uri` | str | Path to committed result JSON |

---

## 7. Feature Definitions

All features are computed at forecast issue time `t` using only data with `observed_at ≤ t` and `fetched_at ≤ t`.

| Feature | Description | Leakage Risk |
|---|---|---|
| `last_pm25` | Most recent observed PM₂.₅ at this station | None |
| `hours_since_last_obs` | Hours since most recent valid observation | None |
| `freshness_flag` | `fresh` / `moderate_delay` / `stale_history` | None |
| `is_reporting` | Whether station reported in the last hour | None |
| `hour_sin`, `hour_cos` | Circular encoding of UTC hour | None |
| `day_of_week_sin/cos` | Circular encoding of weekday | None |
| `neighbor_pm25_mean` | Mean PM₂.₅ of neighbouring stations at issue time | None |
| `neighbor_pm25_std` | Std of PM₂.₅ of neighbouring stations | None |
| `neighbor_reporting_fraction` | Fraction of neighbours currently reporting | None |

> [!CAUTION]
> Never use `observed_at = target_at` features (target-time values). Feature construction uses `observed_at ≤ t` strictly.

---

## 8. Missingness Status Codes

| Code | Meaning |
|---|---|
| `HEALTHY` | Station reporting normally |
| `NO_REPORT` | No value received in this period; cause unknown |
| `LATE` | Value arrived after the decision cutoff but was later received |
| `INVALID` | Value failed quality check |
| `SOURCE_UNAVAILABLE` | Provider API returned error |
| `OFFLINE_CONFIRMED` | Station outage confirmed by operator |
| `FUTURE_TIMESTAMP_INCONSISTENCY` | `observed_at > fetched_at` — rejected |

---

## 9. Dataset Splits (Q1 2024 Pilot)

| Split | n_observations | Usage |
|---|---|---|
| Train | 8,884 | Model training |
| Calibration | 2,937 | Conformal calibration |
| Test | 3,496 | Evaluation (2,727 have valid targets) |
| **Total** | **15,317** | **14,556 after deduplication** |

**Dataset content hash:** `33805c42097fc86e`  
**Benchmark ID:** `SG-BENCH-20261125-040900`

---

## 10. Units and Conventions

| Quantity | Unit | Notes |
|---|---|---|
| PM₂.₅ concentration | µg/m³ | All values normalised to µg/m³ |
| Latitude | decimal degrees | WGS84 |
| Longitude | decimal degrees | WGS84 |
| All timestamps | UTC | Stored as timezone-aware datetime |
| Forecast horizon | hours | Integer, always 1 for MVP |
| Nominal coverage | fraction | 0.9 = 90% |
| Gamma sensitivity parameter | dimensionless | ≥ 1.0; 1.0 = MAR assumption |
