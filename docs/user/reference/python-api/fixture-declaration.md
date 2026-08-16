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
| `"process"` | The process — a worker, or the coordinator | At process exit |

The ladder is **not** ordered by strength of guarantee. `"package"` is exactly
once per run and pays for it in parallelism; `"process"` constrains the
scheduler not at all and guarantees one instance per process instead — at most
`1 + N` for `-n N`, the `1` being the coordinator when an inprocess or arranged
test resolves it. It is the only tier whose instance count you set directly:
change `-n` and the number changes, with no edit to any declaration. Work that
must happen exactly once per run belongs at `"package"` in the rootdir package.

!!! note "Renamed from `session` in 3.1"
    [#1777](https://github.com/kalonji-tools/oxitest/issues/1777) renamed this
    tier from `lifetime="session"` and gave it genuine per-process semantics.
    It previously meant once per *task group* — a single module unless some
    `"package"` declaration merged a subtree — so the old name promised a
    boundary the implementation did not have. `"session"` is no longer accepted;
    `@oxi.fixture(lifetime="session")` raises
    `ValueError: 'session' is not a valid Lifetime`.

For guidance on choosing between them, see
[Use fixtures](../../how-to/use-fixtures.md#choose-a-lifetime).

## The lifetime cap

A declaration may not claim a lifetime wider than its own site's boundary
(ADR-0009 Rule 4). Exceeding the cap is a declaration error, raised while the
declaring module is registered.

| Declaration site | Widest legal lifetime |
|---|---|
| Inline in a `test_*.py` | `"module"` |
| `__fixtures__.py` or `__init__.py` at package X | `"package"`, anchored at X |
| Either file in the rootdir package | `"package"`, or `"process"` |

`"process"` is legal **only** in the rootdir package. Declared lower down it
would outlive the subtree permitted to see it, which is the condition the cap
exists to prevent.

**The rootdir package** is the deepest directory containing every path you list
in `testpaths` that holds tests. If you declare no `testpaths`, it is the
deepest directory containing every test oxitest finds when it walks your
project. In a conventional layout that is `tests/`.

It can be a directory you never listed —
declaring `["tests/api", "tests/db"]` makes it `tests/` — and adding
`testpaths` to a project can move it, so a `"process"` declaration that was
legal before may need to move with it. The error message names the rootdir
package and how it was derived.

Two declared trees that share no ancestor below your project root — say
`["tests", "docs"]` — make the **project root** the rootdir package, since that
is their common ancestor. It never rises above your project: an absolute
`testpaths` entry pointing somewhere else cannot drag it out of the project, so
long as something you declared is inside. A project that declares its whole test
surface outside itself keeps its rootdir package out there with the tests.

## Autouse

`autouse=True` makes a declaration run for every test in its
[B1 boundary](#visibility) without being requested. The value is discarded
unless the test also asks for it, in which case both routes share one instance
rather than building twice.

The lifetime tier sets **how often** it runs, and that is a rate rather than a
boundary event: the build happens inside the first test that reaches the
boundary. So a setup failure is reported against that test, its cost lands in
that test's timing, and a boundary whose tests are all skipped never fires at
all. Where several autouse fixtures apply to one test, they run
widest-lifetime-first — `"process"`, then `"package"`, then `"module"`, then
`"function"` — so a narrower one can rely on a wider one having run.

One combination is refused: `autouse=True` with `lifetime="function"` on an
`async` factory. It would fire for the sync tests in its boundary too, which
cannot await it.

To opt a subtree out, declare a fixture of the same name **without** `autouse`
at a deeper anchor. The suppression is boundary-local — outside that subtree
the original still fires — and the registration notice reports it.

Worked examples are in
[Use fixtures](../../how-to/use-fixtures.md#run-fixtures-automatically-with-autouse).

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
dependencies against its own anchor. A violation written as a literal `fx.`
access is refused at collection, before any test runs; one oxitest cannot see
until it executes — `getattr(fx, name)` — is refused at access time. See the
errors below.

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
`@oxi.fixture` in a declaration file and the retired `Fixtures()` registrar were not
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
| `InternalError` | Decoration time | A recognised tier has no scope mapping. This is an oxitest bug, not a usage error in your code |
| `UsageError` | Registration time | An `async` factory declared `autouse=True` with `lifetime="function"`, or a declaration exceeding [the lifetime cap](#the-lifetime-cap). Every offender in the file is named by one run |
| `BoundaryError` | Access time, `fx` proxy | The fixture is outside the reading test's anchor chain |
| `FixtureNotFoundError` | Access time, either route | Nothing reachable declares it — and also what the `Fixture[T]` route reports for an out-of-anchor fixture, since it has no namespace segment to attribute the failure to |

Full entries, with the rendered messages, are in the
[error reference](../errors.md#fixture-errors).

## See also

- [Use fixtures](../../how-to/use-fixtures.md) — the how-to guide, with worked examples
- [Fixture types](fixture-types.md) — `Fixture[T]`, `FixtureRef[T]`, `Yields[T]`, `Fixtures`
- [`@arrange`](arrange.md) — run a fixture for its side effect without a parameter
- [Built-in fixtures](builtins.md) — the `fx.oxi` namespace
