"""Shared test infrastructure for the oxitest test suite.

Fixtures and helper functions used across test files. Helpers are
accessible via ``from conftest import helpers`` — see
docs/src/explanation/conftest-helpers.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

__helpers_namespace__ = "common"

import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from types import TracebackType

if TYPE_CHECKING:
    from oxitest._bridge._helper_namespace import HelperNamespace

    helpers: HelperNamespace

import oxitest
from oxitest import TempDir, Yields
from oxitest._bridge._fixture_registry import FixtureDef, FixtureRegistry
from oxitest._bridge._fixture_session import FixtureSession, _SessionProtocol
from oxitest._bridge._test_meta import TestMeta
from oxitest._bridge.result import TestResult

__all__ = [
    "exec_inline",
    "make_fixture_def",
    "make_meta",
    "make_session",
    "make_session_with",
    "RecordingDebugger",
    "run_oxitest",
    "run_oxitest_subcmd",
    "run_test",
    "write_test_file",
    "write_test_module",
]

fx = oxitest.Fixtures()


@fx.fixture
def fixture_session(tmp: TempDir) -> Yields[FixtureSession]:
    reg = FixtureRegistry()
    session = FixtureSession(reg)
    yield session
    session.end_session()


@fx.fixture
def clean_sys_modules() -> Yields[None]:
    saved = sys.modules.copy()
    yield
    for key in list(sys.modules):
        if key not in saved:
            del sys.modules[key]
    sys.modules.update({k: v for k, v in saved.items() if k not in sys.modules})


def make_fixture_def(
    name: str,
    factory=None,
    *,
    namespace: str = "",
    shared: bool = False,
    autouse: bool = False,
    is_async: bool = False,
    conftest_path: str = "",
    doc: str = "",
    params: tuple | None = None,
) -> FixtureDef:
    """Create a ``FixtureDef`` with sensible defaults.

    When *factory* is ``None`` a no-op ``lambda: None`` is used and
    its ``__name__`` / ``__doc__`` / ``__module__`` are set so that
    the fixture lister can group by origin.
    """
    if factory is None:

        def _fn() -> None:
            pass

        _fn.__name__ = name
        _fn.__doc__ = doc
        _fn.__module__ = "conftest" if conftest_path else "oxitest._bridge._builtins"
        factory = _fn
    return FixtureDef(
        name=name,
        func=factory,
        autouse=autouse,
        params=params,
        conftest_path=conftest_path,
        shared=shared,
        namespace=namespace,
        is_async=is_async,
    )


def make_session(*defs: FixtureDef) -> FixtureSession:
    """Create a ``FixtureSession`` from one or more ``FixtureDef``s."""
    reg = FixtureRegistry()
    for d in defs:
        reg.register(d)
    return FixtureSession(reg)


def make_session_with(name: str, factory) -> FixtureSession:
    """Shortcut: single-fixture session for quick tests."""
    return make_session(make_fixture_def(name, factory, conftest_path="/conftest.py"))


def run_oxitest(
    tmp_path,
    *extra_args: str,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: int = 60,
) -> tuple[str, str, int]:
    """Run oxitest as a subprocess and return ``(stdout, stderr, returncode)``.

    Disables color output and enforces a timeout (default 60 s).

    Args:
        tmp_path: Path to pass as the positional argument. Pass ``None``
            to omit (e.g. for ``oxitest env``).
        env: Optional environment dict passed directly to ``subprocess.run``.
            Caller is responsible for inheriting ``os.environ`` if needed.
        cwd: Optional working directory for the subprocess.
        timeout: Subprocess timeout in seconds.
    """
    cmd = [sys.executable, "-m", "oxitest"]
    if tmp_path is not None:
        cmd.append(str(tmp_path))
    cmd.extend(["--color", "never", *extra_args])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        env=env,
    )
    return result.stdout, result.stderr, result.returncode


def run_oxitest_subcmd(
    tmp_path,
    *subcmd_and_args: str,
    timeout: int = 60,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[str, str, int]:
    """Run oxitest with a subcommand as a subprocess.

    When *cwd* is not ``None``, *tmp_path* is used as the subprocess
    working directory and is **not** appended as a positional path
    argument.  The actual *cwd* value is treated as a flag — callers
    conventionally pass ``cwd="."`` to activate this mode.
    """
    use_cwd = cwd is not None
    cmd = [sys.executable, "-m", "oxitest", *subcmd_and_args]
    if not use_cwd and tmp_path is not None:
        cmd.append(str(tmp_path))
    if subcmd_and_args and subcmd_and_args[0] not in {"query", "env"}:
        cmd.extend(["--color", "never"])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(tmp_path) if use_cwd else None,
        env=env,
    )
    return result.stdout, result.stderr, result.returncode


def make_meta(
    module_path: str = "t.py",
    fn_name: str = "test_fn",
    param_id: str | None = None,
) -> TestMeta:
    """Create a ``TestMeta`` with sensible defaults for tests."""
    node_id = f"{module_path}::{fn_name}"
    if param_id:
        node_id += f"[{param_id}]"
    return TestMeta(
        module_path=module_path, fn_name=fn_name, node_id=node_id, param_id=param_id
    )


def run_test(
    module_path: str,
    fn_name: str,
    session: _SessionProtocol | None = None,
    param_id: str | None = None,
    default_timeout: int | None = None,
) -> TestResult:
    """Convenience wrapper around ``executor.run_test`` for tests.

    Accepts the old positional-arg style and constructs a ``TestMeta``
    internally, so existing test call sites don't need to change.
    """
    from oxitest._bridge.executor import run_test as _run_test

    meta = TestMeta(
        module_path=module_path,
        fn_name=fn_name,
        node_id=f"{module_path}::{fn_name}" + (f"[{param_id}]" if param_id else ""),
        param_id=param_id,
    )
    return _run_test(meta, session=session, default_timeout=default_timeout)


def exec_inline(
    tmp,
    code: str,
    fn_name: str = "test_ok",
    *,
    session: _SessionProtocol | None = None,
    name: str | None = None,
    param_id: str | None = None,
    default_timeout: int | None = None,
) -> TestResult:
    """Write *code* to a temp test file and execute *fn_name* from it.

    Combines ``write_test_module`` + ``run_test`` into a single call.
    The file name is derived from *fn_name* unless *name* is given.
    """
    if name is None:
        base = fn_name.removeprefix("test_") or "auto"
        name = f"test_{base}.py"
    path = write_test_module(tmp, code, name=name)
    return run_test(
        path,
        fn_name,
        session=session,
        param_id=param_id,
        default_timeout=default_timeout,
    )


def write_test_file(
    tmp_path,
    code: str,
    name: str = "test_auto.py",
) -> str:
    """Write a test file into *tmp_path* and return its path as str."""
    f = tmp_path / name
    f.write_text(code)
    return str(f)


def write_test_module(tmp, code: str, *, name: str = "test_auto.py") -> str:
    f = tmp / name
    f.write_text(textwrap.dedent(code))
    return str(f)


@dataclass
class RecordingDebugger:
    """Test double for DebuggerBackend that records calls."""

    trace_count: int = 0
    post_mortem_tracebacks: list[TracebackType] = field(default_factory=list)

    def trace(self) -> None:
        self.trace_count += 1

    def post_mortem(self, tb: TracebackType) -> None:
        self.post_mortem_tracebacks.append(tb)
