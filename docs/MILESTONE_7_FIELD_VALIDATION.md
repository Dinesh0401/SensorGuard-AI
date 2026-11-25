# SensorGuard AI — Milestone 7 Field Validation & Opt-In Pilot Protocol

**Milestone:** 7.5 — Bounded Live Pilot Pipeline, Delayed Ground-Truth Tracking & Field Validation  
**Date:** 2026-11-25  
**Branch:** `feat/milestone-3-forecasting-uncertainty`  
**Implementation Modules:** [`src/sensorguard/pilot_monitor.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/pilot_monitor.py) · [`src/sensorguard/db/forecast_persistence.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/db/forecast_persistence.py)  
**Test Evidence:** [`tests/test_pilot_monitor.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/tests/test_pilot_monitor.py)  

---

## 1. Opt-In Bounded OpenAQ Polling Architecture

To permit controlled future live-data collection without risking OpenAQ quota exhaustion, unattributed ingestion, or silent missing-data confounding, [`run_bounded_opt_in_poll`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/pilot_monitor.py) enforces five structural guardrails via [`BoundedPilotConfig`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/pilot_monitor.py) and [`PilotQuotaTracker`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/pilot_monitor.py):

1. **Default Opt-Out Fail-Safe:** `opt_in_enabled: bool = False`. Unless explicitly enabled at runtime with configured server-side credentials (`OPENAQ_API_KEY`), polling returns `SKIPPED_OPT_OUT` or `SKIPPED_NO_CREDENTIALS` with zero outbound network requests.
2. **Station Allowlist & Lookback Window Cap:** Restricted to approved pilot stations (`allowed_location_ids = (141, 153, 154, 157, 698, 804, 1795, 2312)`) and sliding UTC lookback window (`lookback_hours = 48`, `max_pages_per_sensor = 1`, `page_size = 100`), preventing accidental multi-month backfills.
3. **Hard Daily Request Quota:** `PilotQuotaTracker(max_requests_per_day=48)` resets strictly on UTC day boundaries and aborts remaining sensors cleanly (`status = "QUOTA_EXHAUSTED"`) before exceeding budget.
4. **Explicit Temporal Provenance Separation:** Every ingested `Observation` distinguishes:
   - `observed_at`: physical hourly measurement period end (UTC)
   - `received_at`: upstream provider delivery timestamp (when provided)
   - `fetched_at`: local HTTP ingestion completion timestamp (UTC)
   - `persisted_at`: database write timestamp (UTC)
   Any record failing non-finite, unit, range, or causality checks (`observed_at > fetched_at`) is tagged `INVALID` rather than silently corrected or dropped.
5. **Continuous Freshness & Source-Status Audit (`audit_station_staleness`):** Classifies each station against the poll reference timestamp into `HEALTHY` (`<= 3h`), `LATE` (`3h–12h`), or `NO_REPORT` (`> 12h` or absent) without conflating upstream reporting silence with confirmed physical hardware breakage.

---

## 2. Delayed Ground-Truth Arrival & Rolling Calibration Tracking

In live operation, a forecast persisted in [`forecasts`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/db/schema.py) for `target_time` may have its ground-truth observation arrive on time, hours late, or never. [`reconcile_delayed_ground_truth`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/pilot_monitor.py) enforces strict post-hoc audit separation:

- **Matched Arrivals (`matched_count`):** Joins `(sensor_id, target_time)` to the latest `VALID` `Observation` (`order_by(version.desc())`), computing `arrival_delay_hours = max(0.0, (fetched_at - target_time) / 3600)` and counting `delayed_arrival_count` (`> 1.5h` threshold).
- **Unmatched / Missing Ground Truth (`unmatched_missing_truth_count`):** Tracked explicitly as censored/unknown (`missing_label_rate`). Missing labels are **never** treated as covered, abstained-correct, or imputed.
- **Rolling Coverage & Censoring Alert:** Computes `published_picp`, `published_mpiw`, and exact 95% Wilson score CI strictly over matched published forecasts, and triggers `mnar_censoring_warning = True` whenever `missing_label_rate > 10%` or delayed label arrivals occur.

Verified deterministically in [`tests/test_pilot_monitor.py`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/tests/test_pilot_monitor.py): opt-out enforcement (`0` HTTP requests), lookback-bounded query (`datetime_from` UTC cap), daily quota exhaustion cutoff, delayed arrival tracking (`5h` late), abstained-vs-published separation, and non-inflation of `published_picp` under missing labels.

---

## 3. Consent-Based Pilot-User Evaluation Protocol (Pre-Staging Gate)

Before transitioning beyond internal staging with live telemetry:
1. **Participant Recruitment & Informed Consent:** Invite 5–10 technical practitioners (air-quality analysts / civil engineers / reliability engineers) with explicit disclosure that forecasts are experimental uncertainty intervals for historical/pilot situational awareness — **never** health/medical advisories.
2. **Guided Scenario Tasks (Using Verified UI in `apps/web`):**
   - Compare `ABSTAIN (stale_sensor_history)` vs `PUBLISH` intervals across UK AURN stations.
   - Inspect sensitivity slices (`gamma = 0.0 MAR` vs `gamma = 2.0 MNAR`) and `D_sens` spreads.
   - Verify whether users correctly distinguish *"upstream gap (`NO_REPORT`)"* from *"confirmed hardware breakage"* and understand why missing targets leave chart gaps.
3. **Quantitative Usability & Comprehension Targets:**
   - $\ge 80\%$ participant accuracy on distinguishing abstention causes vs sensor hardware failure.
   - Zero confusion between demonstration/archived Q1 2024 fixtures and live provider feeds.
   - Structured feedback logged cleanly before any public release consideration.

---

## 4. Milestone 7 Complete Deliverable & Staging Readiness Matrix

| Sub-Milestone | Deliverable Report | Key Measured Verification | Public Launch Status |
|---|---|---|---|
| **7.1 PostgreSQL 18 Integration** | [`POSTGRES_INTEGRATION_RESULTS.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/POSTGRES_INTEGRATION_RESULTS.md) | Alembic `001`–`005`, `14,556` obs & `576` persisted forecasts, `pg_dump` (`1.28s`) / `pg_restore` (`1.50s`), `91` pytest pass | Private Staging Verified |
| **7.2 Inference & Concurrency** | [`PRODUCTION_PERFORMANCE_REPORT.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/PRODUCTION_PERFORMANCE_REPORT.md) | `/forecasts` `c=20` p95 **`1236.79ms -> 362.33ms`** (`35.4 -> 119.8 r/s`, `0.0%` err; uncached `17.97ms` p95) | Windows single-host ceiling noted; Linux proxy pending |
| **7.3 Playwright Browser E2E** | [`BROWSER_E2E_RESULTS.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/BROWSER_E2E_RESULTS.md) | `3/3` Playwright Chrome tests passing (`11.5s`–`17.7s`), live PG18 FastAPI, Demo isolation verified | Verified in CI workflow |
| **7.4 / 7.7 Adaptive SAGE Causal Audit** | [`SAGE_ADAPTIVE_RESEARCH_PROTOCOL.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/SAGE_ADAPTIVE_RESEARCH_PROTOCOL.md) · [`MILESTONE_7_7_SCIENTIFIC_CONSISTENCY_AUDIT.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/MILESTONE_7_7_SCIENTIFIC_CONSISTENCY_AUDIT.md) | Causal timestamp-boundary + 2h embargo OOF; natural $\rho=+0.1749$ ($<0.20$), MNAR $\rho=+0.2627$, 8/8 LOSO $\rho>0.25$ (rescues `station_4` `92.07%` vs MAR `85.36%`, loses avg LOSO Winkler `6.59` vs MAR `4.63` / CQR `5.26`); full negative results reconciled | Empirical prototype audited under causal splits |
| **7.5 Opt-In Live Pilot Protocol** | [`MILESTONE_7_FIELD_VALIDATION.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/MILESTONE_7_FIELD_VALIDATION.md) | Bounded opt-in polling, daily quota caps, delayed-label reconciliation tested via mocks | **NO-GO for Public Prod** · **GO for Opt-In Controlled Pilot** |
