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

## Amendment — the index keys on the Binding Type, the qualifier matches the Provided Type (#2094)

A yield fixture declares `Yields[T]`, which is exactly `Generator[T, None, None]`. It **provides** `T`. The two are different types. The type index keys on the first; the qualifier comparison matches on the second.

`FixtureDef` therefore carries `is_generator` and derives `provides`. Only `FixtureRegistry.resolve`'s qualifier branch reads `provides`. `_by_type` is unchanged.

### Why the index is not unwrapped

Indexing a yield fixture under `T` is the obvious correction, and it is wrong. It was built and measured, and it fails:

```
python/tests/test_dependency_teardown_boundary.py::test_plugin_fixture_survives_the_first_test
AmbiguousFixtureError: 2 fixtures provide type 'Conn': 'conn', 'owner'
```

A module-lifetime `owner() -> Iterator[Conn]` then joins `_by_type[Conn]` beside a plugin fixture that provides `Conn`. The rescue fails because `FixtureSession.get_fixture_by_type` disambiguates with `qualifier=t.__name__` — the *type's* name — and no fixture carries it.

A narrower variant that unwraps only `Generator` and `AsyncGenerator` passes the whole suite. It passes **because it does not unwrap `Iterator`**, which is the annotation on the fixture that regresses. Its green run is a false negative, not evidence.

**Do not enlarge `_by_type`.** Every failure measured while deciding this traces to an enlarged index. `python/tests/test_provided_type.py::test_the_binding_type_of_a_yield_fixture_is_not_unwrapped` pins the invariant.

### The consequence for the domain model

`CONTEXT.md` previously defined **Binding Type** as *"the type a fixture provides, used as the primary key for resolution"*. For a yield fixture those clauses name different types, and the registry implemented the definition literally. The glossary now separates **Binding Type** from **Provided Type**; that separation is the fix, and the code follows it.
