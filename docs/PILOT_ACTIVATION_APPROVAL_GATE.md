# SensorGuard AI — Live Pilot Activation Approval Gate & Two-Stage Authorization

**Current Operational Status:** **LOCKED / FAIL-CLOSED PENDING EXPLICIT OPERATOR APPROVAL**  
**Stage 1 Readiness (One-Time Pre-Flight Station Availability Check, Max 16 HTTP Attempts):** **READY FOR EXPLICIT STAGE 1 AUTHORIZATION**  
**Stage 2 Readiness (Recurring Shadow-Live Polling):** **NO-GO / HELD PENDING STAGE 1 RESULTS & SEPARATE APPROVAL**  
**Public Production / Adaptive SAGE Promotion:** **NO-GO (Static `SAGEForecaster` only)**

---

## 1. Stage 1 Command, Limits & Credentials (`preflight-stations`)

The dedicated Stage 1 CLI command ([`src/sensorguard/cli.py`](../src/sensorguard/cli.py), [`src/sensorguard/preflight.py`](../src/sensorguard/preflight.py)) defaults strictly to **dry-run (0 network requests)** unless `--execute-once` AND explicit Stage 1 operator approval are provided. It **never** sets `PilotControlState(control_name="openaq_live_polling").opt_in_enabled = True` for recurring polling.

### Exact Invocation Commands

```bash
# 1. Default Safe Dry-Run (0 external HTTP calls, lists 8 archived UK locations & attribution)
python -m sensorguard.cli preflight-stations

# 2. One-Time Bounded Stage 1 Check (requires explicit approval + server-side secrets)
OPENAQ_API_KEY="<server-side-key>" \
DATABASE_URL="postgresql+psycopg2://..." \
SENSORGUARD_POLLING_KILL_SWITCH="false" \
SENSORGUARD_STAGE1_PREFLIGHT_APPROVED="true" \
python -m sensorguard.cli preflight-stations \
  --execute-once \
  --max-attempts 16 \
  --authorized-by "<operator-name>" \
  --output artifacts/preflight/stage1_result.json
```

### Hard Safety Limits & Expected Request Count

- **Hard Global Cap (`--max-attempts 16`):** Enforced pre-request in `_stage1_attempt_guard` across all retries, 429s, 5xx responses, timeouts, and network errors (`> 16` fails validation immediately).
- **Expected Request Count:** **`8`–`16` HTTP calls total**:
  - Phase 1: `8` `/v3/locations/{id}` metadata calls (`154, 157, 141, 153, 698, 804, 1795, 2312`).
  - Phase 2: Up to `8` `/v3/sensors/{id}/hours?limit=5` recent-measurement probes using only remaining budget (`<= 16` minus Phase 1 attempts/retries).
- **Durable DB Protection:** Every outbound attempt commits `1` unit into `pilot_quota_states` via `DurablePilotQuotaTracker` before the socket opens. If the durable database is unavailable, execution aborts fail-closed (`BLOCKED_DB_UNAVAILABLE`, `0` network calls).
- **Secrets Hygiene:** Credentials (`OPENAQ_API_KEY`, `DATABASE_URL`) are loaded server-side only (git-ignored `.env`) and redacted from stdout, logs, DB audit rows, and JSON artifacts (`artifacts/preflight/*` is git-ignored via `artifacts/*`).

---

## 2. Target Station & PM2.5 Sensor Inventory (Offline `2026-11-22` Snapshot)

All entries below reflect archived DB records with **zero external OpenAQ HTTP calls executed**:

| Priority | Station Name | OpenAQ Loc ID | OpenAQ PM2.5 Sensor ID | Upstream Feed | License | Last Stored Meta UTC | Stage 1 Pre-Flight Action |
|---|---|---|---|---|---|---|---:|
| **Tier 1** | Westminster - Marylebone Road | `154` | `246` | London Air Quality Network | `UK OGL` | `2026-11-22T11:00Z` | **VERIFY LIVE ONCE** |
| **Tier 1** | Bexley - Slade Green | `157` | `254` | London Air Quality Network | `UK OGL` | `2026-11-22T11:00Z` | **VERIFY LIVE ONCE** |
| **Tier 2** | London Harlington - UKA00472 | `141` | `261` | EEA | `ODC-BY` | `2026-05-26T13:00Z` | **STALE AT SNAPSHOT — PROBE ONCE** |
| **Tier 2** | London Hillingdon - UKA00266 | `153` | `1927060` | EEA | `ODC-BY` | `2026-05-26T13:00Z` | **STALE AT SNAPSHOT — PROBE ONCE** |
| **Tier 2** | Southampton Centre - UKA00235 | `698` | `1213` | EEA | `ODC-BY` | `2026-05-26T13:00Z` | **STALE AT SNAPSHOT — PROBE ONCE** |
| **Tier 2** | Wicken Fen - UKA00362 | `804` | `1999688` | EEA | `ODC-BY` | `2026-05-26T13:00Z` | **STALE AT SNAPSHOT — PROBE ONCE** |
| **Tier 2** | St Osyth - UKA00445 | `1795` | `1732639` | EEA | `ODC-BY` | `2026-05-26T12:00Z` | **STALE AT SNAPSHOT — PROBE ONCE** |
| **Tier 2** | Manchester Piccadilly - UKA00248 | `2312` | `5269` | EEA | `ODC-BY` | `2026-05-26T13:00Z` | **STALE AT SNAPSHOT — PROBE ONCE** |

---

## 3. Emergency Halt & Rollback Behavior

- **Pre-Flight / Instant Environment Halt:** Set `SENSORGUARD_POLLING_KILL_SWITCH=true` (blocks both Stage 1 `preflight-stations` -> `BLOCKED_KILL_SWITCH` and any recurring poller -> `SKIPPED_KILL_SWITCH` with `0` HTTP calls).
- **Stage 1 DB Halt / Cleanup:** Remove env approval flags and optionally set `PilotControlState(control_name="openaq_stage1_preflight", kill_switch_active=True, opt_in_enabled=False)`. Recurring `openaq_live_polling` remains untouched and locked (`opt_in_enabled=False`).
- **Stage 2 Rollback & Schema:** `alembic downgrade 008_m87_artifact_timing_gate` (current head `009_m87_commit_timing`); restore PostgreSQL snapshot via `pg_restore` if needed; follow [MODEL_ROLLBACK.md](MODEL_ROLLBACK.md) for serving artifact rollback.

---

## 4. Stage 2 Hold Criteria (Not Approved Today)

Do **not** authorize Stage 2 recurring polling until:
1. Stage 1 confirms live active feeds and restricts `BoundedPilotConfig.allowed_location_ids` solely to active sensors.
2. `SENSORGUARD_SHADOW_ARTIFACT_DIR` is provisioned with verified `.json` shadow artifacts (`sensorguard.shadow_artifact.v1`) and prospective forecasts enforce post-commit completion proof (`commit_completed_at`, `min_lead_seconds >= 300s`, `commit_safety_margin_seconds >= 15s`).
3. Minimum prospective duration (`>= 7` days / `>= 168h`; hard floor `min_unique_hours >= 24`, `min_matched_outcomes >= 100`) and practitioner usability study ([REAL_USER_USABILITY_STUDY.md](REAL_USER_USABILITY_STUDY.md)) pass before any model promotion.

---

## 5. Formal Operator Sign-Off (Pending)

- **Stage 1 Approval Wording (to authorize once):**  
  `Approve one-time Stage 1 OpenAQ station availability pre-flight check (<= 16 HTTP attempts total, no recurring polling, no opt_in_enabled change) across locations (141, 153, 154, 157, 698, 804, 1795, 2312).`
- **Stage 1 Status:** `NOT YET AUTHORIZED`
- **Stage 2 Recurring Polling Status:** `NO-GO (HELD)`
- **Operator Identity / UTC Timestamp:** `___________________________`
