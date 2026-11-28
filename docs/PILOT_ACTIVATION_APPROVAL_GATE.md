# SensorGuard AI — Live Pilot Activation Approval Gate & Two-Stage Authorization

**Current Operational Status:** **STAGE 1 EXECUTED ONCE (`1/8` ACTIVE); STAGE 2 RECURRING POLLING LOCKED (`opt_in_enabled = False`)**  
**Stage 1 Readiness (One-Time Pre-Flight Station Availability Check, Max 16 HTTP Attempts):** **COMPLETED ONCE (`2026-11-27T06:49:34Z`) — Token `s1_85f032d67045433f` Consumed (`16/16` requests)**  
**Stage 2 Readiness (Recurring Shadow-Live Polling):** **NO-GO / HELD PENDING SEPARATE STAGE 2 AUTHORIZATION & STATION SCOPE DECISION**  
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

## 2. Target Station Inventory & Verified Stage 1 Live Result (`2026-11-27T06:49:34Z`)

Archived evidence JSON: [`docs/audit/stage1_preflight_result.json`](audit/stage1_preflight_result.json) (`approval_id = s1_85f032d67045433f`, `execution_id = exec_d91ef7f6a583485a`, `attempts_consumed = 16 / 16`, `daily_quota_used_today = 16 / 48`, `recurring_polling_opt_in_enabled = false`).

| Station Name | OpenAQ Loc ID | OpenAQ PM2.5 Sensor ID | Upstream Feed | License | Stage 1 Metadata UTC | Stage 1 PM2.5 Hourly (48h) | Live Pre-Flight Status |
|---|---|---|---|---|---|---|---:|
| **Bexley - Slade Green** | `157` | `254` | London Air Quality Network | `UK OGL` | `2026-11-27T05:00:00Z` | **`2026-11-25T11:00:00+00:00`** | **`active`** |
| Westminster - Marylebone Road | `154` | `246` | London Air Quality Network | `UK OGL` | `2026-11-27T04:00:00Z` *(other pollutants)* | None within 48h | `inactive` |
| London Harlington - UKA00472 | `141` | `261` | EEA | `ODC-BY` | `2026-05-26T13:00:00Z` | None within 48h | `inactive` |
| London Hillingdon - UKA00266 | `153` | `1927060` | EEA | `ODC-BY` | `2026-05-26T13:00:00Z` | None within 48h | `inactive` |
| Southampton Centre - UKA00235 | `698` | `1213` | EEA | `ODC-BY` | `2026-05-26T13:00:00Z` | None within 48h | `inactive` |
| Wicken Fen - UKA00362 | `804` | `1999688` | EEA | `ODC-BY` | `2026-05-26T13:00:00Z` | None within 48h | `inactive` |
| St Osyth - UKA00445 | `1795` | `1732639` | EEA | `ODC-BY` | `2026-05-26T12:00:00Z` | None within 48h | `inactive` |
| Manchester Piccadilly - UKA00248 | `2312` | `5269` | EEA | `ODC-BY` | `2026-05-26T13:00:00Z` | None within 48h | `inactive` |

**Stage 1 Finding:** Probing `/v3/sensors/{id}/hours` confirmed **only `1 / 8` pilot locations (`157` Bexley - Slade Green) returned live PM2.5 reports within the 48h window**; `154` (Marylebone Road) updated other station pollutants on `2026-11-27T04:00:00Z` while PM2.5 sensor `246` remained silent.

---

## 3. Emergency Halt & Rollback Behavior

- **Pre-Flight / Instant Environment Halt:** Set `SENSORGUARD_POLLING_KILL_SWITCH=true` (blocks both Stage 1 `preflight-stations` -> `BLOCKED_KILL_SWITCH` with zero claim consumption and any recurring poller -> `SKIPPED_KILL_SWITCH` with `0` HTTP calls).
- **Stage 1 DB Halt / Cleanup:** Remove unused approvals or set `PilotControlState(control_name="openaq_stage1_preflight", kill_switch_active=True, opt_in_enabled=False)`. Consumed or incomplete `stage1_preflight_authorizations` rows remain permanently blocked from reuse unless explicitly superseded by `authorize-stage1 --recover-from-approval-id ...`. Recurring `openaq_live_polling` remains untouched and locked (`opt_in_enabled=False`).
- **Stage 2 Rollback & Schema:** `alembic downgrade 009_m87_commit_timing` (current head `010_single_use_stage1_proof`); restore PostgreSQL snapshot via `pg_restore` if needed; follow [MODEL_ROLLBACK.md](MODEL_ROLLBACK.md) for serving artifact rollback.

---

## 4. Stage 2 Hold Criteria & Operational Prep Commands (Not Approved Today)

Stage 2 operational CLI tooling is ready and strictly **fail-closed by default** (`pilot-cycle` defaults to zero-network dry-run targeting Stage 1 active location `157`; `--execute-poll` remains blocked `SKIPPED_KILL_SWITCH` until `PilotControlState("openaq_live_polling")` is explicitly unlocked):

```bash
# Provision verified sensorguard.shadow_artifact.v1 JSON files + shadow_manifest.json (0 network calls)
python -m sensorguard.cli build-shadow-artifacts --output-dir artifacts/shadow_suite

# Stage 2 safe dry-run check (0 network calls, defaults to location 157)
SENSORGUARD_SHADOW_ARTIFACT_DIR=artifacts/shadow_suite \
python -m sensorguard.cli pilot-cycle --location-ids 157
```

Do **not** authorize Stage 2 recurring polling until:
1. Operator decides whether Stage 2 should monitor solely active location `(157,)` (degraded single-station neighbor mode, `1–2` req/run within `48 req/day`) or first discover replacement active neighboring UK PM2.5 stations, passing explicit `--location-ids` cleanly.
2. `SENSORGUARD_SHADOW_ARTIFACT_DIR` is provisioned with verified `.json` shadow artifacts (`build-shadow-artifacts` -> `sensorguard.shadow_artifact.v1` + `shadow_manifest.json`) and prospective forecasts enforce post-commit completion proof (`commit_completed_at`, `min_lead_seconds >= 300s`, `commit_safety_margin_seconds >= 15s`).
3. Minimum prospective duration (`>= 7` days / `>= 168h`; hard floor `min_unique_hours >= 24`, `min_matched_outcomes >= 100`) and practitioner usability study ([REAL_USER_USABILITY_STUDY.md](REAL_USER_USABILITY_STUDY.md)) pass before any model promotion.

---

## 5. Formal Operator Sign-Off Ledger

- **Stage 1 Approval Wording (Executed Once):**  
  `Approve one-time Stage 1 OpenAQ station availability pre-flight check (<= 16 HTTP attempts total, no recurring polling, no opt_in_enabled change) across locations (141, 153, 154, 157, 698, 804, 1795, 2312).`
- **Stage 1 Status:** `COMPLETED` (`approval_id = s1_85f032d67045433f`, `execution_id = exec_d91ef7f6a583485a`, `16/16` HTTP requests, `1/8` active: location `157` / sensor `254`; evidence archived in [`docs/audit/stage1_preflight_result.json`](audit/stage1_preflight_result.json))
- **Stage 2 Recurring Polling Status:** `NO-GO (HELD — openaq_live_polling.opt_in_enabled = False)`
- **Operator Identity / UTC Timestamp:** `Dinesh / 2026-11-27T06:49:34Z`
