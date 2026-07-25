# ADR-0008: Fail-closed on `[tool.oxitest]` parse errors, narrow scope

**Status:** Accepted
**Date:** 2026-07-25

`Config::load` historically caught any `toml::de::Error` from parsing pyproject.toml, emitted a WARN, and substituted the default config. A typo like `waivres = "..."` at `[tool.oxitest]`, a wrong-typed value, or a whole-file TOML syntax error all landed in the same soft-fallback path. Users upgraded, hit a rename or a keystroke slip, and their tests ran under defaults with no non-zero exit and a WARN buried in stderr — mistakes surfaced days later, if at all. This ADR ends that pattern for errors inside the `[tool.oxitest]` subtree.

**Decision.** Any deserialization error inside `[tool.oxitest]` — unknown field (via `deny_unknown_fields`), wrong type, malformed value — causes `Config::load` to exit `UsageError` (4) with `eprintln!("error: <path>: <toml::de::Error>")`. Errors outside `[tool.oxitest]` (a broken `[tool.ruff]`, whole-file TOML syntax) remain soft-fallback: warn and continue with defaults. Extraction is Value-based, mirroring `check_no_legacy_keys` — parse to `toml::Value`, extract `tool.oxitest`, deserialize that subtree specifically.

## Considered Options

1. **Fail-open with louder alarm** — keep soft-fallback but promote the WARN to a summary banner or diagnostic entry. Rejected: users who don't read stderr today won't read a banner tomorrow. Fail-open makes the guarantee unenforceable.

2. **Fail-closed on any pyproject parse failure** (broad scope) — hard-exit whenever `toml::from_str::<PyprojectToml>` fails, whether the failing span is inside `[tool.oxitest]` or in an unrelated table like `[tool.ruff]`. Rejected: oxitest is a test runner, not a TOML linter. A valid `[tool.oxitest]` block should not be blocked by a syntax error in someone else's tool configuration.

3. **Fail-closed with `--allow-config-drift` escape hatch.** Rejected: escape hatches mean the guarantee isn't real. Users learn about the flag, CI configs hardcode it "just in case", and we're back to soft-fallback with extra steps. No concrete use case named; the two-line rescue (`mv pyproject.toml pyproject.toml.broken && oxitest && mv back`) covers genuine emergencies.

4. **Fail-closed, narrow scope, no escape hatch** (chosen). Aligns with `check_no_legacy_keys`, which already hard-exits `UsageError` on named-legacy keys. Aligns with the loud-rejection DNA of [ADR-0006](0006-async-organizational-strategy.md) (async hard-break) and [ADR-0007](0007-none-by-exception.md) (None-by-exception). Narrow scope respects that we only own the `[tool.oxitest]` surface.

## Consequences

- **Breaking change for users with existing typos or removed fields at `[tool.oxitest]`.** A pyproject that runs green today can hard-fail on the next oxitest upgrade. Shipped as `feat!:` with a `BREAKING CHANGE:` trailer; git-cliff highlights the migration bullet in the release notes.
- **`deny_unknown_fields` on `OxitestConfig` becomes load-bearing.** Without it, unknown-field errors never reach the hard-exit path. `PyprojectToml` and `ToolTable` deliberately lack `deny_unknown_fields` (they carry `[project]`, `[build-system]`, `[tool.*]` siblings); doc comments on both structs record why they must stay open, preempting well-meaning "consistency" PRs that would break every user pyproject.
- **Migration hints for future purged fields remain a manual convention** on `check_no_legacy_keys`. When removing an `OxitestConfig` field with a migration path, add a `LegacyKey` variant so the user sees the specific hint rather than the generic `unknown field 'X'`. No machine-verified enforcement — the surface is small enough that discipline suffices.
- **`test_invalid_scope_surfaces_deserializer_error` gets tightened.** The docstring's "Until that decision is made" hedge — a placeholder from the #1602 rework — is deleted and the test asserts non-zero exit.
