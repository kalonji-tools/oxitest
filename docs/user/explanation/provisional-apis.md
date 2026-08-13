# Provisional APIs

Some oxitest plugin protocols are marked **provisional**. This means:

- They are fully functional and part of the public API
- They may change in **minor** releases (e.g., 1.1.0, 1.2.0) without a major version bump
- They will be graduated to **stable** once exercised by a real plugin

## Currently provisional

| Protocol | Reason |
|----------|--------|
| `Collector` | Untested; signature may need config or fixture registry access |
| `LogBackend` | Untested; may not fit structured logging libraries |
| `AsyncBackend` | Untested; coupled to async orchestrator internals. Shape changed in 2.2.0 (see CHANGELOG) |
| `DebuggerBackend` | Untested; coupled to pdb interface assumptions |

## Stable protocols

| Protocol | Since |
|----------|-------|
| `FixtureProvider` | 1.0.0 |
| `ExecutionWrapper` | 1.0.0 |
| `Reporter` | 1.0.0 |
| `CoverageProvider` | 1.0.0 |

`FixtureProvider` was reviewed for retirement and kept.
[ADR-0009](https://github.com/kalonji-tools/oxitest/blob/main/docs/adr/0009-fixture-system-redesign.md)
Rule 8 lists it, and that entry records the decision and the reason. It stays
stable surface.

## Graduation policy

A provisional protocol is graduated to stable when:

1. At least one real plugin implements it successfully
2. No design issues are identified during that implementation
3. The graduation is announced in the changelog
