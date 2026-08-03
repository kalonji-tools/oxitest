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
| `"session"` | `lifetime="package"` in the rootdir package | This is the tier that is genuinely once per run. Do **not** reach for `lifetime="process"`, which is once per worker task group and is not a singleton. |

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

### Legacy: mapping onto `Fixtures()` in `conftest.py`

!!! warning "Supported, but no longer the route to migrate onto"
    oxitest still reads `conftest.py` and still supports `Fixtures()`. Both are
    scheduled for removal in
    [#1720](https://github.com/kalonji-tools/oxitest/issues/1720). A migration
    starting today should target `__fixtures__.py`; this section is here for
    suites already part-way through against the older API.

```python
# conftest.py
import oxitest

fx = oxitest.Fixtures()

@fx.fixture
def db():
    conn = connect_db()
    yield conn
    conn.close()

@fx.fixture(shared=True)
def app():
    return create_app()
```

`Fixtures.fixture` accepts `autouse`, `name`, and `shared`. You can create
multiple `Fixtures()` instances in one `conftest.py`; all are discovered
automatically.

`shared=True` is the nearest legacy analogue of `scope="session"`, but it is not
a synonym: a shared fixture is built once per **task group** — a single test
module unless a `lifetime="package"` declaration merges a subtree — and its
value is frozen, so any attribute or item write raises
`SharedFixtureMutationError`.

Fixtures declared this way are registered **run-wide** and are exempt from the
boundary above — more permissive than pytest, not less
([#1760](https://github.com/kalonji-tools/oxitest/issues/1760)). A suite that
migrates to `conftest.py` first can therefore acquire cross-directory fixture
uses that pytest never allowed, and that break again on the move to
`__fixtures__.py`. Migrating straight to `__fixtures__.py` avoids the round
trip.

## Use markers

oxitest uses `@oxitest.mark.<name>` for all markers. Custom marks must be registered in
`pyproject.toml`. See the [Use markers](use-markers.md) guide for full details.

```toml
[tool.oxitest]
markers = ["slow: marks tests as slow", "integration"]
```

```python
import oxitest

@oxitest.mark.slow
def test_heavy():
    ...
```

Built-in marks — `skip`, `xfail`, `timeout` — work without registration.

!!! note "skipif replaced by skip(when=...)"
    pytest's `@pytest.mark.skipif(condition, reason="...")` becomes
    `@oxitest.mark.skip(when=condition, reason="...")`. The `skipif` mark
    does not exist in oxitest.

## Use parametrize

oxitest uses `@oxitest.parametrize` (a first-class decorator, not a mark) with keyword
arguments as named cases. See [Use parametrize](use-parametrize.md) for full details.

```python
--8<-- "python/tests/docs/how-to/test_parametrize.py:dataclass-expanded"
```

Unlike pytest's list-of-tuples style, oxitest parametrize uses named keyword arguments —
the key becomes the test ID (e.g. `test_add[basic]`).

## Steps

1. **Install oxitest.**

   ```console
   $ pip install oxitest
   ```

2. **Run your tests.**

   Replace your usual `pytest` call with `oxitest`:

   ```console
   $ oxitest
   ```

   oxitest discovers tests by the same file and function naming conventions as pytest,
   so no renaming is required.

3. **Migrate configuration (optional).**

   Add a `[tool.oxitest]` section in `pyproject.toml`:

   ```toml
   [tool.oxitest]
   testpaths = ["tests"]
   ```

   oxitest reads only `[tool.oxitest]` — it does not fall back to `[tool.pytest.ini_options]`.

4. **Update CI commands.**

   Find every place your CI pipeline calls `pytest` and replace it with `oxitest`. Flags
   with direct equivalents:

   | pytest flag            | oxitest equivalent              |
   |------------------------|---------------------------------|
   | `-k EXPR`              | `-E 'name(EXPR)'`              |
   | `-m EXPR`              | `-E 'mark(EXPR)'`              |
   | `-v`                   | `-v`                            |
   | `-x`                   | `-x`                            |
   | `--maxfail N`          | `--maxfail N`                   |
   | `--tb short`           | `--tb detail` (default)         |
   | `--tb long`            | `--show-internals`              |
   | `--tb line\|no`        | `--tb line\|no`                 |

   Example GitHub Actions step:

   ```yaml
   - name: Run tests
     run: oxitest -v
   ```

## See also

- [Use fixtures](use-fixtures.md) — oxitest's typed fixture system
- [Fixture declaration reference](../reference/python-api/fixture-declaration.md) — `@oxi.fixture`, declaration homes, and the lifetime cap
- [Use markers](use-markers.md) — `@oxitest.mark.*` decorators
- [Use parametrize](use-parametrize.md) — named keyword-argument cases
- [Configuration reference](../reference/configuration.md) — all `[tool.oxitest]` keys
