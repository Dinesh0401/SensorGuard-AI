# Antigravity Prompt Pack — SensorGuard AI

## Setup

1. Install Google Antigravity from https://antigravity.google/download or the official IDE extension documented at https://antigravity.google/docs/getting-started . Antigravity uses Google authentication; ChatGPT Plus is a separate subscription.
2. Create a new empty folder `sensorguard-ai`, initialize Git, and open it as a trusted workspace only if you trust its contents.
3. Place `SensorGuard_Project03_Blueprint.md` under `docs/PROJECT_BLUEPRINT.md` and the root rules under `AGENTS.md`.
4. Keep agent permissions review-oriented. Approve dependencies, network access, terminal actions and any cloud cost before execution.
5. Execute prompts in order. At the end of each prompt inspect the diff, run verification, and commit if correct. Do not issue all prompts in one giant autonomous job.

## Prompt 0 — Plan only

> You are acting as a senior ML researcher, applied statistician, MLOps engineer, architect, and quality engineer. Read `docs/PROJECT_BLUEPRINT.md` and `AGENTS.md`. This is a research MVP, not a chatbot or tutorial. First do an architecture and scope review; do NOT code. Produce `docs/PRD.md`, `docs/SRS.md`, `docs/ARCHITECTURE.md`, `docs/RESEARCH_PLAN.md`, and `docs/NOVELTY_AUDIT.md`. The novelty audit must distinguish documented prior work from hypothetical gaps and cite URLs. Scope v1 to one pilot city, hourly PM2.5, one-hour-ahead forecasts, 90% intervals, simulated outages with evaluator-only ground truth, and honest abstention. Propose milestone-sized tickets with acceptance criteria, risks, failure modes and dependencies. Mark all missing external information as TO VERIFY; never invent API results, sensor counts, benchmark scores or research novelty. Show the proposed documents before implementation.

## Prompt 1 — Skeleton and development environment

> Implement only milestone M1a from the approved blueprint: repository scaffold using `apps/api` (FastAPI), `apps/web` (Next.js + TypeScript), `research` Python package, `services/ingest`, PostgreSQL and Docker Compose. Create `.env.example`, `.gitignore`, README setup, CI lint and test scaffolding, database migrations, `/healthz`, frontend placeholder, and commands for Windows PowerShell and Linux/macOS. No external API calls or deployment. Add smoke tests. Run them and report exact output. Do not add complicated ML yet. Show all modified files and wait for review before next milestone.

## Prompt 2 — Data provider audit and ingestion

> Implement M1b: OpenAQ v3 provider module with an `X-API-Key` server-side header, typed API response validation, pagination, exponential backoff, bounded rate control, and hourly readings from `/v3/sensors/{id}/hours`. Build a command to audit station coverage across candidate regions without assuming a city has enough sensors. Produce a machine-readable audit containing source, sensor IDs, PM2.5 units, first/last timestamp, observed cadence, gap rates, and usable history. Preserve `observed_at` vs `received_at`, provenance, sensor quality and raw response snapshots. Include fixture tests for duplicate, stale, late, out-of-order and invalid data. If no key exists, run only fixture tests and clearly report the limitation. Do not invent or silently fill measurements.

## Prompt 3 — Dataset splits, replay and baseline models

> Implement M2. Build as-of-time hourly feature generation and chronological train/calibration/test splits. Ensure future observations, future weather, late-received values and target labels cannot enter inference. Add persistence, seasonal-naive, neighbor interpolation, quantile gradient-boosted forecasts and static/rolling conformal methods. Use a documented PM2.5 research dataset or audited OpenAQ subset, logging provenance. Create deterministic CLI scripts with YAML configs, seeds, CSV/Parquet output, metrics for MAE, interval coverage and width, and tests designed to fail if leakage occurs. Produce one baseline comparison report. Never claim improvements until validated.

## Prompt 4 — Structured missingness simulator

> Implement M3. Build an offline evaluation harness with seven scenarios: natural missingness, MCAR, MAR, true-value-dependent MNAR with declared gamma values, continuous blackout, geographically correlated outage, high-pollution dropout, and delayed upstream reporting. Synthetic masks must hide values ONLY from model input; original outcomes stay in a restricted evaluation table. Support 10/30/50% intended outage rates where feasible, 3/12/24/48-hour outages, 3 seeds initially, and fixed chronological splits. Report actual realized missing rates and scenarios, not just requested parameters. Add unit/integration tests to prove truth is inaccessible to forecasters.

## Prompt 5 — Candidate SAGE algorithm

> Implement M4 only after baselines and simulator pass. Design a conservative sensitivity-aware guarded prediction method. Use missingness/severity features, calibrate forecasts where outcomes are verifiable, model MNAR as a sensitivity range rather than identifiable fact, construct scenario-dependent intervals, and allow explicit abstention on low evidence. Preserve reasons and intervals in forecast records. Compare against static conformal, rolling conformal, and missingness-aware baselines under matched coverage/width/abstention. Run ablations removing the mask features, sensitivity policy, adaptive calibration, and abstention. Log findings including failure cases and negative results; do not announce a novel algorithm without evidence.

## Prompt 6 — Backend APIs and scheduling

> Implement read-only public station/observation/forecast/reliability APIs and a scheduler for respectful periodic provider pulls. Ensure output includes latest observed time, fetched time, source citation, unit, model version, target time, prediction interval, nominal coverage, abstention and stale flags. Cache station endpoints and write contract tests. Add restricted admin ingestion and experiment actions with authentication; no arbitrary code execution or destructive functionality. Keep forecasts explicitly historical when replayed; do not label replay outputs live.

## Prompt 7 — Public application

> Implement the research-friendly public interface: a clean responsive city map with station states, timestamped station history, +1h prediction with 90% interval, source provenance, warnings when sensor coverage is low, and a simulator research tab with baseline-vs-method comparisons. Use MapLibre or an equivalent open map renderer with compliant tile attribution. Clearly distinguish measured values, simulated outages and forecasts. Present calibration summaries with caveats, not invented confidence guarantees. Add empty/loading/error states, keyboard accessibility, mobile testing and E2E smoke tests. Show screenshots and test results.

## Prompt 8 — Security, CI, reproducibility and rollout

> Run final release hardening. Audit secrets, dependency versions, API quota handling, SQL migrations, server input validation, authentication, HTML/chart accessibility, provider attribution, timeout/retry behavior, backups, telemetry, and reproducible training. Add GitHub Actions for tests and lint, a smoke deployment script, docker-compose dev, model rollback documentation, and `docs/RESULTS.md` with results actually computed. Prepare public release checklist and rollback procedure. Never push, publish, deploy paid resources or modify cloud accounts without my explicit approval. Return a detailed verification and blocker report.

## Prompt 9 — Research report

> Create a paper-style technical report using the real experiment outputs: precise problem and limitations, complete prior work table, assumptions, equations, dataset provenance, split details, baseline reproducibility, scenario generator specification, confidence intervals, performance/coverage/width/abstention tables, ablations, failure analysis, ethics and threats to validity. Distinguish observed-only calibration from simulated-mask evaluation with preserved truth. Cite sources, report all actual numbers, and flag every unsupported novelty claim. Also generate a concise resume bullet and a 2-minute portfolio demo outline grounded in implemented evidence.

## Standard end-of-task format (ask every agent to use)

- Milestone and precise scope
- Files modified
- Design choices and why
- Tests run + actual pass/fail output
- Benchmarks with config/seed/data hash or `NOT RUN`
- Known limitations and no-leakage checks
- Suggested descriptive commit message
- Next smallest task; do not automatically start it
