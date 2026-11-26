# SensorGuard AI — Live Pilot Activation Approval Gate & Operator Checklist (Phase 8.6)

**Decision Status Today:** **NO-GO FOR LIVE POLLING & PUBLIC PRODUCTION (LOCKED PENDING AUTHORIZATION)**  
**Prerequisite Pre-Flight Verification:** Offline `2026-11-22` snapshot confirmed only **2 of 8** feeds updating recently; all **8** require a live pre-flight verification after key provisioning.

---

## 1. Exact Target Station & PM2.5 Sensor Inventory Requiring Live Check

All values below come from stored database records (`stations` + `sensors`) with **zero external HTTP requests executed yet**:

| Priority | Station Name | OpenAQ Loc ID | OpenAQ PM2.5 Sensor ID | Upstream Feed | License | Last Stored Meta UTC | Live Status Today |
|---|---|---|---|---|---|---|---:|
| **Tier 1 (Expected Active)** | Westminster - Marylebone Road | `154` | `246` | London Air Quality Network | `UK OGL` | `2026-11-22T11:00Z` | **TO VERIFY LIVE** |
| **Tier 1 (Expected Active)** | Bexley - Slade Green | `157` | `254` | London Air Quality Network | `UK OGL` | `2026-11-22T11:00Z` | **TO VERIFY LIVE** |
| **Tier 2 (Aged Feed Check)** | London Harlington - UKA00472 | `141` | `261` | EEA | `ODC-BY` | `2026-05-26T13:00Z` | **STALE AT SNAPSHOT — VERIFY REPLACEMENT** |
| **Tier 2 (Aged Feed Check)** | London Hillingdon - UKA00266 | `153` | `1927060` | EEA | `ODC-BY` | `2026-05-26T13:00Z` | **STALE AT SNAPSHOT — VERIFY REPLACEMENT** |
| **Tier 2 (Aged Feed Check)** | Southampton Centre - UKA00235 | `698` | `1213` | EEA | `ODC-BY` | `2026-05-26T13:00Z` | **STALE AT SNAPSHOT — VERIFY REPLACEMENT** |
| **Tier 2 (Aged Feed Check)** | Wicken Fen - UKA00362 | `804` | `1999688` | EEA | `ODC-BY` | `2026-05-26T13:00Z` | **STALE AT SNAPSHOT — VERIFY REPLACEMENT** |
| **Tier 2 (Aged Feed Check)** | St Osyth - UKA00445 | `1795` | `1732639` | EEA | `ODC-BY` | `2026-05-26T12:00Z` | **STALE AT SNAPSHOT — VERIFY REPLACEMENT** |
| **Tier 2 (Aged Feed Check)** | Manchester Piccadilly - UKA00248 | `2312` | `5269` | EEA | `ODC-BY` | `2026-05-26T13:00Z` | **STALE AT SNAPSHOT — VERIFY REPLACEMENT** |

---

## 2. Pre-Activation Operator Checklist

Do not enable polling until every step is checked and signed off:

1. **Server-Side Secrets & Environment Lock:**
   - Provision `OPENAQ_API_KEY`, `ADMIN_API_KEY`, and PostgreSQL `DATABASE_URL` exclusively in `.env` (git-ignored). Never expose keys in `apps/web` or `NEXT_PUBLIC_*`.
   - Confirm `SENSORGUARD_POLLING_KILL_SWITCH` is `"true"` during setup and `alembic upgrade head` reports `007_m86_prospective_timestamps_and_fencing (head)`.
2. **Pre-Flight Availability Check (Max 8–16 HTTP Requests Total):**
   - Explicitly approve running a one-time `/v3/locations/{id}` probe across the 8 location IDs to confirm which sensors currently return 2026 hourly measurements.
   - Restrict `BoundedPilotConfig.allowed_location_ids` strictly to live active IDs (e.g., `(154, 157)` plus any verified replacement active UK DEFRA sensors).
3. **Quota & Retry Configuration:**
   - For `(154, 157)`: schedule every **2 hours** (`24` best-case / `48` worst-case HTTP attempts per day under `max_retries_per_request=1`).
   - For up to 8 active sensors: schedule every **8 hours** (`24` best-case / `48` worst-case HTTP attempts per day) so a 100% retry episode never exceeds `48/day`.
4. **Prospective Shadow Forecast Loop:**
   - Generate hourly shadow runs strictly BEFORE `:00` target hour (`generate_prospective_shadow_forecasts`) -> ingest truth post-target via `run_bounded_opt_in_poll` -> run `reconcile_shadow_evaluation()` asynchronously.
5. **Minimum Shadow Evaluation Duration & Thresholds Before Model Promotion or Live Labeling:**
   - **Minimum Duration:** Continuous **>= 7 calendar days** (`>= 168` hours target horizon; hard software floor `min_unique_hours >= 24`, `min_matched_outcomes >= 100`).
   - **Integrity Floor:** `backdated_violation_count == 0`, documented missing/delayed rates, reviewed `high_pollution` & `long_outage` subgroups, and completed [REAL_USER_USABILITY_STUDY.md](REAL_USER_USABILITY_STUDY.md) practitioner study (`>= 80%` comprehension on abstentions vs hardware failures).

---

## 3. Monitoring, Emergency Halt, Restart & Rollback Summary

- **Emergency Halt (Instant, Zero DB):** Export `SENSORGUARD_POLLING_KILL_SWITCH=true` and restart scheduler/worker -> all workers return `SKIPPED_KILL_SWITCH`.
- **Durable Multi-Worker Halt:** Execute `set_polling_kill_switch(session, kill_switch_active=True, opt_in_enabled=False, reason=..., updated_by=operator)`.
- **Restart & Lease Recovery:** Workers hold renewable fenced leases (`pilot_poll_leases.fencing_token`); stalled workers lose heartbeat renewal and are aborted cleanly (`SKIPPED_STALE_FENCING_TOKEN`) before commit.
- **Database Rollback:** `alembic downgrade 006_durable_pilot_control` (or `005_persistent_forecasts`); restore snapshot via `pg_restore` if needed; follow [MODEL_ROLLBACK.md](MODEL_ROLLBACK.md) for serving artifact rollback.

---

## 4. Formal Operator Authorization Sign-Off Block

- **Approved By (Name & Role):** `___________________________` (`NOT YET SIGNED`)
- **Approved `allowed_location_ids`:** `[PENDING PRE-FLIGHT RESULT]`
- **Approved Cadence & Daily Cap:** `<= 48 HTTP attempts/UTC day` (`max_retries_per_request = 1`)
- **Usability Study Authorization:** `PENDING (0 participants recruited)`
- **Date / UTC Timestamp:** `___________________________`
