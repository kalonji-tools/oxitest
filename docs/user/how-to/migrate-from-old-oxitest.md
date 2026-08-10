# Migrate from the old fixture API

oxitest retired its first fixture API. This page maps each retired form onto
its replacement, one row at a time.

If you are moving from pytest rather than from an older oxitest, read
[Migrate from pytest](migrate-from-pytest.md) instead.

## What replaces what

| Retired | Replacement |
|---|---|
| `fixtures = oxitest.Fixtures()` then `@fixtures.fixture` | `@oxi.fixture(lifetime=...)` in a declaration file |
| Fixtures in a `conftest.py` | Fixtures in a `__fixtures__.py`, an `__init__.py`, or inline in a test module |
| `@fixtures.fixture(shared=True)` | `@oxi.fixture(lifetime="package")`, or `lifetime="process"` in a rootdir package |
| `oxitest.Fixtures(name="db")` for a namespace | The anchor directory's name — `tests/db/__fixtures__.py` gives `fx.db` |
| `# oxitest: allow[registrar-in-test-module]` | Nothing. The check it suppressed is gone |
| `AutouseRegistrationError` | Nothing to catch. The same combination is still refused, and reported as a collection violation |

## Declare a fixture

Before:

```python title="conftest.py"
import oxitest

fixtures = oxitest.Fixtures()


@fixtures.fixture
def db() -> Database:
    return Database()
```

After:

```python title="tests/__fixtures__.py"
from oxitest import fixture


@fixture(lifetime="function")
def db() -> Database:
    return Database()
```

Three things change together:

- **The file.** `conftest.py` is an ordinary Python module now. oxitest does not
  scan it. Declarations go in a `__fixtures__.py`, an `__init__.py`, or inline
  in a `test_*.py`.
- **The decorator.** `@oxi.fixture` replaces `@<registrar>.fixture`. There is no
  registrar object any more.
- **The lifetime.** `lifetime` is required and has no default. The old bare
  `@fixtures.fixture` meant per-test, which is `lifetime="function"`.

## Choose a lifetime

| Old | New | Built |
|---|---|---|
| `@fixtures.fixture` | `lifetime="function"` | once per test |
| — | `lifetime="module"` | once per test module |
| `@fixtures.fixture(shared=True)` | `lifetime="package"` | once per anchor package, per run |
| — | `lifetime="process"` | once per worker process; only in a rootdir package |

`shared=True` had one meaning; `package` and `process` split it. Use `package`
when the value must be built exactly once for a subtree. Use `process` when one
per worker is right and you want the count to follow `-n`.

## Understand where a fixture is visible

This is the change most likely to surprise you.

A `conftest.py` fixture was registered **run-wide**: every test in the project
could use it, wherever it lived. A declared fixture is **anchored** to the
package holding its declaration file, and only tests in that package or below
can use it. That is the [B1 boundary](use-fixtures.md#understand-fixture-visibility-the-b1-boundary).

So a fixture that every test used from one root `conftest.py` needs its
declaration file at a directory that covers all of them.

Two fixtures with the same name in sibling packages no longer collide, because
neither is visible to the other's tests.

## Read a namespace

A namespace came from the registrar's variable or its `name=`. It comes from the
anchor directory now:

```
tests/db/__fixtures__.py   →   fx.db.conn
tests/api/__fixtures__.py  →   fx.api.conn
```

Directory-derived namespaces are **not** unique across a tree. `tests/api/v1/`
and `tests/admin/v1/` both give `v1`, so `fx.v1.conn` means whichever
declaration is visible from the reading test.

If the namespace's name matches a package segment beside it, the segment wins in
the shortcut form. Access the fixture without the qualifier from inside its own
anchor, or use the full path.
