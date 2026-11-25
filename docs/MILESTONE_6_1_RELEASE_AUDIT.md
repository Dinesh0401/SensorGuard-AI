# SensorGuard AI — Milestone 6.1 Final CI, Research & Release Audit

**Audit Date:** 2026-11-25  
**Branch:** `feat/milestone-3-forecasting-uncertainty`  
**Benchmark ID:** `SG-BENCH-20261125-040900`  
**Ablation ID:** `SG-SAGE-ABLATION-20261124-162613`  
**Dataset Hash:** `33805c42097fc86e`  
**Release Verdict:** **NO-GO for Public Production Launch · GO for Private Staging & Portfolio Demonstration**

---

## 1. Release Readiness Scorecard

| Audit Area | Verification Performed | Outcome | Remaining Production Blocker |
|---|---|---|---|
| **Priority A — Frontend CI & Deps** | Configured non-interactive `.eslintrc.json` + pinned `eslint@8.57.1` & `eslint-config-next@15.5.29`; patched `next` `15.3.9` → `15.5.29`, `postcss` `8.5.33` override, `sharp` `0.35.5` override; fixed TS syntax/type issues in `app/page.tsx` and `lib/api.ts`; added `npm audit --audit-level=high` & `typecheck` to CI | **PASS** (`0` npm audit vulns, `eslint` 0 errors/warnings, `tsc` clean, `next build` clean) | — (wait on remote GitHub Actions confirmation after push) |
| **Priority B — Research & Scientific Claims** | Fixed `2024-01-01` – `2024-03-31` dates across all docs; corrected UK AURN geography across all 8 stations; reconciled `14,556` raw DB obs vs `8,884 + 2,937 + 3,496` splits; separated linear-logit $\gamma$ from Rosenbaum $\Gamma$; removed theoretical MNAR coverage guarantees; disclosed `n=12` stale-history `0/12` published; added all 8 real LOSO folds | **PASS** (100% consistent with `docs/audit/` JSON & DB) | Direct empirical comparison vs. TMLR 2026; local input-dependent $D_{\text{sensitivity}}$ |
| **Priority C — Reproducibility** | Unified docs on `scripts/run_sage_ablations.py`; verified every reported metric against committed JSON outputs; aligned SRS/Data Dictionary/Security docs with codebase; committed `docs/M6_COMPLETE_REPORT.md` | **PASS** | — |
| **Priority D — Deployment Hardening** | Removed default passwords from `docker-compose.yml` & `.env.example`; removed Postgres host port exposure & bound API/Web to `127.0.0.1`; fixed `Dockerfile` (`psycopg2-binary`, `artifacts/`, `alembic/`, `docs/audit/`) & `apps/web/Dockerfile` (`nginx` static export); configured PostgreSQL SQLAlchemy pooling (`pool_size=20`, `max_overflow=10`, `pool_recycle=1800`, `pool_pre_ping=True`) + Alembic Postgres DDL check; updated `smoke_test.ps1` | **PASS (local/config)** | No live PostgreSQL soak cluster, TLS/reverse proxy, or external uptime alerting provisioned; `c=20` `/forecasts` p95 `988ms` requires multi-worker scaling |

---

## 2. Priority A — NPM Security & Frontend Fix Log

- **Root Cause of Prior CI Failure:** `apps/web/package.json` lacked `eslint` and `eslint-config-next`, causing `next lint` to fail non-interactively; additionally `apps/web/app/page.tsx` contained an unquoted key (`mnar_gamma_1.5`) that failed `tsc` and webpack compilation.
- **Resolved Vulnerabilities (`npm audit` `3` → `0` vulnerabilities):**
  - `next` `15.3.9` → `15.5.29` (remediated Critical RCE/SSRF/DoS CVEs within Next 15 branch — avoided blind `v16` breaking bump).
  - `postcss` `<=8.5.22` (nested inside `next` `8.4.31`, High severity) → resolved via `"postcss": "8.5.33"` override (`"$postcss"`).
  - `sharp` `<=0.35.5-rc.1` (High severity) → resolved via `"sharp": "0.35.5"` override.
- **Verified Clean Commands:** `npm ci` · `npm audit --audit-level=high` (`found 0 vulnerabilities`) · `npm run lint` (`eslint . --ext .ts,.tsx --max-warnings=0`) · `npm run typecheck` (`tsc --noEmit`) · `npm run build` (`next build` static export).

---

## 3. Priority D — Concurrent Latency, DB, Auth & Live Integration Checks

### 3.1 Live Network Socket Benchmark (`benchmark_network_concurrency.py`, v0.5.3)
Run on live Uvicorn (`127.0.0.1:8000`, `100` requests/tier, `1,600` total requests, `0.0%` errors) in [network_benchmark_results.json](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/docs/audit/network_benchmark_results.json):

| Endpoint | `c=1` p95 | `c=5` p95 | `c=10` p95 | `c=20` p95 | `c=1` Throughput |
|---|---:|---:|---:|---:|---:|
| `/health` | 13.66 ms | 54.60 ms | 184.20 ms | 425.19 ms | 91.4 req/s |
| `/api/v1/stations` | 9.90 ms | 34.36 ms | 126.74 ms | 306.48 ms | 114.1 req/s |
| `/api/v1/stations/1/observations` | 25.50 ms | 61.39 ms | 164.37 ms | 379.27 ms | 51.5 req/s |
| `/api/v1/stations/1/forecasts` | 30.89 ms | 169.89 ms | 486.81 ms | **988.32 ms** | 36.1 req/s |

**Interpretation:** Single-user latency meets `< 50 ms`, and `c=10` stays `< 500 ms`, but a single Uvicorn worker exceeds `500 ms` p95 at `c=20` concurrent requests (`988.32 ms` on `/forecasts`), requiring multi-worker ASGI deployment + PostgreSQL before multi-user production traffic.

### 3.2 PostgreSQL, Auth & Live Integration Verification
- **Python Deps:** `python -m pip check` → `No broken requirements found.`
- **PostgreSQL Configuration:** `create_db_engine("postgresql+psycopg2://...")` configures `pool_size=20`, `max_overflow=10`, `pool_recycle=1800`, `pool_pre_ping=True` (verified by unit test `test_postgres_engine_pool_configuration`), and `alembic upgrade head --sql` under Postgres dialect cleanly generates SQL migrations `001`–`004`. (`[postgres]` optional extra in `pyproject.toml` installs `psycopg2-binary` cleanly inside `Dockerfile`).
- **Smoke Test & Auth Boundaries (Live Uvicorn):** `.\scripts\smoke_test.ps1` passed **10/10** endpoints; unauthenticated `POST /api/v1/admin/ingest` failed closed with `503` when `ADMIN_API_KEY` unset (and returns `422`/`401` when configured, verified by pytest); CORS allows `http://localhost:3000` across all 10 live GET API calls while omitting CORS headers for untrusted origins.
- **Static Frontend (`apps/web/out`) & API Contract:** Served built HTML cleanly on `127.0.0.1:3000`; verified all 10 frontend API endpoints return `200` with schemas matching `apps/web/lib/api.ts`; disabled silent mock table fallback in live mode (`!isDemoMode`). **Interactive Chrome DevTools rendering was skipped** because remote debugging was not active on the host Chrome instance (`DevToolsActivePort` absent).

---

## 4. Final GO / NO-GO Production Decision

- **Private Staging & Portfolio Demo:** **GO.** Codebase, tests (`87 passed`), security scans (`0` npm audit vulns), research reports, negative results, and container specs are internally consistent and reproducible.
- **Public Production Deployment:** **NO-GO.** Remains gated on:
  1. Multi-worker Uvicorn + live PostgreSQL integration & soak testing (to pull `c=20` p95 below `500 ms`).
  2. External TLS reverse proxy with public GET rate limiting & monitoring alerts.
  3. Live Chrome UI visual verification once Chrome DevTools is available, and external dataset / TMLR 2026 validation prior to any research or public deployment claims.
