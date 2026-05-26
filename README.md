# oxitest

[![CI](https://github.com/kalonji-tools/oxitest/actions/workflows/ci.yml/badge.svg)](https://github.com/kalonji-tools/oxitest/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/kalonji-tools/oxitest/branch/main/graph/badge.svg)](https://codecov.io/gh/kalonji-tools/oxitest)
[![PyPI version](https://img.shields.io/pypi/v/oxitest)](https://pypi.org/project/oxitest/)
[![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://kalonji-tools.github.io/oxitest/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**A fast, typed, and explicit Python test framework backed by a Rust runner.**

oxitest rewrites the slow parts of test infrastructure in Rust while keeping
your test code in pure Python. Collection, scheduling, parallelism, caching,
and reporting are all handled by the Rust core; your fixtures, marks, and
assertions stay in the Python you already know.

## Why oxitest?

- **Explicit injection** — only parameters annotated with `Fixture[T]` are injected. No magic name-matching.
- **Typed fixtures** — fixture return types flow through to the test, so your type checker works end-to-end. No plugin required.
- **Parallel by default** — the Rust scheduler spawns worker subprocesses and distributes tests automatically.
- **Fast startup** — the Rust binary starts in milliseconds; Python is only loaded per worker, not per test. [2–3x faster than pytest](https://kalonji-tools.github.io/oxitest/explanation/benchmarks/) on runner overhead.
- **Strict mode** — opt-in checks for bare asserts, dict parametrize, and marks missing a `reason`.

## Installation

```bash
pip install oxitest
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add oxitest
```

Requires Python 3.11+.

## Quick start

### Write a test

A test is a function whose name starts with `test_`. Use plain Python `assert`.

```python
# tests/test_math.py

def test_add():
    assert 1 + 2 == 3
```

Run it:

```bash
oxitest tests/
```

### Add a fixture

Create a `Fixtures()` registry in `conftest.py`. Annotate test parameters with
`Fixture[T]` to inject them.

```python
# conftest.py
import oxitest

fx = oxitest.Fixtures()

@fx.fixture
def sample_numbers() -> list[int]:
    return [1, 2, 3, 4, 5]
```

```python
# tests/test_example.py
from conftest import sample_numbers
from oxitest import Fixture

def test_sum(sample_numbers: Fixture[list[int]]) -> None:
    assert sum(sample_numbers) == 15
```

Your IDE knows `sample_numbers` is `list[int]` — no plugin needed.

### Parametrize with named cases

Use keyword arguments as case labels. Frozen dataclasses give you type safety
and readable test IDs.

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
    zero=AddCase(x=0, y=0, expected=0),
)
def test_add(x: int, y: int, expected: int) -> None:
    assert x + y == expected
```

Runs as `test_add[basic]`, `test_add[negative]`, `test_add[zero]`.

### Use marks

Tag tests with `@oxitest.mark.<name>`. Custom marks must be registered in
`pyproject.toml`.

```toml
[tool.oxitest]
markers = ["slow: marks tests as slow"]
```

```python
import oxitest

@oxitest.mark.slow
def test_large_computation():
    ...
```

```bash
oxitest tests/ -m slow          # run only slow tests
oxitest tests/ -m "not slow"    # skip slow tests
```

Built-in marks — `skip`, `xfail`, `timeout` — work without registration.

## Documentation

Full documentation is at [kalonji-tools.github.io/oxitest](https://kalonji-tools.github.io/oxitest/).

- [Getting started tutorial](https://kalonji-tools.github.io/oxitest/tutorials/getting-started/) — from installation to your first fixture
- [How-to guides](https://kalonji-tools.github.io/oxitest/how-to/use-fixtures/) — fixtures, markers, parametrize, parallel, async, retries, and more
- [CLI reference](https://kalonji-tools.github.io/oxitest/reference/cli/) — all command-line options
- [Configuration](https://kalonji-tools.github.io/oxitest/reference/configuration/) — `pyproject.toml` keys
- [Why Rust + Python](https://kalonji-tools.github.io/oxitest/explanation/why-rust-python/) — the architectural reasoning

## Background

This project started as a learning exercise — specifically to learn Rust/PyO3
FFI, practice [Spec Driven Development](https://www.specdriven.dev/), and
explore [working responsibly with AI](https://kalonji-tools.github.io/oxitest/explanation/why-ai/).
The constraints and workflow are documented in the explanation section of the
docs for anyone interested in the approach.
