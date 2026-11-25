# SensorGuard AI — Live Data Operational Runbook (Phase 8.4)

**System Scope:** Bounded, Opt-In OpenAQ v3 Shadow-Live Ingestion, PostgreSQL 18 Persistence, Delayed-Truth Evaluation & Emergency Controls  
**Core Modules:** [`src/sensorguard/pilot_monitor.py`](../src/sensorguard/pilot_monitor.py) · [`src/sensorguard/shadow_eval.py`](../src/sensorguard/shadow_eval.py) · [`src/sensorguard/api.py`](../src/sensorguard/api.py)  
**Default Security Posture:** **FAIL-CLOSED LOCKED (`kill_switch_active = True`, `opt_in_enabled = False`)**

---

## 1. Pre-Flight Authorization Checklist (Before Any Outbound Request)

Do **not** unlock live polling until all items below are confirmed by the human operator:

1. **Secrets Hygiene:** Verify `.env` is excluded from Git (`.gitignore`) and stores server-side `OPENAQ_API_KEY` + PostgreSQL 18 `DATABASE_URL`. Zero keys may ever enter `apps/web` or `NEXT_PUBLIC_*`.
2. **Database Schema at Head (`006`):**
   ```powershell
   alembic upgrade head
   alembic current  # Must display 006_durable_pilot_control (head)
   ```
3. **One-Time Station Availability Pre-Flight (Requires Approval):** Offline snapshot audit (`2026-11-22`) found **only 2 of 8 pilot locations (`154 Marylebone Road`, `157 Bexley - Slade Green`)** updated recently via OpenAQ (`2026-11-22T11:00Z`), whereas the 6 EEA `ODC-BY` feeds stopped on `2026-05-26`. Upon authorization, query `/v3/locations/{id}` once (8 requests) to confirm which sensors actively report before enabling recurring polling.
4. **Quota Cadence Math (`max_requests_per_day = 48`):**
   - Each polled PM2.5 sensor consumes `1` page request per run (`max_pages_per_sensor = 1`).
   - Polling all `8` stations consumes **8 requests/run** $\implies$ maximum **6 polls/day** (poll every **4 hours**, NOT hourly, to avoid exhausting the 48/day quota in 6 hours).
   - Polling only the `2` verified LAQN active stations (`allowed_location_ids=(154, 157)`) consumes **2 requests/run** $\implies$ supports hourly polling (**48 req/day**).

---

## 2. Activating Bounded Shadow-Live Polling (`shadow_live`)

Once pre-flight passes and operator signoff is recorded:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sensorguard.pilot_monitor import set_polling_kill_switch

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
with SessionLocal() as sess:
    set_polling_kill_switch(
        sess,
        kill_switch_active=False,
        opt_in_enabled=True,
        reason="Operator-approved bounded shadow_live pilot (max 48 req/day).",
        updated_by="operator_id",
    )
```

Verify `/api/v1/pilot/readiness` returns:
- `operational_data_mode == "shadow_live"`
- `kill_switch_active == False`, `opt_in_enabled == True`
- `primary_model_class == "SAGEForecaster"` (Adaptive SAGE remains `promoted_to_primary == False`)

Each poll cycle runs `run_bounded_opt_in_poll(SessionLocal, BoundedPilotConfig(opt_in_enabled=True, ...))` with `lookback_hours = 48` (bounded sliding reconciliation window — never `/latest`-only), automatically acquiring leader lease `openaq_hourly_poll`, checking `DurablePilotQuotaTracker`, preserving append-only observation versions, and logging `PilotIngestAuditRun`.

---

## 3. Instant Emergency Kill-Switch Procedures

Stop all outbound polling immediately using **any** of three independent circuit breakers (Tier 1 takes precedence instantaneously without touching the DB):

1. **Tier 1 (Immediate Environment Override — Zero DB Required):**
   ```powershell
   $env:SENSORGUARD_POLLING_KILL_SWITCH = "true"
   # Or set SENSORGUARD_POLLING_KILL_SWITCH=true in service environment and restart worker
   ```
   All worker polls immediately return `SKIPPED_KILL_SWITCH` and record an audit trail row.
2. **Tier 2 (Durable Database Lock Across All Workers):**
   ```python
   set_polling_kill_switch(
       sess,
       kill_switch_active=True,
       opt_in_enabled=False,
       reason="Emergency operator halt.",
       updated_by="operator_id",
   )
   ```
3. **Tier 3 (Credential Revocation):** Remove/rotate `OPENAQ_API_KEY` server-side (`SKIPPED_NO_CREDENTIALS`).

---

## 4. Failure & Incident Playbooks

| Failure Condition | System Signal / Audit Status | Immediate Root-Cause Check | Operator Remediation |
|---|---|---|---|
| **OpenAQ Rate Limiting** | `PilotIngestAuditRun.status == "HTTP_429_BACKOFF"` | Poll halted cleanly on 429 after retry; lease released. | Reduce poll frequency or restrict `allowed_location_ids` to active subset `(154, 157)`. |
| **Daily Quota Exhaustion** | `status == "QUOTA_EXHAUSTED"`, `requests_remaining == 0` | Daily UTC cap (`48`) reached cleanly before overshoot. | Verify poll cadence math (`sensors * polls_per_day <= 48`); wait for automatic `00:00 UTC` rollover. |
| **Worker Crash Mid-Poll** | Next worker shows `recovered_from_expired_lease = True` | Previous holder crashed before releasing lease. | TTL (`300s`) recovers lock automatically; inspect `error_summary` in `pilot_ingest_audit_runs`. |
| **Upstream Silence > 12h** | `audit_station_staleness` marks `NO_REPORT`; API returns `ABSTAIN` | Could be EEA/LAQN feed lag, provider outage, or maintenance (**never assume hardware destruction**). | Inspect `PilotIngestAuditRun.records_fetched`; do NOT backfill synthetic values; verify upstream location status manually if prolonged >48h. |
| **Upstream Value Revisions** | `records_revised > 0` in `PilotIngestAuditRun` | Provider corrected historical hourly value within 48h window. | Automatic append-only `version + 1` rows preserve original data; run `invalidate_pilot_feature_cache()` if serving predictions depend on affected window. |
| **High Missing Shadow Targets** | `shadow_evaluation_summary.mnar_censoring_warning == True` | Ground truth delayed (`>1.5h`) or missing post-cutoff. | Hold promotion decisions; keep `calibration_claim_eligible == False` until >=100 matched arrivals clear bias review. |

---

## 5. Database & Model Rollback Procedures

- **Schema Rollback (`006` -> `005`):**
  ```powershell
  alembic downgrade 005_persistent_forecasts
  ```
  Drops `pilot_ingest_audit_runs`, `pilot_control_state`, `pilot_poll_leases`, `pilot_quota_states`, and `forecast_runs.execution_mode`/`data_mode` cleanly without touching `observations` or `stations`.
- **PostgreSQL Snapshot Restore (Before Risky Changes):** Always run `pg_dump` before modifying production/staging PostgreSQL state.
- **Model Artifact Governance Rollback:** Follow [`MODEL_ROLLBACK.md`](MODEL_ROLLBACK.md); primary serving artifact remains SHA-256 pinned to `e552ca09a37856f809f2dc6db9f8eabf291a6fd4103da92874ac440bd91e4115` unless an audited replacement manifest is signed off.
