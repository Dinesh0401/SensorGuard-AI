# SensorGuard AI — Project 03 Research & Product Blueprint

**Track:** ML/MLOps. **Status:** proposed; originality and scientific claims unverified.  
**Research question:** Can we preserve forecast calibration when public environmental sensors fail in nonrandom patterns?  
**Product:** Public air-quality forecasting and sensor-network reliability dashboard.  
**MVP:** One selected city; hourly PM2.5, +1-hour prediction, 90% prediction interval; historical replay and provider-backed near-real-time updates.  
**Final-year project:** separate, out of scope.

## 1. Product definition and scope

### Problem
Public sensors can fail or report late. Their absences may correlate with weather, physical conditions, network outages, or the unknown pollution level. Data observed after failure can be biased, and a model can appear confident despite limited evidence. Sensor outages, ingestion delay, upstream outages and actual adverse sensor conditions must not be conflated.

### Users
1. Community observers: station status, nearby measurement history, point forecasts, uncertainty and freshness.
2. Researchers: historical replay, outage scenarios, baselines, calibration and subgroup comparisons.
3. Network operators: data quality/coverage dashboard, source delay alerts, validated failure explanations, exports.

### Initial non-goals
No clinical/medical advice, no health-critical alert promises, no official AQI certification, no hardware manufacturing, no global sensor coverage guarantees, no fully automated publication of speculative pollution crises, no claim of distribution-free coverage under unrestricted MNAR.

### Product promise
"Show what the station measured, what the model estimates next, how uncertain it is, and when the available evidence is too weak to make a trusted forecast."

## 2. First-principles and 5 Whys

Observed-only evaluation overrepresents available stations. Availability may depend on conditions that influence pollution. Calibration based only on available ground truth may be biased, even if model accuracy appears adequate.

5 Whys chain: (1) Interval misses during events? (2) Sensors stop reporting precisely in some events. (3) Remaining data no longer represent affected conditions. (4) Training/calibration ignore the observation process. (5) Existing evaluation omits intentionally unobserved target outcomes and overstates reliability.

Central distinction: input-sensor masking is experimentally testable on historically measured target values; true missing target ground truth is not identifiable from observed records alone without external reference or assumptions.

## 3. Closely related prior work (verify before research claim)

- Zaffran et al., *Conformal Prediction with Missing Values*, ICML 2023, https://proceedings.mlr.press/v202/zaffran23a.html
- *Citywide Air Quality Forecasting over Sparse Sensor Networks*, 2026, https://www.mdpi.com/2224-2708/15/4/52
- *OmniST: Topology-Invariant Spatiotemporal Forecasting...*, 2026, https://arxiv.org/abs/2611.04256
- *SA-IDS: ... Informative Sensor Silence ...*, preprint 2026, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6602679
- *Evolving trends in application of low-cost air quality sensor networks*, 2025, https://www.nature.com/articles/s41612-025-01216-4
- *Adaptive Conformal Prediction for Time Series with Missing Values*, TMLR 2026, discover full bibliographic details and compare before implementing.

Originality is NOT established. The proposed gap to test: decision-quality calibration under a constrained uncertainty budget and partially specified MNAR conditions, with explicit abstention and evaluated resource use.

## 4. Mathematical model

Let i be a public monitoring station, t a UTC hourly timestamp, and Y[i,t] its latent PM2.5 concentration (µg/m³). Let R[i,t] in {0,1} indicate whether an admissible observation was available by the decision cutoff, and X[i,t] include known-at-issue-time weather, spatial neighborhood, timestamp and station metadata.

The observation mechanism is P(R[i,t]=1 | X[<=t], Y[i,t], operational context). Missingness can be MCAR, MAR conditional on X or MNAR when dependence on the unobserved Y remains. Under unrestricted MNAR, neither the full target distribution nor universal conditional coverage is identifiable from observed values alone.

Forecast at issue time t for horizon h=1: point estimate mu_hat[i,t+h] and bounds [L[i,t+h], U[i,t+h]]. For an evaluation distribution where the true Y is accessible, target P(L<=Y<=U) near 1-alpha, e.g. alpha=0.10, while minimizing interval width, missed intervals, compute cost, and abstention rate. This is a testable operational goal, not an unconditional formal guarantee.

Baseline split conformal residual score: s_k = max(q_lo(x_k)-y_k, y_k-q_hi(x_k), 0). Calibrated interval = [q_lo(x)-q_alpha, q_hi(x)+q_alpha] using calibration residual quantiles. A simple rolling recalibration improves temporal adaptability but may fail under informative missing labels; never claim guaranteed coverage there.

Sensitivity model for experiments: logit P(R=1 | X,Y)=g_phi(X) + gamma*(Y-c)/s. Gamma is an analyst-imposed sensitivity parameter, not recoverable from MNAR observations alone. Simulate multiple gamma values. Derive scenario-specific intervals and optionally take a conservative envelope; compare against equally conservative baselines.

Candidate research mechanism **SAGE (Sensitivity-Aware Guarded Estimation)**, provisional name:
1. Train a simple forecast model with explicit sensor availability and age features.
2. Train an observed-covariate reporting model and score blackout severity; do NOT treat its propensity as fully identified under MNAR.
3. Replay multiple hypothesized MNAR sensitivities and outage structures, calibrating scenario-conditioned forecast residuals when true target outcomes are held back for evaluation.
4. Combine sensitivity scenario bounds, rolling calibration, and effective-sample-size safeguards.
5. Abstain or publish 'insufficient evidence' under severe outage, stale upstream data, or scenario disagreement; display reason and source timestamps.
6. Benchmark each module through ablations; if it does not improve the relevant loss relative to strong baselines, remove or revise it.

## 5. Data design

### Primary source
OpenAQ API v3: https://docs.openaq.org/api ; authenticated with X-API-Key; station sensors, /v3/sensors/{id}/hours for hourly aggregates, latest endpoint for recent reporting. Verify sensor coverage, timestamps, units and API constraints. Choose the pilot city ONLY AFTER automated availability audit (aim for >=8 active and sufficiently complete PM2.5 sensors plus multiple months of historical records; fallback to another city or a reproducible public research dataset if unavailable). Explicitly report actual station count and gaps.

### Context and supplemental sources
Open-Meteo weather forecasts and possibly air-quality model forecasts: https://open-meteo.com/en/docs/air-quality-api ; label model values as model estimates, never observed ground truth. For backtests, use only data that could have been available at issue time; retrospective reanalysis or revised model histories are not legitimate historical contemporaneous forecasts.

### Critical data provenance rules
- Store source ID, station ID, sensor ID, parameter, unit, measurement interval, observed_at UTC, received_at UTC, source update time if available, quality flag, provenance and raw payload reference.
- Normalize PM2.5 units to µg/m³ only if unit conversion is documented; do not combine different averaging windows or measurement methods blindly.
- Preserve raw observations and append ingestion revisions; keep event time distinct from arrival time.
- Distinguish `NO_REPORT`, `LATE`, `INVALID`, `SOURCE_UNAVAILABLE`, `OFFLINE_CONFIRMED`, `HEALTHY`. Never infer hardware breakage solely from missing API measurements.
- Respect per-provider licensing, attribution, quotas, retention limits and terms; no API keys in the frontend or version control.

## 6. Requirements and acceptance criteria

### Functional requirements
F01 station discovery/audit; F02 hourly ingestion with retries/backoff/pagination; F03 time-aware data normalization and quality flags; F04 replay of issue-time windows; F05 feature engineering with missingness and age; F06 baselines and reproducible experiment runner; F07 outage simulator with MCAR/MAR/MNAR and structured block outages; F08 point and interval forecasts; F09 interval validation and ablation reports; F10 public dashboard map, sensor state, trend, forecast, uncertainty, provenance; F11 administrative model/version/incident detail; F12 explicit abstention and stale-data behavior.

### Non-functional requirements (engineering targets, not claimed achievements)
- Forecast updates hourly when upstream observations are available; show UTC time, latest measurement and refresh status.
- Cache dashboard queries; initial target p95 API response <=500 ms on a small pilot deployment excluding external providers.
- Deterministic experiments with recorded random seeds, time ranges, station splits, dataset manifests and package versions.
- Minimum 80% test coverage for deterministic core modules; API contracts, ingestion, scenarios, and no-leakage checks must have tests.
- Accessible responsive UI, keyboard navigation and readable charts; no misleading green 'healthy' state when data is missing.
- Secrets only in server-side environment variables, HTTPS, basic rate limiting and request validation; no unnecessary user personal information.
- Provider failure leads to 'stale data' state and a preserved last-known forecast with timestamp, not silently fabricated fresh readings.

## 7. Architecture

```
OpenAQ v3 / (optional) weather providers
          |
     Ingestion worker (hourly, retries, API budgets)
          |
  Raw snapshots + normalized observations (PostgreSQL)
          |
  Time-aligned feature pipeline and missingness diagnosis
          |-----------------------------|
 Historical replay / experiments     Production forecast worker
          |                             |
 Mask scenarios and baselines     Model+calibrator+guard policy
          |                             |
  Experiment artifacts              Forecast records
          |                             |
   Experiment dashboard        FastAPI read API
                                         |
                           Next.js web app + map + charts
```

Recommended monorepo:
```
sensorguard-ai/
  AGENTS.md
  README.md
  .env.example
  docker-compose.yml
  apps/
    api/                 # FastAPI + SQLAlchemy + Alembic
    web/                 # Next.js + TypeScript + Tailwind + MapLibre
  services/
    ingest/              # OpenAQ adapter and background job
    forecast/            # Scheduled inference and cache
  research/
    src/sensorguard/     # preprocessing, masks, baselines, models, calibration, safeguards
    tests/
    configs/
    scripts/
    notebooks/           # exploration only; production logic lives in src
  docs/
    PRD.md
    SRS.md
    ARCHITECTURE.md
    DATA_DICTIONARY.md
    RESEARCH_PLAN.md
    NOVELTY_AUDIT.md
    EXPERIMENTS.md
    SECURITY.md
  infra/
    docker/
  .github/workflows/
```

Stack: Python 3.11+, FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL, pandas/Polars, NumPy, SciPy, scikit-learn, LightGBM optional, pytest, Ruff, mypy, MLflow (local), Next.js TypeScript, Tailwind, MapLibre, Docker Compose, GitHub Actions. Keep local MVP runnable without cloud bills. Heavy GNN is optional after reproducible baselines.

## 8. Data model

- stations(id, provider, provider_location_id, name, lat, lon, timezone, metadata_json, created_at)
- sensors(id, station_id, provider_sensor_id, parameter, canonical_unit, averaging_period, source_metadata_json)
- raw_ingest_batches(id, provider, fetched_at, status, raw_path, response_hash, error_code)
- observations(id, sensor_id, observed_at, received_at, value, unit, quality_flag, raw_batch_id, source_version) — unique key defined for provider measurement identity + revision policy
- sensor_status_events(id, sensor_id, start_at, end_at, status_type, evidence_json, confidence_note)
- forecast_runs(id, model_version_id, issued_at, source_cutoff, code_commit, run_metadata_json)
- forecasts(id, forecast_run_id, sensor_id, horizon_hours, target_at, point_estimate, lower, upper, nominal_coverage, decision, decision_reason, newest_source_observed_at)
- evaluation_outcomes(id, forecast_id, actual_value, actual_available_at, outcome_source, evaluation_mode)
- experiments(id, dataset_manifest_hash, config_json, code_commit, started_at, completed_at, artifacts_uri)
- model_versions(id, training_range, feature_version, artifact_uri, calibration_version, created_at)

Require migrations, constraints, indices by station/sensor/time, and separate evaluation truth for masked synthetic experiments. Do not mix original measurements with simulated outage data.

## 9. API contracts (initial)

- GET /healthz — service/liveness
- GET /api/v1/stations?bbox=... — station catalog and availability summary
- GET /api/v1/stations/{station_id}/observations?from=&to= — observed series and freshness
- GET /api/v1/stations/{station_id}/forecasts?limit= — issued forecast, interval, model version, abstain reason
- GET /api/v1/stations/{station_id}/reliability — missingness statistics and evaluation summary
- GET /api/v1/research/experiments — public experiment summaries, with private raw data excluded
- GET /api/v1/research/experiments/{id} — replay results, scenarios and caveats
- POST /api/v1/admin/ingest — protected or local admin-only manual backfill; strict limits
- POST /api/v1/admin/evaluate — protected local/admin request for experiment execution; never expose arbitrary code execution

Implement typed schemas, error responses, paging, date validation, caching, and OpenAPI docs.

## 10. Research experiments

### Data split
Chronological train/calibration/test only; separately run held-out station / held-out city evaluations. Prevent leakage from time-adjacent windows, revised retrospective values, future weather and future labels. Keep simulator and experiment RNG seeded; store manifest checksums.

### Baselines
B0 last observation carried forward and seasonal naive (t-24h). B1 neighbor median/inverse-distance interpolation. B2 supervised gradient-boosted model with last/lag/neighbor/weather features; quantile forecasts. B3 static split conformal and conformalized quantile regression. B4 rolling/adaptive conformal. B5 missingness-conditioned conformal or ACP-TSM where feasible. B6 optional recent sensor-network competitor if code/data feasible. Ensure fair tuning, same data and matching targets.

### Outage scenarios
S0 natural observed gaps. S1 MCAR independent per reading. S2 MAR conditional on observable conditions. S3 MNAR conditioned on hidden simulated true PM2.5 via gamma sweep. S4 contiguous single-station outages 3/12/24/48 h. S5 correlated neighborhood/station failures. S6 peak pollution dropout. S7 upstream late-arrival/backfill as a separate problem. Keep truth in evaluator-only tables where originally observed values have been synthetically masked at inference.

### Metrics
- Empirical prediction interval coverage probability (PICP) with Wilson/bootstrap CIs; target nominal 90%.
- Mean prediction interval width (MPIW) and Winkler/interval score; trade off sharpness with coverage.
- Worst-stratum coverage gap: by scenario, station, missingness severity, time, and high-pollution quantile.
- MAE/RMSE/pinball loss for quality, but do not use these as substitutes for calibration.
- Abstention rate and coverage among non-abstained forecasts; report both, plus total reliability/availability.
- Diagnostic false alarm rate, ingest freshness lag, inference latency and compute time.
- Real-world natural gaps are evaluated only on visible outcomes with explicitly labeled selection bias; cannot claim MNAR-correct full-world coverage from observed data alone.

### Ablations
Remove mask features; remove sensitivity sweep; replace risk-aware intervals with static conformal; remove rolling recalibration; remove abstention; remove neighbor features; vary assumed gamma; vary sensor count; vary extent of label delay. Report confidence intervals and negative results.

### Hypothesis and stop rule
H0: method does not improve interval reliability under matched width/abstention versus strongest baseline. Suggested research hurdle: improve worst-stratum coverage gap by >=5 percentage points in pre-specified synthetic MNAR scenarios without disproportionate interval inflation or abstention, with uncertainty intervals across seeds. This is a pre-registered target, NOT a promised result. If strong competitor dominates after fair tuning, revise contribution and keep failures documented.

## 11. UI/UX design

Home page: product overview, last-updated note, current coverage limitations, select pilot city. City view: MapLibre map of monitored stations with `healthy`, `stale`, `unavailable` indicators and station count by state; avoid AQI colors unless correctly defined. Station detail: graph of observed PM2.5; +1h point + 90% interval; freshness, source attribution, model version; explanation of uncertainty / 'insufficient evidence' state. Reliability lab: scenario switch, historical playback (clearly labeled SIMULATED), plot nominal vs empirical coverage, interval width and abstention, baseline selector. Research page: objective, datasets, publication-style methodology, limitations and code link.

Display units, timezone, measurement interval, observed-vs-model distinction and source attribution on every chart. No claim of official health or regulatory guidance.

## 12. Deployment strategy

Local: Docker Compose PostgreSQL + FastAPI + Next.js; worker as scheduled container. CI: Ruff, type checking, pytest, frontend lint/build, schema validation, migration smoke test. Stage: deploy FastAPI/Postgres/worker with a platform allowing scheduled tasks; web on hosting of choice. Hosting costs/limits must be checked; free tiers are not guaranteed. Use uptime checks, DB backups, API logs with sensitive values redacted, rollback by model version, and provider attribution. Do not schedule ingestion above provider quotas.

## 13. Milestones (research-first)

- M0: Literature/novelty audit, PRD/SRS, data audit, exact benchmark dataset. Evidence: docs, source citations, sample dataset manifest.
- M1: Ingestion and replay. Evidence: 3+ tests of out-of-order/late/invalid observations, station timeline UI, reproducible local run.
- M2: Baseline benchmarking. Evidence: chronological split, point/interval forecasts, baseline comparison table and plots.
- M3: Nonrandom outage simulator. Evidence: MCAR/MAR/MNAR, structural outages, evaluator-only preserved truth, leakage tests.
- M4: Candidate SAGE method and ablations. Evidence: at least 3 seeds, negative/positive results and failure cases.
- M5: Public pilot and production hardening. Evidence: site URL, current provider provenance, honest stale/no-data state, CI and reproducible deployment instructions.
- M6: Research report and portfolio demo. Evidence: paper-like report, limitations, tagged release, demo video.

30-day stretch: M0-M3, initial M4, and one limited MVP public demo; further weeks for mature M4-M6. Do NOT market as publication-level novelty or guaranteed coverage before replicated validation.

## 14. Developer rules (copy to root AGENTS.md)

1. Always write a plan and tests before implementation; produce one milestone per agent task.
2. Use only data that was available by forecast issue time; preserve event/ingest timestamps and no-lookahead replay.
3. All public observations must be marked measured, estimated, delayed or simulated; never invent readings.
4. Distinguish sensor silence from confirmed hardware outage.
5. Missingness is not always identifiable; avoid universal coverage promises under MNAR.
6. Document formulas, assumptions, metrics and baseline comparisons in docs/RESEARCH_PLAN.md.
7. Build the simplest correct reproducible baseline before complex GNN or deep learning.
8. Never commit keys, passwords, `.env` or private provider data. Use backend-only credentials and `.env.example`.
9. Never execute destructive file/database commands, paid deployments, external publishes or GitHub pushes without explicit human approval.
10. After each milestone: run tests, report commands/results, updated files, reproducibility notes, limitations, next small milestone; use descriptive git commits if approved.
