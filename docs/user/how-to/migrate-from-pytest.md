# Migrate from pytest

!!! abstract "How-to"
    Switch an existing pytest project to oxitest with minimal changes.

## What works unchanged

The following pytest features are supported out of the box:

- Plain `test_*` functions with `assert` statements
- `oxitest.skip()` and `unittest.SkipTest`
- Standard test file patterns (`test_*.py`, `*_test.py`)

## Use fixtures with oxitest

pytest's fixture home is `conftest.py`. oxitest's is a file named
**`__fixtures__.py`**, placed in the package that holds the tests using it.
There is no registry object to create and nothing to import into your tests —
a decorated function in that file is a fixture.

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/__fixtures__.py:declare-fixture"
```

`@pytest.fixture` has an optional `scope`; `@oxi.fixture` has a **required**
`lifetime`, with no default. Teardown still hangs off `yield`, exactly as in
pytest:

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/__fixtures__.py:module-lifetime"
```

Injection is the other change you will feel immediately. pytest matches a test
parameter to a fixture **by name**; oxitest injects only parameters annotated
`Fixture[T]`, and matches **by type**, with the parameter name used to break
ties between fixtures returning the same type. An unannotated parameter is
never injected.

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/test_api.py:injection-access"
```

Imperative teardown is available too — annotate a parameter with `TestContext`
and register callbacks with `ctx.addfinalizer()`.

### Translate `scope=` to `lifetime=`

| pytest `scope=` | oxitest | Note |
|---|---|---|
| `"function"` (default) | `lifetime="function"` | Direct equivalent. Rebuilt per test. |
| `"class"` | *no equivalent tier* | oxitest has no class-level boundary. Use `"function"`, or `"module"` when the class is the whole file. |
| `"module"` | `lifetime="module"` | Direct equivalent. Disposed after the module's last test. |
| `"package"` | `lifetime="package"` | Exactly once per run — and it collapses the declaring directory's subtree onto a single worker, so it costs parallelism. |
| `"session"` | `lifetime="package"` in the rootdir package | This is the tier that is genuinely once per run. Do **not** reach for `lifetime="process"`, which is once per *process* — as many instances as you have workers — and is not a singleton. |

The lifetime you may declare is also capped by where you declare it — a fixture
written inline in a `test_*.py` cannot exceed `"module"`. See the
[fixture declaration reference](../reference/python-api/fixture-declaration.md)
for the full table.

### Fixture visibility maps almost directly

A pytest fixture in `tests/api/conftest.py` is visible to `tests/api/` and
below, and nowhere else. `@oxi.fixture` uses the same rule, keyed on the
directory holding the `__fixtures__.py` — so a pytest suite whose fixtures
already sit at the right level ports without restructuring.

Two differences are worth knowing before you hit them:

- **The failure is named.** Reaching a fixture outside your directory raises
  `BoundaryError` with the stable code `fixture-boundary`, which says the
  fixture exists elsewhere — rather than pytest's "fixture not found", which
  reads like a typo.
- **The rootdir catch-all is not automatic.** pytest loads every `conftest.py`
  on the walk-up path. oxitest resolves a `@oxi.fixture` against its anchor
  directory, so a fixture every test needs belongs in a `__fixtures__.py` at
  the rootdir package, not merely somewhere above the test.

See [the B1 boundary](use-fixtures.md#understand-fixture-visibility-the-b1-boundary)
and the [error reference](../reference/errors.md#fixture-errors).

## See also

- [Migrate from the old fixture API](migrate-from-old-oxitest.md) — moving off `Fixtures()` and `conftest.py`
- [Use fixtures](use-fixtures.md) — oxitest's typed fixture system
- [Fixture declaration reference](../reference/python-api/fixture-declaration.md) — `@oxi.fixture`, declaration homes, and the lifetime cap
- [Use markers](use-markers.md) — `@oxitest.mark.*` decorators
- [Use parametrize](use-parametrize.md) — named keyword-argument cases
- [Configuration reference](../reference/configuration.md) — all `[tool.oxitest]` keys
