# oxitest

**A fast, typed, and explicit Python test framework backed by a Rust runner.**

oxitest rewrites the slow parts of test infrastructure in Rust while keeping your test code in pure Python. Collection, scheduling, parallelism, caching, and reporting are all handled by the Rust core; your fixtures, marks, and assertions stay in the Python you already know.

## Why oxitest?

- **Explicit injection** — only parameters annotated with `Fixture[T]` are injected. No magic name-matching.
- **Typed fixtures** — fixture return types flow through to the test, so your type checker works end-to-end.
- **Parallel by default** — the Rust scheduler spawns worker subprocesses and distributes tests automatically.
- **Fast startup** — the Rust binary starts in milliseconds; Python is only loaded per worker, not per test.
- **Strict mode** — opt-in checks for bare asserts, dict parametrize, and marks missing a `reason`.

## Quick start

Install oxitest into your project:

```console
$ pip install oxitest
```

Declare a fixture in a `__fixtures__.py` beside your tests. `lifetime` says
which unit of the test run disposes the value, and has no default — you always
say it:

```python
--8<-- "python/tests/docs/tutorials/first_fixture/__fixtures__.py:declare-fixture"
```

Write a test that asks for it:

```python
from oxitest import Fixture

--8<-- "python/tests/docs/tutorials/first_fixture/test_first_fixture.py:use-fixture"
```

Run it:

```console
$ oxitest tests/
```

## Next steps

!!! tip "Next steps"
    - [Migrate from pytest](how-to/migrate-from-pytest.md) — coming from pytest? Start here.
    - [Getting started tutorial](tutorials/getting-started.md) — a full walkthrough from installation to your first fixture
    - [How-to guides](how-to/use-fixtures.md) — task-oriented recipes for fixtures, markers, parametrize, and more
    - [Reference](reference/cli.md) — CLI flags, configuration keys, exit codes, and JSON output format
    - [Why Rust + Python](explanation/why-rust-python.md) — the architectural reasoning behind oxitest
