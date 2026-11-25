# SensorGuard AI — Data Dictionary & Schema Reference

**Version:** 1.1  
**Date:** 2026-11-25

---

## 1. Exact Dataset & Split Reconciliation (Q1 2024 UK AURN Pilot)

| Stage / Table | Count | Period (UTC) & Definition |
|---|---:|---|
| Raw `observations` (`VALID`) | **14,556** | `2024-01-01T01:00Z` – `2024-03-31T23:00Z` across 8 stations |
| Reindexed hourly station grid | 17,464 | `2,183` hours × `8` stations |
| After dropping pre-first-report warmup | 17,395 | Drops `69` rows with `last_pm25 == NaN` (`14,548` have `reported=True`, omitting each station's single initial measurement) |
| **Train split (60%)** | **8,884** | `2024-01-01T02:00Z` – `2024-02-24T14:00Z` (`reported=True` only) |
| **Calibration split (20%)** | **2,937** | `2024-02-24T15:00Z` – `2024-03-13T18:00Z` (`reported=True` only) |
| **Test split grid (20%)** | **3,496** | `2024-03-13T19:00Z` – `2024-03-31T23:00Z` (unfiltered hourly grid: `437` timestamps × `8` stations) |
| └─ Test valid reported targets | **2,727** | Station-hours with finite observed target `pm25` (`reported=True`) |
| └─ Test missing target slots | **769** | Unreported station-hours (`22.0%` natural test missingness, `reported=False`) |

**Arithmetic Balance:** $8{,}884 + 2{,}937 + 2{,}727 + 8 = 14{,}556$ exact DB observations.  
**Content Hash:** `33805c42097fc86e` (via `compute_dataset_content_hash`).

---

## 2. Pilot Monitoring Stations (`stations` table)

| `id` | `provider_location_id` | Station Name | Latitude | Longitude | UK Region | Raw Valid Obs |
|---:|---:|---|---:|---:|---|---:|
| 1 | 141 | London Harlington - UKA00472 | 51.4888 | -0.4416 | Greater London | 1,552 |
| 2 | 153 | London Hillingdon - UKA00266 | 51.4963 | -0.4609 | Greater London | 1,902 |
| 3 | 154 | Westminster - Marylebone Road | 51.5225 | -0.1546 | Central London | 1,417 |
| 4 | 157 | Bexley - Slade Green | 51.4660 | 0.1849 | South East London | 2,146 |
| 5 | 698 | Southampton Centre - UKA00235 | 50.9081 | -1.3958 | South East England | 1,876 |
| 6 | 804 | Wicken Fen - UKA00362 | 52.2985 | 0.2909 | East of England | 1,867 |
| 7 | 1795 | St Osyth - UKA00445 | 51.7780 | 1.0490 | East of England | 1,925 |
| 8 | 2312 | Manchester Piccadilly - UKA00248 | 53.4815 | -2.2379 | North West England | 1,871 |

---

## 3. SQLAlchemy ORM Tables (`src/sensorguard/db/schema.py`)

### `stations`
`id`, `provider`, `provider_location_id`, `name`, `latitude`, `longitude`, `country`, `timezone_name`, `metadata_json`, `created_at`, `updated_at` (`uq_station_provider_location`).

### `sensors`
`id`, `station_id`, `provider_sensor_id`, `parameter` (`pm25`), `canonical_unit` (`µg/m³`), `averaging_period_hours` (`1.0`), `metadata_json`, `created_at`.

### `raw_ingest_batches`
`id`, `provider`, `endpoint`, `params_json`, `fetched_at`, `persisted_at`, `status_code`, `response_hash` (SHA-256), `payload_json`, `raw_bytes`, `record_count`.

### `observations`
`id`, `sensor_id`, `raw_batch_id`, `observed_at`, `period_start`, `period_end`, `received_at`, `fetched_at`, `persisted_at`, `value` (`µg/m³`), `unit`, `quality_flag` (`VALID`, `INVALID`, `FUTURE_TIMESTAMP_INCONSISTENCY`), `source_status` (`HEALTHY`, `LATE`, `NO_REPORT`, `OFFLINE_CONFIRMED`), `version` (`uq_sensor_observed_version`).

### `forecast_feature_snapshots`
`id`, `sensor_id`, `decision_cutoff`, `target_time`, `features_json`, `feature_hash`, `created_at`.

---

## 4. Causal Model Feature Vector (`DatasetSplits.feature_columns`)

| Feature | Exact Definition (`build_spatiotemporal_features`) |
|---|---|
| `last_pm25` | `visible_series.ffill().shift(1)` ($\mu\text{g/m}^3$) |
| `lag_1h`, `lag_2h`, `lag_24h` | `ffill()` shifted by `2`, `3`, and `24` grid hours |
| `rolling_mean_6h`, `rolling_std_6h` | 6-hour trailing mean and std of `last_pm25` |
| `rolling_mean_24h` | 24-hour trailing mean of `last_pm25` |
| `neighbor_idw_pm25` | Inverse-distance ($1/d^2$, min $d=0.5$ km) mean of other stations with `age_hours <= 3.0`, falling back to `last_pm25` |
| `age_hours` | `(timestamp - last_visible_timestamp) / 3600` (`999.0` on uninitialized scenario cold starts) |
| `hour_sin`, `hour_cos`, `dow_sin`, `dow_cos` | Circular UTC hour & day-of-week harmonics |
