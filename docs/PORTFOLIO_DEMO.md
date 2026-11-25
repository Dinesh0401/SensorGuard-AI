# SensorGuard AI — Portfolio Demo

## Resume Bullet

> **SensorGuard AI** — Built a full-stack air-quality forecasting system (FastAPI · Next.js · PostgreSQL) featuring SAGE, a novel conformal prediction algorithm that handles non-random sensor missingness via a Gamma-sensitivity model and guarded abstention policy, achieving 99.9% published coverage on 8 UK AURN stations with sub-0.5% abstention under natural conditions.

---

## 2-Minute Demo Script

### Slide / screen sequence and talking points

---

### [0:00–0:20] Hook — the problem

**Show:** Live dashboard map of 8 UK stations (London to Manchester)

> "Air-quality sensors fail silently — and they fail *more* during pollution events, the exact times we most need data. Standard forecasting methods assume missingness is random. It isn't. SensorGuard AI is my solution."

---

### [0:20–0:50] Architecture overview

**Show:** README architecture diagram or the Docker Compose diagram

> "The system has four layers:
> 1. A PostgreSQL database storing real AURN measurements  
> 2. A FastAPI backend that serves predictions via a REST API  
> 3. A Next.js frontend with a live simulator lab  
> 4. A SAGE model — Sensitivity-Aware Guarded Estimation — that I designed to be robust to non-random data gaps."

---

### [0:50–1:20] The algorithm — live in the UI

**Show:** Simulator Lab tab → Scenario selector: switch from `natural` → `mnar_gamma_1.5`

> "Here's what makes SAGE different. I calibrate prediction intervals under four different assumptions about *how bad* the missingness is — Gamma values from 0 (random) to 1.5 (strongly non-random). The *disagreement* between those four scenarios is my uncertainty signal.

> When I switch to the MNAR Gamma=1.5 scenario — simulating sensor failures correlated with high pollution — watch the abstention rate climb from 0.4% to 1.1%, and published coverage holds above 99.9%. SAGE selectively withholds predictions when the data-gap mechanism would make them unreliable."

---

### [1:20–1:40] Honest evaluation — the benchmark table

**Show:** Research tab → Benchmark comparison table

> "And here's the honest part. Under normal conditions, a simple Persistence baseline — just copying yesterday's reading — achieves 97% coverage with intervals only 2 µg/m³ wide. SAGE gets 99.9% coverage but with 17 µg/m³ intervals.

> I could have buried that number. Instead it's in the report and in the UI. SAGE's value is *specifically* in high-pollution, stale-data conditions — where the stale-history stratum shows 67% coverage for Persistence vs 100% for SAGE."

---

### [1:40–2:00] Engineering highlights + call to action

**Show:** GitHub Actions CI badge → `86 passed` · model status badge showing SHA-256 verification

> "86 automated tests, green CI, model artefact SHA-256 verification, CORS-enabled API, Docker Compose deployment, and an MLflow experiment registry. The full research report with reproducibility instructions is in the repo.

> If you work on environmental monitoring, sensor reliability, or uncertainty quantification — I'd love to talk."

---

## Key Numbers to Know

| Metric | Value |
|---|---|
| Test observations | 2,727 (of 3,496 total) |
| SAGE published coverage (natural) | 99.93% |
| SAGE abstention rate (natural) | 0.44% |
| SAGE abstention rate (MNAR Gamma=1.5) | 1.09% |
| Persistence MPIW | 2.00 µg/m³ |
| SAGE MPIW | 17.00 µg/m³ |
| Stale-history coverage — Persistence | 66.67% |
| Stale-history coverage — SAGE | 100.00% |
| Automated tests | 86 passed |
| Stations | 8 UK AURN |
| Dataset period | Q1 2024 |

---

## Potential Interview Questions & Suggested Answers

**Q: Why not just use a wider split-conformal interval?**  
A: You could inflate a split-conformal interval to match SAGE's coverage, but then you lose the semantic meaning: a wider interval from SAGE specifically reflects disagreement across MNAR assumptions, which is actionable information. The abstention policy surfaces that signal without inflating every interval.

**Q: What is the Gamma-sensitivity model?**  
A: It's a classic partial-identification model (Rosenbaum 2002) that bounds how much the missing-data odds ratio can deviate from 1. Gamma=1 is MAR. Gamma=1.5 means a sample can be 1.5× more likely to be missing because of its own value than because of observed covariates.

**Q: Why does coverage exceed the 90% nominal?**  
A: The sensitivity envelope uses the worst-case quantile across all Gamma values. This is a conservative construction — it over-covers. That's the right trade-off for public health applications where under-coverage is dangerous.

**Q: How would you deploy this in production?**  
A: The Docker Compose file and MODEL_ROLLBACK.md document the full path. The API validates the model SHA-256 on startup and fails closed if the artefact is missing or corrupted — no silent fall-back to on-demand training. The scheduler worker handles incremental data ingestion on a cron schedule.
