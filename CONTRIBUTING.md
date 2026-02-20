# Contributing

## Development Principles

- Keep connectors read-only in v1.
- Every RCA claim must be backed by citation IDs.
- Provider-neutral keys and canonical service identity are mandatory.
- Preserve backward compatibility for plugin SDK contracts.

## Setup

```bash
make setup
make test
```

## Pull Requests

- Include tests for behavior changes.
- Update docs and API contracts when interfaces change.
- Keep changes scoped and explain tradeoffs in PR description.

## Versioning

This project follows SemVer. Breaking changes require a documented deprecation window.
