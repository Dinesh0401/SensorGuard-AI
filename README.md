# SensorGuard AI

**Project 03 · ML/MLOps Research Flagship · v0.5.3**

> **Investigating:** Can we preserve useful forecast calibration when public sensors fail in non-random patterns?

[![Python CI](https://github.com/Dinesh0401/SensorGuard-AI/actions/workflows/ci.yml/badge.svg?branch=feat%2Fmilestone-3-forecasting-uncertainty)](https://github.com/Dinesh0401/SensorGuard-AI/actions/workflows/ci.yml)

> **Status (M5.3 complete):** Research prototype evaluated on real UK AURN PM₂.₅ data (Q1 2024, 8 stations).
> Live data ingestion is **opt-in** (requires `OPENAQ_API_KEY` and `ENABLE_LIVE_PROVIDER_FETCH=true`).
> Do **not** interpret research results as public health advice.

---

## What SAGE does

**SAGE** (Sensitivity-Aware Guarded Estimation) is a conformal prediction algorithm designed for air-quality forecasting under non-random sensor missingness (MNAR):

1. **Gamma-sensitivity calibration** — calibrates a prediction interval for each assumption in a user-specified MNAR sensitivity grid (Gamma ∈ {0, 0.5, 1.0, 1.5}).
2. **Sensitivity envelope** — publishes the union of all scenario intervals, providing conservative but MNAR-robust coverage.
3. **Disagreement signal** — computes the spread of interval widths across Gamma values as an uncertainty signal.
4. **Guarded abstention** — withholds the forecast when disagreement exceeds a threshold (τ = 8 µg/m³), preferring abstention to publishing an unreliable interval.

**Key results** (benchmark `SG-BENCH-20261125-040900`, dataset hash `33805c42097fc86e`):

| Metric | Value |
|---|---|
| Published coverage (natural regime) | **99.93%** |
| Abstention rate (natural) | 0.44% |
| Abstention rate (MNAR Gamma=1.5) | 1.09% |
| Stale-history stratum — Persistence coverage | 66.7% |
| Stale-history stratum — SAGE coverage | **100.0%** |
| Automated tests | **86 passed** |

**Honest trade-off:** Under calm natural conditions, Persistence + Split Conformal achieves 96.9% coverage with MPIW = 2.0 µg/m³ (vs. SAGE's 17.0 µg/m³). SAGE's advantage is specifically in high-pollution and stale-data conditions.

---

## Quick start

### Python environment (research / local development)

```bash
git clone https://github.com/Dinesh0401/SensorGuard-AI.git
cd SensorGuard-AI
python -m venv .venv
# Windows:   .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"

# Run all tests (86 pass)
pytest -q

# Start the API (needs DATABASE_URL in .env)
cp .env.example .env   # then edit .env
alembic upgrade head
uvicorn sensorguard.api:app --reload
# API docs: http://localhost:8000/docs
```

### Docker Compose (all services)

```bash
cp .env.example .env   # fill in ADMIN_API_KEY and POSTGRES_PASSWORD
docker compose up --build
# API:      http://localhost:8000/docs
# Frontend: http://localhost:3000
```

### Reproduce benchmark results

```bash
python scripts/run_real_benchmarks.py    # → docs/audit/benchmark_results.json
python scripts/run_sage_ablation.py     # → docs/audit/sage_ablation_results.json
```

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Next.js frontend (apps/web, port 3000)         │
│  • Station map  • Forecast chart                │
│  • Simulator Lab (live scenario selector)       │
└──────────────────┬──────────────────────────────┘
                   │ REST (CORS-enabled)
┌──────────────────▼──────────────────────────────┐
│  FastAPI backend (src/sensorguard/api.py, 8000) │
│  • /api/v1/stations  /forecast/{id}             │
│  • /api/v1/model/status  (SHA-256 verified)     │
│  • /api/v1/research/experiments                 │
└──────────┬────────────────┬───────────────────── ┘
           │                │
┌──────────▼───┐    ┌───────▼───────────────────── ┐
│  PostgreSQL  │    │  SAGE model artefact          │
│  (port 5432) │    │  artifacts/models/*.joblib    │
└──────────────┘    │  (SHA-256: e552ca09…)         │
                    └────────────────────────────── ┘
```

---

## Project structure

```
SensorGuard-AI/
├── src/sensorguard/          # Python package
│   ├── api.py                # FastAPI application (v0.5.3)
│   ├── models/
│   │   ├── sage.py           # SAGE algorithm
│   │   └── data.py           # Feature engineering + leakage guards
│   ├── calibration.py        # Conformal calibration
│   ├── ablation.py           # Ablation study runner
│   ├── evaluation.py         # Benchmark evaluator
│   └── scheduler.py          # Background ingestion worker
├── apps/web/                 # Next.js frontend
├── scripts/
│   ├── train_pilot_model.py  # Model training
│   ├── run_real_benchmarks.py
│   ├── run_sage_ablation.py
│   └── smoke_test.ps1        # API smoke test
├── tests/                    # 86 automated tests
├── docs/
│   ├── RESEARCH_REPORT.md    # Paper-style technical report
│   ├── PORTFOLIO_DEMO.md     # Resume bullet + 2-min demo script
│   ├── MODEL_ROLLBACK.md     # Model governance
│   └── audit/                # Committed benchmark & ablation JSON
├── artifacts/models/         # Trained model + SHA-256 manifest
├── docker-compose.yml        # Multi-service deployment
└── .env.example              # Environment variable reference
```

---

## MLOps maturity (completed milestones)

| Milestone | Status |
|---|---|
| M0: Synthetic foundation + CI | ✅ Done |
| M1: Real data pipeline (OpenAQ) | ✅ Done |
| M2: Six baseline forecasters | ✅ Done |
| M3: Conformal calibration (CQR, ACI, Rolling) | ✅ Done |
| M4: SAGE algorithm + ablation | ✅ Done |
| M4.1: Evaluation-mask correctness audit | ✅ Done |
| M5: FastAPI + MLflow + scheduler | ✅ Done |
| M5.1: Disagreement signal fix + fail-closed API | ✅ Done |
| M5.2: CORS, model SHA-256 verification | ✅ Done |
| M5.3: Frontend lab rewrite, retrained model | ✅ Done |
| **M6: Research report + portfolio demo** | ✅ **Done** |

---

## Scientific integrity

- All benchmark numbers come directly from committed artefacts in `docs/audit/`; none are fabricated.
- Honest negative results are reported: Persistence + SplitConformal outperforms SAGE on sharpness under calm conditions.
- MNAR is non-identifiable from observed data; we simulate MNAR to study robustness, not to prove the data is MNAR.
- Conformal coverage guarantees are **marginal**; conditional coverage (per station or pollution level) is not guaranteed.
- Full limitations are documented in [RESEARCH_REPORT.md](docs/RESEARCH_REPORT.md) §10.

---

## Documentation

| Document | Description |
|---|---|
| [docs/RESEARCH_REPORT.md](docs/RESEARCH_REPORT.md) | Full paper-style technical report |
| [docs/PORTFOLIO_DEMO.md](docs/PORTFOLIO_DEMO.md) | Resume bullet + 2-min demo script |
| [docs/MODEL_ROLLBACK.md](docs/MODEL_ROLLBACK.md) | Model version governance |
| [docs/FRONTEND_API_CONTRACT.md](docs/FRONTEND_API_CONTRACT.md) | API ↔ frontend type contract |
| [docs/BASELINE_COMPARISON.md](docs/BASELINE_COMPARISON.md) | Baseline method comparison |
| [docs/audit/benchmark_results.json](docs/audit/benchmark_results.json) | Full benchmark JSON |
| [docs/audit/sage_ablation_results.json](docs/audit/sage_ablation_results.json) | Full ablation JSON |

---

## Credential configuration

Copy `.env.example` to `.env` and fill in:
- `DATABASE_URL` — PostgreSQL connection string
- `ADMIN_API_KEY` — required for ingest and admin endpoints
- `OPENAQ_API_KEY` — only needed with `ENABLE_LIVE_PROVIDER_FETCH=true`

Never commit `.env`. See [.env.example](.env.example) for all variables.
