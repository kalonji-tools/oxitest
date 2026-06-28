# ADR-0002: Unified fixture backend with type-based resolution

**Status:** Accepted
**Date:** 2026-06-28

oxitest had three separate fixture mechanisms — conftest (name-based, `FixtureDef` + `FixtureRegistry`), plugins (`FixtureProvider` protocol, type-based, no registry), and builtins (`BuiltinFixture` enum, type-based, class variable registry). We unified all three into a single `FixtureDef` + `FixtureRegistry` with type-based resolution as the primary strategy and parameter name as a qualifier for disambiguation.

## Considered Options

1. **Keep the three-system split, unify storage only.** Would solve the immediate inspect graph completeness problem but leave two inconsistent resolution strategies (name vs type) and three separate lifecycle contracts. Each new feature (dependency tracking, scope management, provenance) would need to handle three code paths.

2. **Unify storage and resolution, keeping name-based for conftest.** Would reduce storage to one registry but preserve the odd-one-out: builtins and plugins resolve by type, conftest by name. Parameter names remain load-bearing for conftest fixtures, and `Fixture[DBSession]` means something different depending on source.

3. **(Chosen) Type-based resolution for all sources, name as qualifier.** All fixtures resolve through `Fixture[T]` → type match → qualifier disambiguation. Single `FixtureDef` with a source variant (`ConftestSource | PluginSource | BuiltinSource`) preserves lifecycle differences without leaking them into the registry. DI container pattern with multi-index map (by-type primary, by-name secondary).

## Consequences

- **Parameter names are no longer load-bearing** for conftest fixtures when the binding type is unique. `def test(db: Fixture[DBSession])` works regardless of fixture name. Names become qualifiers only when multiple fixtures share a type.
- **`Fixture[Any]` / `Fixture[object]`** falls back to qualifier-only resolution (non-strict) or errors (strict mode `"abort"`). This is a soft deprecation of loose annotations.
- **`FixtureProvider` protocol gains `scope` and `autouse` properties** — backwards-compatible via `getattr` defaults.
- **Scope vocabulary standardized** to three tiers: `"each"`, `"shared"`, `"session"` across all sources.
- **`CollectedItem.fixture_names`** replaced by `fixture_deps: tuple[(str, str), ...]` carrying `(qualifier, binding_type_name)` pairs. Cache version bump.
- **`depends_on` on `FixtureDef`** stores `tuple[(str, type), ...]` (injection points), resolved to graph edges by the graph builder.
- **Override precedence**: builtins (lowest) → plugins → root conftest → leaf conftest (highest). A conftest fixture can override a builtin or plugin fixture of the same type.
