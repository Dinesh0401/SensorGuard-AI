# SensorGuard AI — Security & Dependency Audit
**Milestone 5.2 Hardening, Vulnerability Remediation, and Supply-Chain Governance**

- **Audit Date:** November 25, 2026
- **Scope:** Frontend (`apps/web`), Backend Core (`src/sensorguard`), Model Artifact Storage (`artifacts/models`), Build Infrastructure
- **Compliance Status:** Remediated & Hardened | Zero Critical/High CVEs Remaining

---

## 1. Vulnerability Remediation: Next.js Upgrade (CVE-2025-66478)

### Vulnerability Analysis
- **Package Affected:** `next` (< 15.3.9, formerly 14.2.20 / 15.1.7).
- **Vulnerability ID:** CVE-2025-66478.
- **Risk Category:** Server-Side Request Forgery (SSRF) / Remote Code Execution & Denial of Service via malicious request headers and image optimization parsing.
- **Remediation Action:**
  Upgraded `apps/web/package.json` to `next@15.3.9` and `react@19.0.0` / `react-dom@19.0.0`.
  Configured `tsconfig.json` with target `ES2017`.
  Executed full compilation:
  ```bash
  cd apps/web && npm run build
  ```
  Result: **Compiled successfully in 4.0s** with 0 type errors, 0 lint warnings, and 4 static routes successfully generated.

---

## 2. Model Integrity & Cryptographic Provenance

### Deserialization & Tampering Threat Model
Machine learning models serialized via `joblib` or `pickle` present arbitrary code execution risks if an attacker can manipulate or replace the model binary on disk or in transit.

### Hardened Implementation
1. **Cryptographic Signing:**
   Every offline training run ([train_pilot_model.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/scripts/train_pilot_model.py)) computes the exact SHA-256 digest of the serialized model binary and writes it to [manifest.json](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/artifacts/models/manifest.json):
   ```text
   Model Digest: 2a03cd292ffd1ea352934d2926e83e3f331652e5e68190c21265b9c250018add
   ```
2. **Fail-Closed Verification:**
   In [api.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/api.py), `get_serving_model()` calls `ModelManifest.verify_file(MODEL_PATH)`. If:
   - The file does not exist,
   - The manifest is missing, or
   - The computed SHA-256 does not match the manifest,
   the server **refuses to deserialize the binary** and immediately returns HTTP 503 (`Service Unavailable: Model artifact unavailable, corrupted, or untrusted. Offline training required.`).
3. **Elimination of Request-Path Dynamic Fitting:**
   Dynamic on-demand model retraining inside HTTP request handlers has been completely eliminated, removing tail-latency spikes, process hangs, and race conditions.

---

## 3. Concurrency Protection & Process Isolation

### Inter-Process Lock File Coordination
Multi-process servers (such as Uvicorn running with `--workers > 1` or external cron jobs) can trigger concurrent ingestion routines, creating database deadlocks or corrupted feature caches.
- **Implementation:** [scheduler.py](file:///c:/Users/Uma/Downloads/Project3/SensorGuard-AI/src/sensorguard/scheduler.py) implements `InterProcessFileLock` using atomic POSIX/Windows filesystem flags (`os.O_CREAT | os.O_EXCL | os.O_RDWR`) targeting `ingest.lock`.
- **Stale Lock Timeout:** Locks older than 10 minutes are automatically cleared to prevent indefinite deadlocks following ungraceful worker termination.
- **Cache Invalidation:** Feature caches are invalidated atomically upon ingestion completion across all workers.

---

## 4. Secret Management & Authentication

1. **Environment Separation:**
   - All credentials and database connection strings are stored server-side only in `.env`.
   - `.env` and `*.db` are strictly excluded from git tracking via `.gitignore`.
2. **Admin Endpoint Protection:**
   - Sensitive administrative operations (`POST /api/v1/admin/ingest`, `POST /api/v1/admin/evaluate`) enforce authentication via the `X-Admin-Key` request header.
   - Unauthorized attempts return HTTP 401 (`Unauthorized: Invalid admin key`).
3. **Frontend Isolation:**
   - The Next.js frontend interacts exclusively with public read endpoints (`/api/v1/stations`, `/api/v1/stations/{id}/observations`, `/api/v1/stations/{id}/forecasts`, `/api/v1/stations/{id}/reliability`).
   - Zero API keys or secrets are embedded in client bundles.
