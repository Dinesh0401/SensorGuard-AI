# SensorGuard AI — Agent instructions

- Read `SensorGuard_Project03_Blueprint.md` and `Antigravity_Prompts.md` before coding.
- Operate in plan → implement one milestone → run tests → report → human review. Do not begin the next milestone without confirmation.
- Never fabricate data, dataset availability, evaluated results, coverage guarantees, research novelty or live readings.
- Always distinguish sensor observed_at, fetched_at/received_at, forecast issued_at and target_at; reject any future leakage.
- Never equate no recent upstream report to confirmed physical sensor failure. Track reason and source status explicitly.
- Never claim prediction coverage under unrestricted missing-not-at-random data without assumptions or a valid identification strategy.
- Preserve ground-truth values in evaluator-only storage for masked simulation; model training/inference must never see them.
- Keep an honest baseline comparison with same train/calibration/test splits, metrics, and compute budgets; add ablations and negative results.
- Check providers' attribution, rights, and quotas. Store secrets server-side only and exclude `.env` from Git.
- No destructive commands, paid infrastructure, GitHub pushes, public deployment, or external communications without explicit approval.
- For every milestone report files, test commands/results, blockers, dataset lineage, assumptions, and recommended git commit message.
