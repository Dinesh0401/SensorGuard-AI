# Model Rollback Procedure

## Overview

SensorGuard API validates the pilot model artifact on startup using a SHA-256
digest stored in `EXPECTED_MODEL_SHA256` inside `src/sensorguard/api.py`.
If the digest does not match, the API **fails closed** (HTTP 503) rather than
silently serving predictions from an unverified model.

This document explains how to roll back to a previous model version or promote
a newly retrained model.

---

## Artefact files

| File | Purpose |
|---|---|
| `artifacts/models/sage_pilot_q1_2024.joblib` | Serialised SAGE model (joblib) |
| `artifacts/models/sage_pilot_q1_2024.sha256` | Hex digest of the `.joblib` file |
| `artifacts/models/manifest.json` | Human-readable metadata (version, hash, thresholds) |
| `src/sensorguard/api.py` → `EXPECTED_MODEL_SHA256` | Constant checked at startup |

All four must be consistent or the API will refuse to start.

---

## Rollback steps

### 1. Identify the target version

```bash
# list committed model manifests
git log --oneline -- artifacts/models/manifest.json
```

Pick the commit SHA for the version you want to restore.

### 2. Restore the artefact files from git

```bash
git checkout <commit-sha> -- artifacts/models/sage_pilot_q1_2024.joblib \
                             artifacts/models/sage_pilot_q1_2024.sha256 \
                             artifacts/models/manifest.json
```

### 3. Read the SHA-256 of the restored file

```powershell
# PowerShell
(Get-FileHash artifacts\models\sage_pilot_q1_2024.joblib -Algorithm SHA256).Hash.ToLower()
```

```bash
# bash/Linux
sha256sum artifacts/models/sage_pilot_q1_2024.joblib
```

### 4. Update the constant in api.py

Edit `src/sensorguard/api.py`:

```python
EXPECTED_MODEL_SHA256 = "<paste-the-sha256-from-step-3>"
```

### 5. Verify consistency

```bash
python - <<'EOF'
import hashlib, pathlib
path = pathlib.Path("artifacts/models/sage_pilot_q1_2024.joblib")
sha = hashlib.sha256(path.read_bytes()).hexdigest()
stored = pathlib.Path("artifacts/models/sage_pilot_q1_2024.sha256").read_text().strip()
print("file sha256  :", sha)
print("stored sha256:", stored)
print("match        :", sha == stored)
EOF
```

### 6. Run tests

```bash
python -m pytest tests/ -q
```

### 7. Commit and push

```bash
git add artifacts/models/ src/sensorguard/api.py
git commit -m "chore(model): roll back to version <tag>"
git push origin feat/milestone-3-forecasting-uncertainty
```

---

## Promoting a newly retrained model

```bash
# 1. Retrain
python scripts/train_pilot_model.py

# 2. Get the new SHA-256 (the script prints it at the end)
# 3. Update EXPECTED_MODEL_SHA256 in src/sensorguard/api.py
# 4. Verify & commit (steps 5-7 above)
```

---

## Safety rules

- **Never commit a model without also committing its `.sha256` and updating `manifest.json`.**
- **Never update `EXPECTED_MODEL_SHA256` to match a file you have not inspected.**
- The API will **not** fall back to training a replacement at serving time; it fails
  closed. This is intentional — silent on-demand training would mask data-pipeline
  failures.
- Keep at least the previous two model versions as named tags in git so a one-step
  rollback is always possible.
