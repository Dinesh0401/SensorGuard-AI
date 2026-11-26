# SensorGuard AI — Live Pilot Activation Approval Gate & Two-Stage Authorization (Phase 8.7)

**Current Operational Status:** **LOCKED / FAIL-CLOSED PENDING OPERATOR AUTHORIZATION**  
**Stage 1 Readiness (One-Time Pre-Flight Station Availability Check, Max 8–16 HTTP Calls):** **READY FOR EXPLICIT APPROVAL**  
**Stage 2 Readiness (Recurring Shadow-Live Polling):** **READY FOR EXPLICIT SEPARATE APPROVAL ONLY AFTER STAGE 1 FEED VERIFICATION & OPERATOR SIGN-OFF**  
**Public Production / Adaptive SAGE Promotion:** **NO-GO (Static `SAGEForecaster` only)**

---

## 1. Two Separate Operational Authorization Decisions

1. **Decision A — Limited One-Time Pre-Flight Station Availability Check (`<= 16` HTTP requests total):**
   - Purpose: Probe `/v3/locations/{id}` once across the 8 UK stations below to confirm which sensors currently return active 2026 hourly measurements after provisioning `OPENAQ_API_KEY` server-side.
   - Prerequisite controls verified: `OpenAQClient` per-attempt quota reservation (`max_requests_per_day <= 48`), single-leader lease, kill switch, and JSON-only shadow artifact verification are implemented and tested.
2. **Decision B — Recurring Shadow-Live Polling (`shadow_live` mode):**
   - Requires Stage 1 results first so `BoundedPilotConfig.allowed_location_ids` is restricted strictly to verified active sensors (e.g., LAQN pair `(154, 157)` unless EEA replacements are confirmed).
   - Requires explicit operator unlock in `PilotControlState` (`kill_switch_active=False`, `opt_in_enabled=True`, non-null `authorized_at`, non-default `updated_by`) alongside `SENSORGUARD_POLLING_KILL_SWITCH=false`.

---

## 2. Target Station & PM2.5 Sensor Inventory (Offline `2026-11-22` Snapshot)

All entries below reflect archived DB records with **zero external OpenAQ HTTP calls executed**:

| Priority | Station Name | OpenAQ Loc ID | OpenAQ PM2.5 Sensor ID | Upstream Feed | License | Last Stored Meta UTC | Pre-Flight Action |
|---|---|---|---|---|---|---|---:|
| **Tier 1** | Westminster - Marylebone Road | `154` | `246` | London Air Quality Network | `UK OGL` | `2026-11-22T11:00Z` | **VERIFY LIVE IN STAGE 1** |
| **Tier 1** | Bexley - Slade Green | `157` | `254` | London Air Quality Network | `UK OGL` | `2026-11-22T11:00Z` | **VERIFY LIVE IN STAGE 1** |
| **Tier 2** | London Harlington - UKA00472 | `141` | `261` | EEA | `ODC-BY` | `2026-05-26T13:00Z` | **STALE AT SNAPSHOT — PROBE ONCE** |
| **Tier 2** | London Hillingdon - UKA00266 | `153` | `1927060` | EEA | `ODC-BY` | `2026-05-26T13:00Z` | **STALE AT SNAPSHOT — PROBE ONCE** |
| **Tier 2** | Southampton Centre - UKA00235 | `698` | `1213` | EEA | `ODC-BY` | `2026-05-26T13:00Z` | **STALE AT SNAPSHOT — PROBE ONCE** |
| **Tier 2** | Wicken Fen - UKA00362 | `804` | `1999688` | EEA | `ODC-BY` | `2026-05-26T13:00Z` | **STALE AT SNAPSHOT — PROBE ONCE** |
| **Tier 2** | St Osyth - UKA00445 | `1795` | `1732639` | EEA | `ODC-BY` | `2026-05-26T12:00Z` | **STALE AT SNAPSHOT — PROBE ONCE** |
| **Tier 2** | Manchester Piccadilly - UKA00248 | `2312` | `5269` | EEA | `ODC-BY` | `2026-05-26T13:00Z` | **STALE AT SNAPSHOT — PROBE ONCE** |

---

## 3. Pre-Activation Operator Checklist

1. **Environment, Database & Secrets Lock:**
   - Provision `OPENAQ_API_KEY`, `ADMIN_API_KEY`, and PostgreSQL `DATABASE_URL` in server-side `.env` (git-ignored; never exposed in `apps/web`).
   - Keep `SENSORGUARD_ALLOW_IN_MEMORY_RATE_LIMIT_DEV=false` (fail closed HTTP `503` on durable rate-limiter failure).
   - Confirm `alembic upgrade head` reports `008_m87_artifact_timing_gate (head)`.
2. **Stage 1 Only — Authorize One-Time Pre-Flight Availability Probe (`<= 16` HTTP Calls):**
   - Explicitly approve running `/v3/locations/{id}` once across the 8 station IDs and confirm active 2026 PM2.5 sensors + OpenAQ license attribution.
3. **Stage 2 Only — Authorize Recurring Bounded Shadow Polling (`<= 48` HTTP Attempts/UTC Day):**
   - Restrict `allowed_location_ids` strictly to live active sensors verified in Stage 1 (`(154, 157)` every **2h** -> `24` best-case / `48` worst-case HTTP attempts/day under `max_retries_per_request=1`).
   - Run `fit_shadow_suite()` into an immutable artifact directory (`SENSORGUARD_SHADOW_ARTIFACT_DIR`) ensuring SHA-256 verified JSON artifacts (`sensorguard.shadow_artifact.v1`), then issue hourly forecasts via `generate_prospective_shadow_forecasts(..., min_lead_seconds=300.0)` strictly `>= 5` minutes before `:00` target time.
4. **Minimum Prospective Shadow Duration & Usability Gate Before Promotion:**
   - Continuous **>= 7 calendar days** (`>= 168` hours target horizon; hard floor `min_unique_hours >= 24`, `min_matched_outcomes >= 100`, `min_lead_seconds >= 300`, `backdated_violation_count == 0`, `lead_time_violation_count == 0`, `artifact_violation_count == 0`).
   - Complete human practitioner study ([REAL_USER_USABILITY_STUDY.md](REAL_USER_USABILITY_STUDY.md); `0` participants currently recruited).

---

## 4. Emergency Halt & Rollback

- **Instant Halt:** Set `SENSORGUARD_POLLING_KILL_SWITCH=true` and restart worker -> returns `SKIPPED_KILL_SWITCH`.
- **Durable Halt:** Execute `set_polling_kill_switch(session, kill_switch_active=True, opt_in_enabled=False, reason=..., updated_by=operator)`.
- **Schema Rollback:** `alembic downgrade 007_m86_safety_fencing` (or `006_durable_pilot_control`); restore PostgreSQL snapshot via `pg_restore` if required; follow [MODEL_ROLLBACK.md](MODEL_ROLLBACK.md) for serving rollback.

---

## 5. Formal Operator Sign-Off (Pending)

- **Stage 1 Approval (One-Time Pre-Flight Check `<= 16` HTTP requests):** `NOT YET APPROVED`
- **Stage 2 Approval (Recurring `shadow_live` Polling `<= 48` attempts/UTC day):** `NOT YET APPROVED`
- **Approved `allowed_location_ids`:** `[PENDING STAGE 1 RESULT]`
- **Approved By / UTC Timestamp:** `___________________________`
