# SensorGuard AI — Architecture & Ingestion Design

## 1. System Overview

SensorGuard AI investigates whether forecast calibration can be preserved when environmental sensor networks experience nonrandom outages and delayed upstream reporting.

```mermaid
flowchart TD
    OpenAQ["OpenAQ REST API v3\n(/v3/locations, /v3/sensors/{id}/hours)"] -->|X-API-Key, Rate Limiter, Bounded Retries| IngestWorker["Ingestion Pipeline & Validator"]
    IngestWorker -->|Raw Payload + SHA-256 Hash| RawBatches[("raw_ingest_batches\n(Immutable Raw Provenance)")]
    IngestWorker -->|Append-Only Revisions| ObsDB[("observations\n(Versioned, Validated)")]
    ObsDB --> ReplayEngine["Point-in-Time Replay Engine"]
    ReplayEngine -->|Prospective: fetched_at <= cutoff AND observed_at <= cutoff| FeaturesProd["Point-in-Time Features\n(Production Serving)"]
    ReplayEngine -->|Retrospective: observed_at <= cutoff - delay| FeaturesResearch["Simulated Replay Features\n(Offline Backtesting)"]
    FeaturesProd --> Model["Forecaster + Conformal Guard Policy"]
    FeaturesResearch --> Model
```

---

## 2. Ingestion & Provenance Architecture (Hardened)

### 2.1 Provider Client (`sensorguard.providers.OpenAQClient`)
- **Authentication**: Strict server-side `X-API-Key` header injection. Keys are never logged or exposed in client responses.
- **Rate Control**: Token bucket rate limiter (`RateLimiter`) enforcing bounded query rates.
- **Resilience**: Bounded exponential backoff with jitter on HTTP `429` (Too Many Requests) and `5xx` server errors. Respects upstream `Retry-After` header.
- **Response Timing**: Records `fetched_at` immediately upon complete receipt of the HTTP response bytes over the wire, distinguishing network retrieval time from database transaction commit time.
- **Cryptographic Provenance**: Calculates deterministic SHA-256 hashes of raw HTTP response payloads and saves the verbatim text in `raw_ingest_batches.payload_json`.

### 2.2 Strict Timestamp Semantics
To prevent temporal contamination and future leakage, three distinct timestamps are tracked for every observation:
1. `observed_at`: The physical observation timestamp. In accordance with OpenAQ v3 hourly aggregate conventions, this corresponds to the **end of the measurement interval** (`period.datetimeTo` / `datetime.utc`).
2. `received_at`: The upstream provider publication/update timestamp. This is preserved **only if explicitly reported by upstream metadata**. If unavailable, it is stored as `NULL` (`None`). It is **never** inferred from `observed_at` or local system time.
3. `fetched_at`: The exact UTC timestamp when the SensorGuard worker fetched the HTTP response.
4. **Consistency Invariant**: Observations with `observed_at > fetched_at` (future physical timestamps) are rejected as `FUTURE_TIMESTAMP_INCONSISTENCY` and flagged `INVALID`.

### 2.3 Append-Only Versioning for Revised Measurements
Rather than discarding later updates, the observation store is append-only and version-aware:
- The database enforces `UniqueConstraint("sensor_id", "observed_at", "version")`.
- If an updated measurement arrives with a corrected value or quality flag, it is inserted with `version = latest_version + 1`.
- Identical re-fetches are safely deduplicated without creating extraneous versions.

### 2.4 Geographic Coordinate Integrity
- Station ingestion rejects missing, NaN, out-of-range (`[-90, 90]`, `[-180, 180]`), or `(0.0, 0.0)` null-island coordinates, preventing spatial distortion in neighborhood modeling.

---

## 3. Point-in-Time Historical Replay (`sensorguard.replay.PointInTimeReplayEngine`)

The replay engine guarantees zero future leakage under two distinct modes:

| Mode | Filter Condition | Use Case | Guarantees |
| :--- | :--- | :--- | :--- |
| **Prospective Replay** | `Observation.fetched_at <= cutoff` AND `Observation.observed_at <= cutoff` (with `observed_at <= fetched_at`) | Production backtesting & prospective auditing | Guarantees zero retroactive backfill leakage. Backfilled data fetched today cannot leak into historical predictions. Returns the latest version available as of the cutoff. |
| **Retrospective Replay** | `Observation.observed_at <= (decision_cutoff - delay_hours)` | Offline research backtesting where historical `fetched_at` was unrecorded | Explicitly flags outputs as `simulated_availability=True` with declared `assumed_delay_hours`. Never misrepresents simulated data as prospective truth. |

### 3.1 Continuous Timeline Reindexing (`build_replay_timeline`)
Fills gaps along expected hourly grids:
- `HEALTHY`: On-time, valid measurement.
- `NO_REPORT`: Unobserved hourly slot explicitly emitted to prevent conflating missing reports with physical hardware failures.
- `LATE`: Arrival delay exceeded 3 hours from interval end.
- `INVALID`: Out-of-bounds or corrupt measurement.

---

## 4. Storage & Relational Data Model

Supported storage backends:
- **Local Development / Testing**: SQLite (`sqlite:///data/sensorguard.db` or `:memory:`)
- **Production MLOps**: PostgreSQL (`postgresql://...` via `DATABASE_URL`)

### Relational Tables:
- `stations`: Physical monitoring location coordinates, country, timezone, and metadata.
- `sensors`: Individual instruments (e.g., PM2.5, units, averaging periods).
- `raw_ingest_batches`: Raw HTTP payload snapshots, response status, endpoint, parameters, and SHA-256 hashes.
- `observations`: Validated, normalized measurements with explicit versioning, timestamp lineage, and quality flags (`HEALTHY`, `NO_REPORT`, `LATE`, `INVALID`).

Schema versioning is managed via **Alembic** migrations:
- `001_initial_schema`: Initial relational tables.
- `002_version_aware_observations`: Added `version` column and `(sensor_id, observed_at, version)` unique constraint.
