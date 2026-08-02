# Fixture declaration

!!! abstract "Reference"
    The `@oxi.fixture` decorator — where a declaration may live, what lifetime
    it may claim, and which tests can see it.

[Fixture types](fixture-types.md) covers the annotations that *consume* a
fixture. This page covers the other half: the decorator that *declares* one.

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/__fixtures__.py:declare-fixture"
```

## `@oxi.fixture`

::: oxitest.fixture
    options:
      show_source: false
      heading_level: 3

## Where a declaration may live

The Rust prescan reads three kinds of file. A fixture placed anywhere else —
`helpers.py`, `utils.py`, a plain module — is never scanned, so it is dead code
rather than a half-registered fixture (ADR-0009 Rule 1).

| File | May declare | Notes |
|------|-------------|-------|
| `__fixtures__.py` | any lifetime | The general home. |
| `__init__.py` | any lifetime | Conventional home for package-lifetime declarations. Its presence is **not** what defines a package boundary — any directory is a package. |
| `test_*.py` | `function`, `module` | **Inline.** Visible only inside that one module, including to sibling files in the same directory. |

A declaration file is only registered if the directory holding it also holds a
collected test file — see
[the caveat in the how-to guide](../../how-to/use-fixtures.md#declare-a-fixture).

## Lifetime tiers

`lifetime` is keyword-only and required. It names the code-structural unit
whose exit disposes the value.

| `lifetime` | Boundary | Disposed |
|---|---|---|
| `"function"` | The individual test | After that test |
| `"module"` | The test module | After the module's last test |
| `"package"` | The declaring directory's subtree | After the subtree's last test |
| `"session"` | The worker task group | At task-group teardown |

The ladder is **not** ordered by strength of guarantee. `"package"` is exactly
once per run and pays for it in parallelism; `"session"` constrains the
scheduler not at all and therefore guarantees correspondingly little — a task
group is a single module unless some `"package"` declaration merges a subtree,
so in a suite with no `"package"` declaration `"session"` behaves exactly like
`"module"`. It is the only tier whose instance count is set by another tier's
declarations. Work that must happen exactly once per run belongs at
`"package"` in the rootdir package.

!!! note "`session` is being reworked"
    [#1777](https://github.com/kalonji-tools/oxitest/issues/1777) has decided to
    rename this tier to `lifetime="process"` and give it genuine per-process
    semantics. That has not shipped — `"session"` is the spelling `@oxi.fixture`
    accepts today, with the semantics described above.

For guidance on choosing between them, see
[Use fixtures](../../how-to/use-fixtures.md#choose-a-lifetime).

## The lifetime cap

A declaration may not claim a lifetime wider than its own site's boundary
(ADR-0009 Rule 4). Exceeding the cap is a declaration error at prescan time.

| Declaration site | Widest legal lifetime |
|---|---|
| Inline in a `test_*.py` | `"module"` |
| `__fixtures__.py` or `__init__.py` at package X | `"package"`, anchored at X |
| Either file in the rootdir package | `"package"`, or `"session"` |

`"session"` is legal **only** in the rootdir package. Declared lower down it
would outlive the subtree permitted to see it, which is the condition the cap
exists to prevent.

## Anchors and namespaces

A fixture's **anchor** is the directory holding its declaration file — or, for
an inline declaration, the test module itself. The anchor does two jobs: it
bounds visibility, and its basename becomes the fixture's namespace segment in
`fx.<namespace>.<name>`. Nothing names either explicitly; move the file and both
move with it.

Because the namespace is a basename, namespaces are **not unique across a
tree**. That is legal: no test can see two anchors that derive the same one.

## Visibility

A fixture is usable only by tests in its anchor package or a descendant of it
(ADR-0009 Rule 3, the "B1 boundary"), and the same rule governs a fixture's own
dependencies against its own anchor. Violations are refused at access time —
see the errors below.

The rules, the worked tree, and the rendered diagnostic are in
[Use fixtures](../../how-to/use-fixtures.md#understand-fixture-visibility-the-b1-boundary).

## How a `Fixture[T]` parameter resolves

A `Fixture[T]` parameter is matched to a fixture by the **parameter's name**. `T`
is checked, but it never selects.

```python
# __fixtures__.py
@oxi.fixture(lifetime="function")
def db_schema() -> str: ...
```

```python
def test_a(db_schema: Fixture[str]) -> None: ...  # resolves
def test_b(schema: Fixture[str]) -> None: ...     # refused: nothing is named `schema`
```

`test_b` is refused **at collection**, before any test executes, however well its
type matches. The same name-based pass validates both declaration routes — a
`@oxi.fixture` in a declaration file and a legacy `Fixtures()` registrar are not
matched differently.

!!! note "Why the name and not the type"
    Name matching is what holds the [B1 boundary](#visibility) closed. Fixtures
    *are* indexed by return type internally, and that index carries no visibility
    filtering — so a parameter that could resolve by type alone would let a test
    reach a fixture anchored in a package it cannot see, just by naming its type
    ([#1768](https://github.com/kalonji-tools/oxitest/issues/1768)).

Fixtures you do **not** declare are the exception. A built-in is injected by its
own type annotation (`tmp: TempDir` — see [built-in fixtures](builtins.md)), and a
plugin-provided fixture resolves through its provider's type, so neither depends
on what you call the parameter.

## Errors

| Error | Raised | When |
|---|---|---|
| `ValueError` | Decoration time | `lifetime` is not one of the four tier names |
| `UsageError` | Decoration time | A recognised tier has no scope mapping — an oxitest bug, not a usage error in your code |
| `BoundaryError` | Access time, `fx` proxy | The fixture is outside the reading test's anchor chain |
| `FixtureNotFoundError` | Access time, either route | Nothing reachable declares it — and also what the `Fixture[T]` route reports for an out-of-anchor fixture, since it has no namespace segment to attribute the failure to |

Full entries, with the rendered messages, are in the
[error reference](../errors.md#fixture-errors).

## See also

- [Use fixtures](../../how-to/use-fixtures.md) — the how-to guide, with worked examples
- [Fixture types](fixture-types.md) — `Fixture[T]`, `FixtureRef[T]`, `Yields[T]`, `Fixtures`
- [`@arrange`](arrange.md) — run a fixture for its side effect without a parameter
- [Built-in fixtures](builtins.md) — the `fx.oxi` namespace
