# SensorGuard AI

**Project 03 · ML/MLOps research flagship · Synthetic research foundation (v0.1)**

Investigating: **Can we preserve useful forecast calibration when public sensors fail in nonrandom patterns?**

> **Status:** Research prototype using entirely synthetic PM2.5-like values. There is **no live monitoring**, no validated environmental forecast, and **no guarantee** of prediction-interval coverage under arbitrary missing-not-at-random (MNAR) outages. Do not interpret synthetic data as public health advice.

## What actually works

- Reproducible multi-station hourly synthetic observations with separate evaluator-only truth.
- Random (MCAR), observed-condition-dependent (MAR), hidden-target-dependent (MNAR), and burst outage generators.
- Point-in-time feature processing: pollutant and weather values shifted to avoid target-time leakage.
- Chronological 60%/20%/20% train/calibration/test partition; training and calibration labels must be reported.
- Two baseline predictors (persistence and Ridge regression) with standard split-conformal uncertainty.
- Explicit freshness-based publish / caution / abstain demo policy (heuristic, not a calibrated guarantee).
- Empirical coverage, interval width, MAE, selective coverage, and per-status evaluations.
- Read-only OpenAQ v3 client stub requiring explicit provider credentials (not used by default).
- Read-only FastAPI demonstration, offline tests, Dockerfile, CI, and research documentation.

## Quick start

Python 3.11+:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest -q
python -m sensorguard.cli run-demo --mechanism mnar --seed 17 --output artifacts/benchmark.json
uvicorn sensorguard.api:app --reload
```

Open `http://localhost:8000/docs`; `GET /v1/demo/experiment?mechanism=mnar` returns **synthetic** benchmark results. No credentials or network connection are needed for tests or synthetic studies.

## Credential configuration

Set `OPENAQ_API_KEY` only when running the optional read-only provider adapter. Copy `.env.example` to a local `.env` and load it using your own shell or secret manager. The application **does not automatically load** `.env` or contact OpenAQ. Never commit or share your key. See [credentials](docs/CREDENTIALS.md).

## MLOps roadmap

1. **Now:** reproducible experiments, honest data boundaries, tests, CI, and API.
2. **Next:** OpenAQ location feasibility audit, source license/quotas, observed/received timestamp schema, safe historical replay, and persisted immutable inputs.
3. **Then:** temporal/geospatial baselines, stronger calibration methods, simulated regional outages, contamination checks, MLflow experiment tracking.
4. **Research:** compare sensitivity-aware policy vs strong missingness-aware baselines, complete uncertainty/width/abstention ablations, publish negative results.
5. **Product:** PostgreSQL, jobs, monitoring, dashboard, deployment, alerting, privacy and abuse review.

See [research protocol](docs/RESEARCH_PROTOCOL.md) and the original [project blueprint](docs/PROJECT_BLUEPRINT.md).

## Scientific integrity

The synthetic generator creates an illustrative association between PM2.5 and covariates; it is **not a validated atmospheric model**. All numeric results are synthetic. MNAR is generally non-identifiable from observed data alone. Standard conformal coverage requires exchangeability, which may fail under time dependence or informative outage patterns. Evaluating all synthetic truths is only possible because the simulation retains an isolated truth table; a real sensor outage does not provide such truth. No originality or superiority claim is made for the placeholder SAGE research direction.

## Engineering conventions

Python functions have docstrings and explicit types where practical; frontend TypeScript/TSDoc will be introduced when the UI milestone begins. Preserve main via pull requests; do not commit credentials, datasets with unknown rights, or unreviewed deployment configuration.
