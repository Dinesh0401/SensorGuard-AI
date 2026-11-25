# SensorGuard AI — Milestone 8 Pilot Readiness & Governance Report

**Milestone:** 8.0–8.5 — Monitored Real-World Pilot & User Validation (Offline Preparation Mode)  
**Date:** 2026-11-25  
**Branch:** `feat/milestone-3-forecasting-uncertainty`  
**Public Production Launch Decision:** **NO-GO**  
**Internal Shadow-Live Pilot Software Status:** **READY IN FAIL-CLOSED LOCKED STATE (Awaiting Explicit Operator Authorization)**

---

## 1. Executive Summary

Milestone 8 prepares SensorGuard AI cleanly for controlled, bounded, opt-in live-data shadow evaluation and structured user validation while strictly adhering to offline-preparation guardrails:
- **Zero unapproved external polling:** No external OpenAQ API requests were executed in Milestone 8. Both database-level (`PilotControlState.kill_switch_active = True`) and config-level (`opt_in_enabled = False`) gates remain locked by default.
- **Honest model serving separation:** Primary API serving explicitly reports `SAGEForecaster` (`sage_pilot_q1_2024 v1.0.0`, Static SAGE), while `AdaptiveSAGEForecaster` (`v2.0.0-shadow-m77`) remains unpromoted shadow-only research ([`MODEL_SERVING_VERSION_AUDIT.md`](MODEL_SERVING_VERSION_AUDIT.md)).
- **Audited historical station availability:** Offline inspection of stored station metadata from the `2026-11-22` fixture snapshot reveals that **only 2 of 8 pilot stations** had fresh upstream timestamps at snapshot time, proving why live pre-flight verification cannot be assumed from Q1 2024 historical completeness.

---

## 2. Four Explicit Data Operating Modes

Every API health payload (`/health`), model metadata response (`/api/v1/model/status`), pilot readiness endpoint (`/api/v1/pilot/readiness`), PostgreSQL `forecast_runs.data_mode` column, and dashboard banner (`apps/web/app/page.tsx`) explicitly distinguishes:

| Mode | Identifier | Description | Permitted Claims & Guardrails |
|---|---|---|---|
| **1. Archived Historical** | `archived_historical` | Verified DEFRA UK AURN Q1 2024 dataset (`14,556` obs, `2024-01-01` – `2024-03-31`). | Point-in-time historical evaluation and private demo fixtures only; never labeled as live readings. |
| **2. Simulated Outage** | `simulated_outage` | Controlled synthetic masking/censoring (`mcar_20`, `mnar_gamma_1.5`, `burst_24h`, `extended_72h`). | Stress-tests algorithm sensitivity and missing-data policies; never equates synthetic censoring to real MNAR labels. |
| **3. Shadow Live** | `shadow_live` | Prospective forecasting against bounded, delayed, opt-in upstream feeds once unlocked. | Forecasts stored *before* target arrival; coverage evaluated strictly after ground-truth arrival (`>= 100` matched minimum). |
| **4. Validated Live** | `validated_live` | Disabled placeholder pending completed prospective shadow validation and operator authorization. | Cannot be entered until `shadow_live` calibration criteria and user study gates are verified and signed off. |

---

## 3. Offline Audit of the 8 Pilot UK Stations (Licenses & Upstream Snapshot Recency)

Generated offline from stored `stations.metadata_json` (`updated_at = 2026-11-22`) via [`inspect_station_provider_metadata`](../src/sensorguard/pilot_monitor.py) with **zero external HTTP requests**:

| DB ID | OpenAQ Loc ID | Station Name | PM2.5 Sensor ID (DB / OpenAQ) | Upstream Provider | License | First Metadata UTC | Last Metadata UTC (at `2026-11-22` Snapshot) | Active at `2026-11-22` Snapshot? | Current Live Status |
|---:|---:|---|---:|---|---|---|---|---|---|
| 1 | `141` | London Harlington - UKA00472 | `4` / `261` | EEA | `ODC-BY` | `2016-01-30T01:00Z` | `2026-05-26T13:00Z` | No (`4319.88h` old) | `UNVERIFIED_OFFLINE_MODE` |
| 2 | `153` | London Hillingdon - UKA00266 | `8` / `1927060` | EEA | `ODC-BY` | `2016-01-30T01:00Z` | `2026-05-26T13:00Z` | No (`4319.88h` old) | `UNVERIFIED_OFFLINE_MODE` |
| 3 | `154` | Westminster - Marylebone Road | `13` / `246` | London Air Quality Network | `UK OGL` | `2016-01-30T01:00Z` | **`2026-11-22T11:00Z`** | **Yes (`1.88h` old)** | `UNVERIFIED_OFFLINE_MODE` |
| 4 | `157` | Bexley - Slade Green | `18` / `254` | London Air Quality Network | `UK OGL` | `2016-01-30T01:00Z` | **`2026-11-22T11:00Z`** | **Yes (`1.89h` old)** | `UNVERIFIED_OFFLINE_MODE` |
| 5 | `698` | Southampton Centre - UKA00235 | `23` / `1213` | EEA | `ODC-BY` | `2016-02-27T21:00Z` | `2026-05-26T13:00Z` | No (`4319.89h` old) | `UNVERIFIED_OFFLINE_MODE` |
| 6 | `804` | Wicken Fen - UKA00362 | `28` / `1999688` | EEA | `ODC-BY` | `2016-02-27T21:00Z` | `2026-05-26T13:00Z` | No (`4319.89h` old) | `UNVERIFIED_OFFLINE_MODE` |
| 7 | `1795` | St Osyth - UKA00445 | `33` / `1732639` | EEA | `ODC-BY` | `2016-02-27T21:00Z` | `2026-05-26T12:00Z` | No (`4320.90h` old) | `UNVERIFIED_OFFLINE_MODE` |
| 8 | `2312` | Manchester Piccadilly - UKA00248 | `37` / `5269` | EEA | `ODC-BY` | `2016-02-27T21:00Z` | `2026-05-26T13:00Z` | No (`4319.90h` old) | `UNVERIFIED_OFFLINE_MODE` |

**Critical Operational Implication:** Only the 2 LAQN feeds (`#154`, `#157`) reported recently on `2026-11-22`. The 6 EEA feeds had stopped updating via that OpenAQ sensor feed by `2026-05-26`. A one-time pre-flight check (upon explicit approval) is required to determine current active sensors or substitute active UK stations.

---

## 4. Durable Multi-Worker Polling Infrastructure (`006` Schema)

Implemented in [`alembic/versions/006_durable_pilot_control.py`](../alembic/versions/006_durable_pilot_control.py), [`src/sensorguard/db/schema.py`](../src/sensorguard/db/schema.py), and [`src/sensorguard/pilot_monitor.py`](../src/sensorguard/pilot_monitor.py):

1. **`pilot_quota_states` (`DurablePilotQuotaTracker`):** Stores UTC daily request budget per provider; uses atomic conditional `UPDATE ... WHERE requests_used + count <= effective_cap` with `synchronize_session=False` so concurrent workers and process restarts cannot overshoot `max_requests_per_day = 48`.
2. **`pilot_poll_leases` (`acquire_poll_lease`, `release_poll_lease`):** Single-leader distributed lock preventing duplicate overlapping worker polls, with deterministic TTL crash recovery (`recovered_from_expired_lease = True`).
3. **`pilot_control_states` (`ensure_default_control_state`, `set_polling_kill_switch`):** Database fail-closed kill switch (`kill_switch_active = True`, `opt_in_enabled = False` default), plus unconditional environment emergency override `SENSORGUARD_POLLING_KILL_SWITCH=true`.
4. **`pilot_ingest_audit_runs` (`PilotIngestAuditRun`):** Persists every poll decision (`SKIPPED_KILL_SWITCH`, `SKIPPED_OPT_OUT`, `SKIPPED_NO_CREDENTIALS`, `SKIPPED_NO_LEASE`, `HTTP_429_BACKOFF`, `QUOTA_EXHAUSTED`, `SUCCESS`, `FAILED`) with request counts, inserted/revised/deduped/invalid tallies, and error summaries.
5. **Bounded Historical Reconciliation & Revisions:** Uses sliding lookback windows (`lookback_hours = 48`, not `/latest`-only) so late upstream observations arrive cleanly and revised historical values append `version + 1` rows without mutating prior observations.
6. **Backward Compatibility for Un-Migrated SQLite Fixtures:** `/api/v1/pilot/readiness` degrades cleanly with fail-closed defaults if queried against local Alembic `004` SQLite fixture databases (`data/sensorguard.db`).

---

## 5. Companion Milestone 8 Deliverables

- [`MODEL_SERVING_VERSION_AUDIT.md`](MODEL_SERVING_VERSION_AUDIT.md) — Active primary vs shadow model governance
- [`SHADOW_FORECAST_EVALUATION_PROTOCOL.md`](SHADOW_FORECAST_EVALUATION_PROTOCOL.md) — Prospective shadow forecast & delayed-truth reconciliation protocol
- [`REAL_USER_USABILITY_STUDY.md`](REAL_USER_USABILITY_STUDY.md) — Consent-based user evaluation design (protocol only; zero fabricated feedback)
- [`LIVE_DATA_OPERATIONAL_RUNBOOK.md`](LIVE_DATA_OPERATIONAL_RUNBOOK.md) — Step-by-step live polling activation, quota management, kill-switch & rollback runbook

---

## 6. Explicit Pre-Live Authorization Gate

The following actions **require explicit human authorization** before execution:
1. Supplying a valid server-side `OPENAQ_API_KEY` in `.env` (git-ignored) and executing a bounded pre-flight check on `/v3/locations/{id}` to inspect current 2026 live availability across the 8 UK stations.
2. Unlocking `PilotControlState` (`kill_switch_active = False`, `opt_in_enabled = True`) for `shadow_live` polling under the 48 requests/day cap.
3. Recruiting 5–10 consented participants for the private-staging usability evaluation.
4. Any merge to `main`, external deployment, or public launch remains **NO-GO**.
