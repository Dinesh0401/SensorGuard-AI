# SensorGuard AI — Portfolio Demo & Walkthrough

## Resume Bullet

> **SensorGuard AI** — Engineered a full-stack air-quality forecasting and uncertainty-evaluation platform (FastAPI · Next.js 15 · PostgreSQL/SQLite · MLflow) across 8 UK AURN stations (Q1 2024, 14,556 hourly PM₂.₅ observations), implementing point-in-time feature replay, six conformal baselines (Split, CQR, Rolling, ACI), SAGE parametric logit sensitivity enveloping with staleness abstention, SHA-256 fail-closed model serving, and CI/CD (86 Python tests + Next.js lint/typecheck/build).

---

## 2-Minute Demo Script

### [0:00–0:20] Hook — The Evaluation Problem
**Show:** Map view of all **8 UK AURN pilot stations** (spanning Greater London, Southampton, Wicken Fen, St Osyth, and Manchester Piccadilly — clearly labeled *Archived Q1 2024 UK AURN Network*).

> "Environmental sensors fail non-randomly. If you evaluate forecasters only on intact readings, you miss periods when history goes stale or drops out during pollution episodes. SensorGuard AI separates real historical measurements from causal simulated outages and exposes uncertainty intervals honestly."

### [0:20–0:50] Engineering & Provenance Architecture
**Show:** Station detail view + Footer model badge (`SHA-256 Verified`) + Architecture diagram.

> "Every observation separates physical `observed_at`, upstream `received_at`, and `fetched_at` timestamps to block future lookahead. Behind a Next.js 15 static UI sits a CORS-configured FastAPI service with fail-closed SHA-256 model verification, constant-time admin auth, PostgreSQL pooling config, and Alembic schema migrations."

### [0:50–1:25] Simulator Lab & Sensitivity Analysis
**Show:** Simulator Lab tab → toggle `natural` → `mnar_gamma_1.5` → `burst_24h`.

> "SAGE fits a covariate propensity model `P(R=1|X)` and sweeps a parametric logit sensitivity grid across `γ ∈ {0, 0.5, 1, 1.5, 2}`, taking the union envelope and checking staleness (`>12h`), effective sample size, and inter-scenario spread. In MNAR simulations (`γ = 1.5`), SAGE retains `99.96%` published coverage with `0.95%` single-seed (`1.09%` 3-seed) freshness abstention."

### [1:25–2:00] Honest Scientific Benchmarking & Negative Results
**Show:** Benchmark comparison table + LOSO discussion in [`docs/RESEARCH_REPORT.md`](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/RESEARCH_REPORT.md).

> "Just as important are the honest negative results documented in our benchmark: in calm conditions, Persistence + Split Conformal gets `96.9%` coverage at `2.0 µg/m³` width (`Winkler 3.16`), and LightGBM + CQR achieves `99.9%` coverage at `11.35 µg/m³` (`Winkler 11.45`) — outperforming SAGE's conservative `17.01 µg/m³` width (`Winkler 17.06`). SAGE only improves coverage over CQR on the anomalous held-out Station 4 LOSO fold (`90.7%` vs `81.7%`), still at higher Winkler loss (`13.23` vs `11.46`). All 86 tests, npm audit (`0` vulns), ESLint, TypeScript, and Next.js build pass in CI."

---

## Verified Key Figures (Never Exaggerated)

| Fact | Verified Value | Caveat / Context |
|---|---|---|
| Geography & Dates | 8 UK AURN stations, `2024-01-01` – `2024-03-31` | Greater London (4), Southampton, Wicken Fen, St Osyth, Manchester |
| Raw valid DB observations | `14,556` (`8,884` train + `2,937` cal + `2,727` test reported + `8` warmup) | `3,496` test grid rows = `2,727` valid + `769` missing |
| Natural benchmark (`SG-BENCH-20261125-040900`) | Persistence `96.88%` (`MPIW 2.00`), CQR `99.89%` (`MPIW 11.35`), SAGE `99.93%` (`MPIW 17.01`) | SAGE over-covers via conservative bounds; CQR & Persistence win on sharpness |
| Active Q1 2024 abstentions | `12 / 2,727` (`0.44%`) natural; `26 / 2,727` (`0.95%`) MNAR seed 42 (`1.09%` 3-seed) | Driven by `age_hours > 12h` freshness guard (`D_sens = 3.32 < 8.0`); stale-history `n=12` natural had `0` published forecasts |
| 8-fold LOSO CV (`natural`) | CQR wins Winkler on 8/8 stations; `station_4` coverage: SAGE `90.72%` vs CQR `81.72%` | No formal MNAR finite-sample guarantee claimed |
| Test & Security checks | `86 passed` pytest, `0` npm audit vulnerabilities | Private staging prototype — not deployed to public production |
