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

## Amendment — a value that names something absent is an invalid request (#2185)

The decision above covers **deserialization** failures at `[tool.oxitest]`: an unknown field, a wrong type, a malformed value. Two settings deserialize perfectly and name something that does not exist. Neither this ADR nor [ADR-0014](0014-target-validation.md) reached them, and both exited `3`:

| Setting | Before | After |
|---|---|---|
| `plugins = ["absent_xyz"]` | 3 | **4** |
| `async_backend = "no_such_backend"` | 3 | **4** |

**A `[tool.oxitest]` value that deserializes correctly and names something absent is an invalid request, and exits 4.** This is ADR-0014's argument for a Target applied to the configuration surface this ADR already owns: a value naming something absent is a mistake in the request, whatever the request's spelling. Exit `3` is defined as a test file that could not be imported, a declaration inside one that was refused, or a strict violation — and a plugin that was never installed is none of the three.

The narrow scope is unchanged, and the amendment draws a line rather than widening the surface. A plugin that is **defective** — no `oxitest_plugin()`, an entry point that raises, a wrong return type — stays at `3`. That is the plugin author's bug, and exit 4 would tell the user to correct a `pyproject.toml` that is already correct. `PluginNotFoundError` carries the first case and votes; `PluginLoadError` carries the second and does not.

Eleven of `plugin_loader.py`'s thirteen raise sites are the second kind, which is why one verdict for the whole class was refused: it would have been wrong about eleven sites or about two.

`ImportError.name` decides which case a failed import is. It reports the **first absent segment**, so for a plugin named `pkg.sub` whose `pkg` is not installed it holds `pkg` — an equality test would read that plugin as installed. The predicate matches the name or a dotted prefix of it.
