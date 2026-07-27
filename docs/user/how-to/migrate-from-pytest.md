# Migrate from pytest

!!! abstract "How-to"
    Switch an existing pytest project to oxitest with minimal changes.

## What works unchanged

The following pytest features are supported out of the box:

- Plain `test_*` functions with `assert` statements
- `oxitest.skip()` and `unittest.SkipTest`
- Standard test file patterns (`test_*.py`, `*_test.py`)

## Use fixtures with oxitest

oxitest has its own fixture system. In `conftest.py` files, create a `Fixtures` instance
and use it as a decorator:

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

`Fixtures.fixture` accepts `autouse`, `name`, and `shared`. See [Understand fixture scoping](#understand-fixture-scoping) below for the pytest→oxitest scope mapping. You can create multiple `Fixtures()` instances in one `conftest.py`; all are discovered automatically.

Fixture teardown uses `yield` or `TestContext` with `ctx.addfinalizer()`:

```python
from oxitest import TestContext

@fx.fixture
def tmp_file(ctx: TestContext):
    path = make_temp_file()
    ctx.addfinalizer(lambda: path.unlink())
    return path
```

!!! tip "Migration step"
    Replace `@pytest.fixture` declarations with a `Fixtures()` instance in each `conftest.py`.
    See [Use fixtures](use-fixtures.md) for full details.

## Understand fixture scoping

pytest has five fixture scopes (`function`, `class`, `module`, `package`, `session`). oxitest has two: **function** (per-test, default) and **session** (once per session, via `shared=True`). Every pytest scope maps to one of the two:

| pytest scope | oxitest equivalent | Notes |
|---|---|---|
| `function` (default) | `@fx.fixture` | Direct 1:1 |
| `class` | `@fx.fixture` (per-test) — no shortcut | Class-shared state needs a per-test fixture reused via helper functions |
| `module` | `@fx.fixture(shared=True)` — but see below | Semantically **wider**: what was module-bounded becomes session-bounded |
| `package` | `@fx.fixture(shared=True)` | Same widening as module |
| `session` | `@fx.fixture(shared=True)` | Direct 1:1 |

### When the module → session collapse hurts

For most fixtures that were `scope="module"` — per-module DB connections, loaded config, compiled schemas — the shift to `shared=True` is neutral or a slight win: the resource persists longer and cost amortizes across more tests.

**The collapse hurts when the fixture mutates process-global state.** A per-module reset of process globals under pytest becomes a session-long override under `shared=True`, silently affecting tests in other modules whose expectations may differ. Watch for:

- `os.umask` changes
- `os.chdir` context
- `sys.path` munging
- environment-variable overrides shared across a group of tests
- global logging configuration
- global socket / DNS / networking hooks

**Recommended pattern for process-global state:** convert the fixture into a plain `@contextlib.contextmanager` helper and invoke it explicitly at the block scope where the state actually matters:

```python
# BEFORE — pytest, scope="module"
@pytest.fixture(scope="module", autouse=True)
def set_umask():
    default = os.umask(0)
    yield
    os.umask(default)


# AFTER — oxitest, explicit block scope
import contextlib
from collections.abc import Iterator


@contextlib.contextmanager
def cleared_umask() -> Iterator[None]:
    default = os.umask(0)
    try:
        yield
    finally:
        os.umask(default)


def test_permissions(tmp: TempDir) -> None:
    with cleared_umask():
        # umask change is bounded to this block
        ...
```

You lose autouse ergonomics — every affected test must open the `with` — but you gain visible, bounded scope. For process-global state that could contaminate other modules, this is usually the right trade.

See also [Use fixtures → Understand fixture scoping](use-fixtures.md#understand-fixture-scoping) for the oxitest-native treatment of the two scopes.

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
- [Use markers](use-markers.md) — `@oxitest.mark.*` decorators
- [Use parametrize](use-parametrize.md) — named keyword-argument cases
- [Configuration reference](../reference/configuration.md) — all `[tool.oxitest]` keys
