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
             `# type: ignore[return]` on yield-based fixtures.

parametrize — First-class decorator for named test cases:
              `@oxitest.parametrize(basic=MyCase(x=1))`.
              Accepts frozen dataclass instances (dataclass mode) or plain dicts
              (dict mode). See `help(oxitest.parametrize)` for full docs.

partial     — Create a partial case for parametrize composition:
              `oxi.partial(MyCase, x=1)`. Stack multiple `@parametrize`
              decorators with partial values to express cartesian products.

mark       — Decorator namespace: mark.skip, mark.xfail, mark.timeout,
             and any custom mark registered in pyproject.toml.

TestContext — The running test's identity and imperative cleanup. Reach it
              with `oxi.current_test()` (or `TestContext.current()`), which
              works from plain functions the test calls. The injected form
              (`ctx: TestContext`) is legacy, retired at v4.

current_test — `oxi.current_test()` → the running test's TestContext. Alias
               for TestContext.current(); raises outside a test body.

TempDir        — Injected bare (`tmp: TempDir`); unique temp dir deleted after test.
TempDirFactory — Session-scoped factory; `factory.mktemp("label")` → TempDir.
StdCapture     — Capture `sys.stdout`/`sys.stderr`; `cap.readouterr()`
                 → CaptureResult.
FdCapture      — Capture at fd level (C extensions); same readouterr() API.
Patcher        — Temp overrides: `patch.setattr`, `patch.setenv`,
                 `patch.delenv`, `patch.chdir`.
CaptureResult  — `out` and `err` strings returned by `readouterr()`.
LogCapture     — Capture logging records; `log.records`, `log.text`,
                 `log.set_level()`.
WarnCapture    — Capture all warnings.warn() calls during a test:
                 `warn.warnings`, `warn.clear()`.
raises         — Assert a block raises an exception:
                 `with oxitest.raises(ValueError, match="pattern"):`.
warns          — Assert a block emits a warning:
                 `with oxitest.warns(UserWarning, match="pattern"):`.
importorskip   — Skip test if module not installed:
                 `oxitest.importorskip("loguru")`.
approx          — Approximate floating-point comparison:
                 `assert 0.1 + 0.2 == oxi.approx(0.3)`.
                 Supports scalars, sequences, mappings, and nested combos.
                 Parameters: rel (1e-6), abs (1e-12), nan_ok (False).

Note: TempDir, TestContext, Patcher, StdCapture, FdCapture, LogCapture,
      TempDirFactory and WarnCapture are decorated with ``@injectable`` —
      annotate parameters directly (`tmp: TempDir`) without wrapping in
      `Fixture[T]`.

Examples:
--------
``raises`` asserts that a block raises the expected exception:

>>> import oxitest
>>> with oxitest.raises(ValueError):
...     int("not a number")

Use the ``match`` parameter to check the exception message:

>>> with oxitest.raises(ValueError, match="invalid literal"):
...     int("nope")

Access the caught exception via ``as``:

>>> with oxitest.raises(KeyError) as exc_info:
...     {"a": 1}["b"]
>>> exc_info.value.args[0]
'b'

``warns`` asserts that a block emits a warning:

>>> import warnings
>>> with oxitest.warns(UserWarning, match="deprecated"):
...     warnings.warn("deprecated feature", UserWarning)

``approx`` enables approximate floating-point comparison:

>>> 0.1 + 0.2 == oxitest.approx(0.3)
True
>>> [0.1, 0.2] == oxitest.approx([0.1, 0.2])
True
>>> {"x": 0.1 + 0.2} == oxitest.approx({"x": 0.3})
True

"""

from __future__ import annotations

import sys

from oxitest._bridge._approx import ApproxBase as ApproxBase, approx as approx
from oxitest._bridge._arrange_api import arrange as arrange
from oxitest._bridge._async_backend import (
    AsyncBackend as AsyncBackend,
    AsyncSession as AsyncSession,
)
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
    current_test as current_test,
)
from oxitest._bridge._coverage import (
    CovReportFormat as CovReportFormat,
)
from oxitest._bridge._debugger import (
    DebuggerBackend as DebuggerBackend,
)
from oxitest._bridge._errors import (
    ArrangeError as ArrangeError,
    AutouseRegistrationError as AutouseRegistrationError,
    BoundaryError as BoundaryError,
    SharedFixtureMutationError as SharedFixtureMutationError,
    TestContextUnavailableError as TestContextUnavailableError,
)
from oxitest._bridge._fixture_decorator import fixture as fixture
from oxitest._bridge._fixture_type import (
    Fixture as Fixture,
    FixtureRef as FixtureRef,
    Yields as Yields,
    injectable as injectable,
)
from oxitest._bridge._fixtures import (
    Fixtures as Fixtures,
)
from oxitest._bridge._importorskip import importorskip as importorskip
from oxitest._bridge._mark_api import (
    mark as mark,
    skip as skip,
)
from oxitest._bridge._plugin_config import (
    Both as Both,
    Cli as Cli,
    CliExtension as CliExtension,
    Conf as Conf,
)
from oxitest._bridge._raises import raises as raises
from oxitest._bridge._read_fixtures import _FixturesProxy as _FixturesProxy
from oxitest._bridge._streams import force_utf8_streams
from oxitest._bridge._warns import warns as warns
from oxitest._bridge.parametrize import (
    parametrize as parametrize,
    partial as partial,
)
from oxitest._bridge.result import (
    CollectedItem as CollectedItem,
    TestResult as TestResult,
)
from oxitest._exit_code import ExitCode as ExitCode
from oxitest._oxitest import run
from oxitest.plugin import CoverageProvider as CoverageProvider, Plugin as Plugin

__all__ = [
    "ArrangeError",
    "AsyncBackend",
    "AsyncSession",
    "AutouseRegistrationError",
    "Both",
    "BoundaryError",
    "CaptureResult",
    "Cli",
    "CliExtension",
    "CollectedItem",
    "Conf",
    "CovReportFormat",
    "CoverageProvider",
    "DebuggerBackend",
    "ExitCode",
    "FdCapture",
    "Fixture",
    "FixtureRef",
    "Fixtures",
    "LogCapture",
    "Patcher",
    "Plugin",
    "SharedFixtureMutationError",
    "StdCapture",
    "TempDir",
    "TempDirFactory",
    "TestContext",
    "TestContextUnavailableError",
    "TestResult",
    "WarnCapture",
    "Yields",
    "approx",
    "arrange",
    "current_test",
    "fixture",
    "fixtures",
    "importorskip",
    "injectable",
    "mark",
    "parametrize",
    "partial",
    "raises",
    "skip",
    "warns",
]


fixtures = _FixturesProxy()


def main() -> None:
    # Both `python -m oxitest` and the `oxitest` console script land here
    # (pyproject.toml declares `oxitest = "oxitest:main"`), so this is the only
    # CLI-side site. Before run(), because the Rust reporter already writes
    # UTF-8 to fd 1 directly and the Python half has to agree with it (#2004).
    force_utf8_streams()
    sys.exit(run(sys.argv[1:]))
