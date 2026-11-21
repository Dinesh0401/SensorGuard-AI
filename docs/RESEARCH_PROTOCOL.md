# SensorGuard AI — research protocol (pre-registration draft)

## Claim boundaries

- Null: no statistically credible benefit from proposed sensitivity-aware handling versus strong existing baselines.
- Alternative: meaningful improvement in worst-regime empirical coverage or coverage/width/abstention frontier under *specified* missingness assumptions.
- No unconditional predictive guarantees under unrestricted MNAR. Avoid misinterpreting synthetic hidden truth as live-observed data.

## Dataset plan

Begin with synthetic scenarios to validate the experiment machinery. Before using OpenAQ: audit station count, PM2.5 units, timestamp semantics, observation versus receipt time, provider rights/quotas, time coverage and geographic availability. Identify a pilot region only after inspection. Use immutable raw extracts and a dataset manifest with source, retrieval time, checksum, and transformations. Reject leakage from future weather revisions and delayed uploads.

## Evaluation

- Split by unique target timestamp (train first 60%, calibration next 20%, test final 20%). Train and calibrate only with labels genuinely observed by the time used for fitting.
- Keep masked pollution values from the model and feature builder. Separate evaluator-only truth for simulated failure scenarios.
- Compare persistence, ridge and future seasonal/quantile/conformal baselines under exactly matched splits and missingness levels.
- Metrics: full-population empirical coverage (simulated evaluator), published-only coverage, unconditional MAE, interval width, abstention fraction, worst-stratum coverage gap, runtime.
- Scenarios: MCAR, MAR, MNAR with varied gamma, burst outage, regional outage, and delayed arrival. Test across multiple seeds/regions.
- Calibrate on available labels only; explicitly describe selection bias. Validate on unseen periods, stations, and eventually cities. Report confidence intervals.

## Known v0.1 limitations

1. Illustrative synthetic PM2.5 generation; environmental validity not established.
2. Feature weather values are synthetic and observed on schedule; only pollutant readings are masked.
3. Chronological split alone does not establish independence or conformal validity.
4. Missingness-aware calibration has **not** been implemented; only standard split conformal is present.
5. Freshness-based abstention is a heuristic, not an empirically optimized SAGE policy.
6. No real data ingested, trained serving model stored, database, frontend, or public deployment.
7. Last-observation forecast features require contiguous hourly timelines; real feeds require reindexing and time-availability checks.

## Originality audit

Related research already exists in missing-value conformal prediction, sensor dropout, sparse spatial forecasting and MNAR sensitivity analysis. A future SAGE implementation must contribute a narrowly measurable advance and compare against the relevant published baselines rather than rebranding standard calibration.
