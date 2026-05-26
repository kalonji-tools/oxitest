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

`Fixtures.fixture` accepts `autouse`, `name`, and `shared`. Use `shared=True` instead of
`scope="session"` — a shared fixture is created once per session and immutable. You can
create multiple `Fixtures()` instances in one `conftest.py`; all are discovered automatically.

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

Built-in marks — `skip`, `skipif`, `xfail`, `timeout` — work without registration.

## Use parametrize

oxitest uses `@oxitest.parametrize` (a first-class decorator, not a mark) with keyword
arguments as named cases. See [Use parametrize](use-parametrize.md) for full details.

```python
from dataclasses import dataclass
import oxitest

@dataclass(frozen=True)
class AddCase:
    x: int
    y: int
    expected: int

@oxitest.parametrize(
    basic=AddCase(x=1, y=2, expected=3),
    negative=AddCase(x=-5, y=3, expected=-2),
)
def test_add(x: int, y: int, expected: int) -> None:
    assert x + y == expected
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

   | pytest flag            | oxitest flag           |
   |------------------------|------------------------|
   | `-k EXPR`              | `-k EXPR`              |
   | `-m EXPR`              | `-m EXPR`              |
   | `-v`                   | `-v`                   |
   | `-x`                   | `-x`                   |
   | `--maxfail N`          | `--maxfail N`          |
   | `--tb short\|line\|no` | `--tb short\|line\|no` |

   Example GitHub Actions step:

   ```yaml
   - name: Run tests
     run: oxitest --tb short -v
   ```

## See also

- [Use fixtures](use-fixtures.md) — oxitest's typed fixture system
- [Use markers](use-markers.md) — `@oxitest.mark.*` decorators
- [Use parametrize](use-parametrize.md) — named keyword-argument cases
- [Configuration reference](../reference/configuration.md) — all `[tool.oxitest]` keys
