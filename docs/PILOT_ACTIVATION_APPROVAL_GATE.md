# SensorGuard AI — Live Pilot Activation Approval Gate & Two-Stage Authorization

**Current Operational Status:** **LOCKED / FAIL-CLOSED PENDING EXPLICIT OPERATOR APPROVAL**  
**Stage 1 Readiness (One-Time Pre-Flight Station Availability Check, Max 16 HTTP Attempts):** **READY FOR EXPLICIT STAGE 1 AUTHORIZATION**  
**Stage 2 Readiness (Recurring Shadow-Live Polling):** **NO-GO / HELD PENDING STAGE 1 RESULTS & SEPARATE APPROVAL**  
**Public Production / Adaptive SAGE Promotion:** **NO-GO (Static `SAGEForecaster` only)**

---

## 1. Stage 1 Command, Limits & Credentials (`authorize-stage1` & `preflight-stations`)

The dedicated Stage 1 CLI workflow ([`src/sensorguard/cli.py`](../src/sensorguard/cli.py), [`src/sensorguard/preflight.py`](../src/sensorguard/preflight.py)) defaults strictly to **dry-run (0 network requests)** and enforces a **genuinely single-use DB approval token** in `stage1_preflight_authorizations` (`AUTHORIZED -> CLAIMED_IN_PROGRESS -> COMPLETED | COMPLETED_WITH_ERRORS | QUOTA_EXHAUSTED | FAILED_INCOMPLETE`). Environment flags alone can no longer execute Stage 1 probes, repeated runs reusing an approval or restarting the CLI are rejected (`BLOCKED_APPROVAL_ALREADY_USED` / `BLOCKED_NO_SINGLE_USE_APPROVAL`), and `PilotControlState(control_name="openaq_live_polling").opt_in_enabled` stays strictly `False`.

### Exact Invocation Commands

```bash
# 1. Default Safe Dry-Run (0 external HTTP calls, lists 8 archived UK locations & attribution)
python -m sensorguard.cli preflight-stations

# 2. Mint Single-Use DB Approval Token (0 external HTTP calls, does not enable Stage 2 polling)
DATABASE_URL="postgresql+psycopg2://..." \
python -m sensorguard.cli authorize-stage1 \
  --authorized-by "<operator-name>" \
  --reason "Single-use Stage 1 check for archived UK locations" \
  --max-attempts 16 \
  --confirm-single-use

# 3. One-Time Bounded Stage 1 Execution using the minted <approval_id>
OPENAQ_API_KEY="<server-side-key>" \
DATABASE_URL="postgresql+psycopg2://..." \
SENSORGUARD_POLLING_KILL_SWITCH="false" \
python -m sensorguard.cli preflight-stations \
  --execute-once \
  --approval-id "<approval_id>" \
  --max-attempts 16 \
  --authorized-by "<operator-name>" \
  --output artifacts/preflight/stage1_result.json

# 4. Optional Audited Recovery (only if an execution ended FAILED_INCOMPLETE / CLAIMED_IN_PROGRESS / QUOTA_EXHAUSTED)
# Supersedes prior row to SUPERSEDED_RECOVERED and carries forward only remaining attempt budget unless --allow-budget-reset is passed.
DATABASE_URL="postgresql+psycopg2://..." \
python -m sensorguard.cli authorize-stage1 \
  --recover-from-approval-id "<prior_approval_id>" \
  --authorized-by "<operator-name>" \
  --reason "Audited recovery after operator review of incomplete probe" \
  --confirm-single-use
```

### Hard Safety Limits & Expected Request Count

- **Atomic Single-Use DB Claim:** `_claim_single_use_approval()` runs an atomic conditional `UPDATE stage1_preflight_authorizations SET status='CLAIMED_IN_PROGRESS', execution_id=... WHERE approval_id=... AND status='AUTHORIZED'`. Concurrent workers racing the same token see at most 1 claim succeed; losers fail closed with `0` HTTP calls. Any exception transitions the claim to `FAILED_INCOMPLETE`; subsequent invocations or CLI restarts cannot reuse the token or reset its `<= 16` request budget.
- **Hard Global Cap (`--max-attempts 16`):** Enforced atomically in DB against BOTH `stage1_preflight_authorizations.attempts_consumed` AND `pilot_quota_states` (`DurablePilotQuotaTracker`) before every outbound HTTP socket opens, across all retries, 429s, 5xx responses, timeouts, and network errors (`> 16` fails validation immediately).
- **Expected Request Count:** **`8`–`16` HTTP calls total**:
  - Phase 1: `8` `/v3/locations/{id}` metadata calls (`154, 157, 141, 153, 698, 804, 1795, 2312`).
  - Phase 2: Up to `8` `/v3/sensors/{id}/hours?limit=5` recent-measurement probes using only remaining budget (`<= 16` minus Phase 1 attempts/retries).
- **Fail-Closed DB & Secrets Hygiene:** If `pilot_quota_states` or `stage1_preflight_authorizations` is inaccessible, execution aborts fail-closed (`BLOCKED_DB_UNAVAILABLE`, `0` HTTP calls). Credentials (`OPENAQ_API_KEY`, `DATABASE_URL`) are loaded server-side only (git-ignored `.env`) and redacted from stdout, logs, DB audit rows, and JSON artifacts (`artifacts/preflight/*` is git-ignored via `artifacts/*`).

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

- **Pre-Flight / Instant Environment Halt:** Set `SENSORGUARD_POLLING_KILL_SWITCH=true` (blocks both Stage 1 `preflight-stations` -> `BLOCKED_KILL_SWITCH` with zero claim consumption and any recurring poller -> `SKIPPED_KILL_SWITCH` with `0` HTTP calls).
- **Stage 1 DB Halt / Cleanup:** Remove unused approvals or set `PilotControlState(control_name="openaq_stage1_preflight", kill_switch_active=True, opt_in_enabled=False)`. Consumed or incomplete `stage1_preflight_authorizations` rows remain permanently blocked from reuse unless explicitly superseded by `authorize-stage1 --recover-from-approval-id ...`. Recurring `openaq_live_polling` remains untouched and locked (`opt_in_enabled=False`).
- **Stage 2 Rollback & Schema:** `alembic downgrade 009_m87_commit_timing` (current head `010_single_use_stage1_proof`); restore PostgreSQL snapshot via `pg_restore` if needed; follow [MODEL_ROLLBACK.md](MODEL_ROLLBACK.md) for serving artifact rollback.

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
