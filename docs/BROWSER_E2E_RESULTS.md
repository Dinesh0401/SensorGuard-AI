# SensorGuard AI — Browser End-to-End Test Results

**Milestone:** 7.3 — Browser End-to-End Validation Against Live FastAPI + PostgreSQL 18 + Built UI  
**Date:** 2026-11-25  
**Branch:** `feat/milestone-3-forecasting-uncertainty`  
**Frontend Bundle:** `apps/web/out` (Next.js `15.5.29` static export served on `http://127.0.0.1:3000`)  
**Backend API:** Live Uvicorn (`src/sensorguard/api.py`) connected to `PostgreSQL 18.0` (`127.0.0.1:5433/sensorguard`)  
**Browser Runner:** Playwright `@playwright/test` v`1.65.1` driving headless Google Chrome (`channel: "chrome"`)  

---

## 1. Execution Summary

| Check | Command | Outcome | Notes |
|---|---|---|---|
| **TypeScript Typecheck** | `npm --prefix apps/web run typecheck` | **PASS** | Zero `tsc --noEmit` errors including Playwright suite |
| **ESLint (`--max-warnings=0`)** | `npm --prefix apps/web run lint` | **PASS** | Zero warnings/errors across `.ts` and `.tsx` |
| **NPM Vulnerability Audit** | `npm --prefix apps/web audit --audit-level=high` | **PASS** | `0` vulnerabilities across `371` installed packages |
| **Playwright E2E Suite** | `npm --prefix apps/web run test:e2e` | **3 / 3 PASSED (`11.5s`)** | Executed live against Uvicorn + PostgreSQL 18 + built `apps/web/out` |

---

## 2. Verified Browser User Flows (`apps/web/e2e/dashboard.spec.ts`)

1. **Station Catalog, Outage Gaps, Forecast Intervals & Sensitivity Slices (`3.5s`)**
   - Verified `"FastAPI Connected"` live connection status and `"STRICT PILOT MODE: ARCHIVED Q1 2024 UK AURN NETWORK"` disclosure without triggering demo fallback.
   - Confirmed all **8 UK AURN pilot stations** render in the station selector (`Harlington`, `Hillingdon`, `Marylebone Road`, `Bexley - Slade Green`, `Southampton Centre`, `Wicken Fen`, `St Osyth`, `Manchester Piccadilly`).
   - Verified historical reliability panel (`Total Observations`, `Missingness / Gap Rate`, `Max Consecutive Outage`), forecast table columns (`Target UTC`, `D_sens`, `Policy`, `Reason`), explicit non-filled gap indicator (`24 timesteps (gaps not filled)`), and scalar `D_sens = 3.32`.
   - Clicked `Manchester Piccadilly - UKA00248 (#2312)` and `Bexley - Slade Green (#157)` and verified live `200 OK` `/api/v1/stations/{id}/forecasts` reloads and coordinate header updates (`Lat: 53.4815`).
   - Exercised `Sensitivity Slice:` selector across `"2"`, `"0"`, and `"envelope"`.
   - Asserted zero page exceptions, zero failed network requests, zero `>= 400` API responses, and zero non-favicon console errors (`404 /favicon.ico` explicitly ignored because Next.js static export omits `favicon.ico`).

2. **Simulator Lab Regime Switching & Scientific Disclosures (`2.4s`)**
   - Awaited live `200 OK` `/api/v1/research/experiments` fetch on load and opened `"Simulator Lab & Ablations"`.
   - Verified manifest badges (`SG-BENCH-20261125-040900`, `SG-SAGE-ABLATION-20261124-162613`), benchmark rows (`lightgbm_quantile`, `sage_full`), ablation rows (`full_sage`), and initial `"NATURAL OBSERVED"` state (`97.02%` published Persistence PICP).
   - Switched through `mnar_gamma_1.5` (confirming `"SIMULATED OUTAGE"` banner + `96.11%` regime transition), `burst_24h`, and back to `natural`.
   - Opened `"SAGE Methodology"` tab and verified explicit non-guarantee disclosure renders cleanly.

3. **Demo Fixture vs Live API Isolation (`3.3s`)**
   - Verified clicking `"Toggle Demo Mode"` disconnects live badge (`FastAPI Connected` absent) and reveals explicit Demo notice; toggling back restores `"FastAPI Connected"`.
   - In a separate browser context with `/api/**` and `/health**` aborted (`connectionrefused`), confirmed the UI never displays `"FastAPI Connected"` and immediately surfaces an explicit offline/demo notice.
