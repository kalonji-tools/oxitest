"""oxitest — a typed Python test framework.

Public API
----------
Fixtures   — Instance-based fixture registry. Create one per conftest.py:

    fixtures = Fixtures()

    @fixtures.fixture
    def my_db() -> Database:
        ...

Fixture[T] — Injection signal for test and fixture parameters. An annotation
             of Fixture[T] tells oxitest to inject the matching fixture value.
             Unannotated parameters are NOT injected.

    def test_example(db: Fixture[Database]) -> None:
        assert db.is_connected()

Yields[T]  — Return type for yield fixtures: Generator[T, None, None]. Eliminates
             ``# type: ignore[return]`` on yield-based fixtures.

parametrize — First-class decorator for named test cases:
              ``@oxitest.parametrize(basic=MyCase(x=1))``.
              Accepts frozen dataclass instances (dataclass mode) or plain dicts
              (dict mode). See ``help(oxitest.parametrize)`` for full docs.

mark       — Decorator namespace: mark.skip, mark.skipif, mark.xfail,
             and any custom mark registered in pyproject.toml.

TestContext — Injected bare (``ctx: TestContext``); provides addfinalizer /
              on_teardown for imperative cleanup.

TempDir        — Injected bare (``tmp: TempDir``); unique temp dir deleted after test.
TempDirFactory — Session-scoped factory; ``factory.mktemp("label")`` → TempDir.
StdCapture     — Capture ``sys.stdout``/``sys.stderr``; ``cap.readouterr()``
                 → CaptureResult.
FdCapture      — Capture at fd level (C extensions); same readouterr() API.
Patcher        — Temp overrides: ``patch.setattr``, ``patch.setenv``,
                 ``patch.delenv``, ``patch.chdir``.
CaptureResult  — ``out`` and ``err`` strings returned by ``readouterr()``.
LogCapture     — Capture logging records; ``log.records``, ``log.text``,
                 ``log.set_level()``.
WarnCapture    — Capture all warnings.warn() calls during a test:
                 ``warn.list``, ``warn.clear()``.
raises         — Assert a block raises an exception:
                 ``with oxitest.raises(ValueError, match="pattern"):``.
warns          — Assert a block emits a warning:
                 ``with oxitest.warns(UserWarning, match="pattern"):``.
importorskip   — Skip test if module not installed:
                 ``oxitest.importorskip("loguru")``.

Note: TempDir, TestContext, Patcher, StdCapture, FdCapture, LogCapture and
      TempDirFactory already carry the injection marker — annotate parameters
      directly (``tmp: TempDir``) without wrapping in ``Fixture[T]``.
"""

from __future__ import annotations

import sys
from typing import Any as _Any, NoReturn as _NoReturn

from oxitest._bridge._builtins import (
    CaptureResult as CaptureResult,
    FdCapture as FdCapture,
    LogCapture as LogCapture,
    Patcher as Patcher,
    StdCapture as StdCapture,
    TempDir as TempDir,
    TempDirFactory as TempDirFactory,
    TestContext as TestContext,
    WarnCapture as WarnCapture,
)
from oxitest._bridge._errors import (
    SharedFixtureMutationError as SharedFixtureMutationError,
)
from oxitest._bridge._fixture_type import (
    Fixture as Fixture,
    FixtureRef as FixtureRef,
    Yields as Yields,
)
from oxitest._bridge._importorskip import importorskip as importorskip
from oxitest._bridge._mark_api import (
    mark as mark,
    skip as skip,
)
from oxitest._bridge._raises import raises as raises
from oxitest._bridge._warns import warns as warns
from oxitest._bridge.fixtures import (
    Fixtures as Fixtures,
    FixtureTeardownWarning as FixtureTeardownWarning,
)
from oxitest._bridge.parametrize import parametrize as parametrize
from oxitest.plugin import Plugin as Plugin

__all__ = [
    "CaptureResult",
    "FdCapture",
    "Fixture",
    "FixtureRef",
    "FixtureTeardownWarning",
    "Fixtures",
    "LogCapture",
    "Patcher",
    "parametrize",
    "raises",
    "SharedFixtureMutationError",
    "StdCapture",
    "TempDir",
    "TempDirFactory",
    "TestContext",
    "Yields",
    "importorskip",
    "mark",
    "Plugin",
    "skip",
    "WarnCapture",
    "warns",
]


def fixture(*args: _Any, **kwargs: _Any) -> _NoReturn:
    """Sentinel: raises with instructions to use Fixtures() registry instead."""
    raise AttributeError(
        "oxitest.fixture does not exist.\n"
        "Fixtures in oxitest are declared via a Fixtures() registry:\n\n"
        "    fixtures = oxitest.Fixtures()\n\n"
        "    @fixtures.fixture\n"
        "    def my_fixture() -> MyType:\n"
        "        ...\n\n"
        "Define your Fixtures() instance in conftest.py and import the functions."
    )


def main() -> None:
    try:
        from oxitest._oxitest import run
    except ImportError:
        msg = "Error: oxitest._oxitest module not found. "
        msg += "Ensure the Rust extension is built.\n"
        sys.stderr.write(msg)
        sys.exit(1)
    sys.exit(run(sys.argv[1:]))
