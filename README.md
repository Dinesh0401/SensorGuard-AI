# SensorGuard AI

**Project 03 · ML/MLOps Research Prototype · v0.5.3 (Milestone 6.1 Audited)**

> **Investigating:** Can we preserve useful forecast calibration when public environmental sensors fail in non-random patterns?

[![Python & Frontend CI](https://github.com/Dinesh0401/SensorGuard-AI/actions/workflows/ci.yml/badge.svg?branch=feat%2Fmilestone-3-forecasting-uncertainty)](https://github.com/Dinesh0401/SensorGuard-AI/actions/workflows/ci.yml)

> **Status & Disclaimer:** Internal engineering research prototype evaluated on **archived Q1 2024 UK AURN PM₂.₅ data** (`2024-01-01` – `2024-03-31`, 8 stations across Greater London and regional England). Live OpenAQ polling is **disabled by default**. This repository makes **no formal unconditional MNAR coverage guarantee**, **no public health claims**, and is **not approved for public production launch**.

---

## Verified Benchmark Summary (`SG-BENCH-20261125-040900`)

Evaluated strictly on `2,727` valid observed test hours (`3,496` test hourly grid rows; dataset content hash `33805c42097fc86e`; nominal target `90%`):

| Method (Natural Regime) | MAE | Overall PICP | Pub. PICP | Overall MPIW | Winkler | Abstention |
|---|---:|---:|---:|---:|---:|---:|
| Persistence + Split Conformal | **0.24** | 96.88% | 97.02% | **2.00 µg/m³** | **3.16** | 0.44% (`12`) |
| LightGBM + CQR (Romano 2019) | 0.29 | 99.89% | 99.89% | 11.35 µg/m³ | 11.45 | 0.44% (`12`) |
| **SAGE Full** (Sensitivity Envelope) | 0.28 | **99.93%** | **99.93%** | 17.01 µg/m³ | 17.06 | 0.44% (`12`) |

**Honest Findings:**
- Under exchangeable Q1 2024 conditions, Persistence and `LightGBM + CQR` deliver substantially sharper intervals and better Winkler scores than SAGE.
- On 8-fold Leave-One-Station-Out CV, CQR wins on Winkler score on all 8 stations; only on held-out `station_4` (`Bexley - Slade Green`) does CQR fall below nominal (`81.72%`) while SAGE preserves `90.72%` (`MPIW 4.76`, `Winkler 13.23`).
- In Q1 2024 testing, `D_sensitivity` equals a calibration-global constant (`3.32 µg/m³ < 8.0`), so all test abstentions (`12` natural, `26` MNAR `seed=42`, `1.09%` 3-seed ablation mean) come from the `age_hours > 12h` freshness guard; `n=12` natural stale-history samples had `0` published forecasts under both SAGE and baseline freshness policies.

---

## Quick Start

### Local Python & Next.js Development

```bash
git clone https://github.com/Dinesh0401/SensorGuard-AI.git
cd SensorGuard-AI
git checkout feat/milestone-3-forecasting-uncertainty

# Python environment (3.11+)
python -m venv .venv
# Windows:     .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"

# Verify migrations, lint, and 86 automated tests
ruff check . && ruff format --check .
alembic upgrade head
pytest tests/ -q

# Start FastAPI server (SQLite archived pilot by default; set ADMIN_API_KEY in env for /api/v1/admin/*)
uvicorn sensorguard.api:app --reload
# API docs: http://localhost:8000/docs

# Start Next.js 15 frontend (separate terminal)
cd apps/web
npm ci
npm run lint && npm run typecheck && npm run dev
# Dashboard: http://localhost:3000
```

### Containerized Private Staging (`docker-compose.yml`)

```bash
cp .env.example .env   # REQUIRED: set strong POSTGRES_PASSWORD and ADMIN_API_KEY (no defaults)
docker compose up --build
# PostgreSQL runs on internal Docker network only (no host port exposure)
# FastAPI API bound to 127.0.0.1:8000 · Frontend bound to 127.0.0.1:3000
```

### Reproduce Benchmarks & Model Artifacts

```bash
python scripts/run_real_benchmarks.py            # → docs/audit/benchmark_results.json (+ LOSO CV & time blocks)
python scripts/run_sage_ablations.py             # → docs/audit/sage_ablation_results.json (3 seeds × 5 conditions)
python scripts/train_pilot_model.py              # → artifacts/models/sage_pilot_q1_2024.joblib + manifest
python scripts/benchmark_network_concurrency.py  # → docs/audit/network_benchmark_results.json
```

---

## Project Structure & Documentation

| Document | Purpose |
|---|---|
| [`docs/RESEARCH_REPORT.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/RESEARCH_REPORT.md) | Paper-style research report with exact dataset balance, LOSO CV, and honest negatives |
| [`docs/PORTFOLIO_DEMO.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/PORTFOLIO_DEMO.md) | Verified resume bullet & 2-minute walkthrough script |
| [`docs/MILESTONE_6_1_RELEASE_AUDIT.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/MILESTONE_6_1_RELEASE_AUDIT.md) | Final CI, security, scientific, and GO/NO-GO production readiness audit |
| [`docs/EXPERIMENTS.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/EXPERIMENTS.md) | Experiment registry, exact configs, and stop-rule evaluation |
| [`docs/PRD.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/PRD.md) · [`docs/SRS.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/SRS.md) | Product & software specifications |
| [`docs/DATA_DICTIONARY.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/DATA_DICTIONARY.md) | Schema tables, UK AURN station metadata, and count reconciliation |
| [`docs/NOVELTY_AUDIT.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/NOVELTY_AUDIT.md) | Prior work boundaries & open comparison gaps |
| [`docs/SECURITY.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/SECURITY.md) · [`docs/MODEL_ROLLBACK.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/MODEL_ROLLBACK.md) | Secret hygiene, auth boundaries, and SHA-256 governance |
| [`docs/FRONTEND_API_CONTRACT.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/FRONTEND_API_CONTRACT.md) | Next.js ↔ FastAPI REST contract |

---

## Credential & Data Attribution

- Copy `.env.example` to `.env` locally; `.env` is excluded by `.gitignore`. Never commit secrets.
- `ADMIN_API_KEY` is required for `/api/v1/admin/*` (API fails closed with `503` if unset).
- Attribution: © UK Defra / AURN via OpenAQ v3 under Open Government Licence v3.0.
