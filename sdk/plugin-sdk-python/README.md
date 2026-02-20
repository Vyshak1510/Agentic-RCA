# RCA Plugin SDK (Python)

Connector plugins must implement the connector interface and expose an entry point under `rca.connectors`.

## Required methods

- `discover_context(alert, service_identity)`
- `collect_signals(plan_step)`
- `normalize_evidence(raw_payload)`
- `healthcheck()`

## Contract guarantees

- Read-only behavior only in v1
- Capability declaration in manifest is required
- Compatibility versioning enforced via `sdk_version`
