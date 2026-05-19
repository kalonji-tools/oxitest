# oxitest

[![CI](https://github.com/kalonji-tools/oxitest/actions/workflows/ci.yml/badge.svg)](https://github.com/kalonji-tools/oxitest/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/kalonji-tools/oxitest/branch/main/graph/badge.svg)](https://codecov.io/gh/kalonji-tools/oxitest)
[![PyPI version](https://img.shields.io/pypi/v/oxitest)](https://pypi.org/project/oxitest/)
[![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://kalonji-tools.github.io/oxitest/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A pytest rewrite in Rust.

## Installation

```bash
pip install oxitest
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add oxitest
```

Requires Python 3.11+.

---

## Background

This project started as a personal learning exercise, not a production tool. The
goals were concrete:

- **Learn Rust and FFI** — specifically PyO3 and the boundary between a Rust
  core and a Python API.
- **Practice Spec Driven Development** — writing design specs before touching
  code, then deleting them once the feature ships.
- **Work responsibly with AI** — 2025 was a turbulent year. Multiple clients
  greenlighted AI in the workplace at once, and the result was a wave of sloppy,
  oversized PRs that were hard to review and, in the worst cases, got merged
  anyway. This project is a counter-experiment: use AI as a disciplined
  collaborator, keep changes small and reviewable, and let the spec drive the
  work rather than the autocomplete.

---

## Getting Started in 5 minutes

### 1. Bare test function

A test is a function whose name starts with `test_`. Use plain Python `assert`.

```python
# tests/test_math.py
from mylib.math import add

def test_add():
    assert add(1, 2) == 3
```

Run: `oxitest tests/`

---

### 2. Custom mark (with registration)

Use `@oxitest.mark.<name>` to tag tests. Custom mark names must be registered
in `pyproject.toml` — unregistered marks abort the run with an error.

```toml
# pyproject.toml
[tool.oxitest]
markers = ["slow: marks tests as slow", "integration: integration tests"]
```

```python
import oxitest

@oxitest.mark.slow
def test_large_computation():
    ...
```

Run only slow tests: `oxitest tests/ -m slow`

---

### 3. Fixtures — `Fixtures()` registry

Create a `Fixtures()` instance in `conftest.py` and decorate with
`@fixtures.fixture`. Import the fixture functions into your test files.

```python
# conftest.py
from __future__ import annotations
import oxitest

fixtures = oxitest.Fixtures()

@fixtures.fixture
def sample_numbers() -> list[int]:
    return [1, 2, 3, 4, 5]
```

---

### 4. Requesting a fixture — `Fixture[T]`

Annotate a test parameter with `Fixture[T]` to inject a fixture. The
annotation is the injection signal — an unannotated parameter is NOT injected.

```python
# tests/test_example.py
from conftest import sample_numbers
from oxitest import Fixture

def test_sum(sample_numbers: Fixture[list[int]]) -> None:
    assert sum(sample_numbers) == 15
```

Your IDE (ty, pylance) knows `sample_numbers` is `list[int]` — no plugin
needed.

---

### 5. Parametrized test

Use keyword arguments as case labels. Each kwarg is a named test case.

```python
from dataclasses import dataclass
import oxitest

@dataclass(frozen=True)
class AddCase:
    x: int
    y: int
    expected: int

@oxitest.mark.parametrize(
    basic=AddCase(x=1, y=2, expected=3),
    negative=AddCase(x=-5, y=3, expected=-2),
    zero=AddCase(x=0, y=0, expected=0),
)
def test_add(x: int, y: int, expected: int) -> None:
    assert x + y == expected
```

Run: `oxitest tests/` — three cases run as `test_add[basic]`, `test_add[negative]`,
`test_add[zero]`.

---

## Further reading

- Built-in marks: `skip`, `skipif`, `xfail`
- Fixture scopes: `function`, `module`, `session`
- Yield teardown: `yield value` in a fixture; code after yield runs as teardown
- `TestContext` / `on_teardown`: imperative cleanup inside fixtures
- `@inject_fixtures`: bundle many fixtures into a typed `NamedTuple` (advanced)
