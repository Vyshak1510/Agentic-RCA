# Rollout Gates

## Gate Thresholds

- Top-3 hit rate >= 65%
- Top-1 hit rate >= 35%
- Unsupported-claim rate < 5%
- p95 first RCA latency < 10 minutes

## Rollout Pattern

1. Shadow mode: no external publishing.
2. 100% human adjudication for first two weeks.
3. Assisted mode only after sustained gate pass.

## Release Blocking

CI must block releases if replay regression fails thresholds.
