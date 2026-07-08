"""Shared test infrastructure for the oxitest test suite.

Fixtures and helper functions used across test files. Helpers are
accessible via ``from oxitest import helpers`` — see
docs/src/explanation/conftest-helpers.md.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType
from typing import TypeVar

import oxitest
from oxitest import Helpers, TempDir, TestResult, Yields
from oxitest._bridge._boundary import safe_type_hints
from oxitest._bridge._fixture_registry import (
    ConftestSource,
    FixtureDef,
    FixtureScope,
)
from oxitest._bridge._fixture_session import FixtureSession, _SessionProtocol
from oxitest._bridge._test_meta import TestMeta
from oxitest._bridge.plugin_loader import PluginRegistry

common = Helpers()

__all__ = [
    "RecordingDebugger",
    "assert_result",
    "exec_inline",
    "make_exc",
    "make_fixture_def",
    "make_meta",
    "make_plugin_module",
    "make_session",
    "make_session_with",
    "run_oxitest",
    "run_oxitest_subcmd",
    "run_test",
    "write_test_file",
    "write_test_module",
]

fx = oxitest.Fixtures()


@common.helper
def assert_result(
    result: TestResult | None,
    expected_type: type[TestResult],
    **fields: object,
) -> TestResult:
    """Narrow a TestResult to a specific variant and assert field values.

    Returns the narrowed result for further assertions.
    """
    assert result is not None, f"expected {expected_type.__name__}, got None"
    assert isinstance(result, expected_type), (
        f"expected {expected_type.__name__}, got {type(result).__name__}"
    )
    for name, expected in fields.items():
        actual = getattr(result, name)
        assert actual == expected, (
            f"{expected_type.__name__}.{name}: expected {expected!r}, got {actual!r}"
        )
    return result


@fx.fixture
def fixture_session(_tmp: TempDir) -> Yields[FixtureSession]:
    session = FixtureSession([], PluginRegistry())
    yield session
    session.end_session()


@fx.fixture
def _clean_sys_modules() -> Yields[None]:
    saved = sys.modules.copy()
    yield
    for key in list(sys.modules):
        if key not in saved:
            del sys.modules[key]
    sys.modules.update({k: v for k, v in saved.items() if k not in sys.modules})


@common.helper
def make_fixture_def(
    name: str,
    factory: Callable[..., object] | None = None,
    *,
    namespace: str = "",
    shared: bool = False,
    autouse: bool = False,
    is_async: bool = False,
    conftest_path: str = "",
    doc: str = "",
    depends_on: tuple[tuple[str, type], ...] = (),
    fixture_type: type | None = None,
) -> FixtureDef:
    """Create a ``FixtureDef`` with sensible defaults.

    When *factory* is ``None`` a no-op ``lambda: None`` is used and
    its ``__name__`` / ``__doc__`` / ``__module__`` are set so that
    the fixture lister can group by origin.

    *depends_on* is a tuple of ``(qualifier, binding_type)`` pairs that
    explicitly declare fixture dependencies for type-based graph construction.

    *fixture_type* overrides the type extracted from the factory's return
    annotation. Useful when the factory is defined in a module with
    ``from __future__ import annotations`` (which turns annotations into
    strings that ``get_type_hints`` cannot resolve for locally-defined types).
    """
    if factory is None:

        def _fn() -> None:
            pass

        _fn.__name__ = name
        _fn.__doc__ = doc or None
        _fn.__module__ = "conftest" if conftest_path else "oxitest._bridge._builtins"
        factory = _fn
    # Explicit fixture_type overrides annotation extraction
    if fixture_type is not None:
        ft: type = fixture_type
    else:
        # Try to extract return type; fall back to object
        hints = safe_type_hints(factory)
        ft = hints.get("return", object) if hints is not None else object
    return FixtureDef(
        name=name,
        fixture_type=ft,
        scope=FixtureScope.SHARED if shared else FixtureScope.EACH,
        source=ConftestSource(func=factory, conftest_path=conftest_path),
        autouse=autouse,
        namespace=namespace,
        is_async=is_async,
        depends_on=depends_on,
    )


@common.helper
def make_session(*defs: FixtureDef) -> FixtureSession:
    """Create a ``FixtureSession`` from one or more ``FixtureDef``s."""
    return FixtureSession(list(defs), PluginRegistry())


@common.helper
def make_session_with(name: str, factory: Callable[..., object]) -> FixtureSession:
    """Shortcut: single-fixture session for quick tests."""
    return make_session(make_fixture_def(name, factory, conftest_path="/conftest.py"))


@common.helper
def run_oxitest(
    tmp_path: TempDir | None,
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
        *extra_args: Additional CLI arguments forwarded to oxitest.
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


@common.helper
def run_oxitest_subcmd(
    tmp_path: TempDir | None,
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
    if subcmd_and_args and subcmd_and_args[0] not in {"query", "env", "fixtures"}:
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


@common.helper
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


_E = TypeVar("_E", bound=BaseException)


@common.helper
def make_exc(exc_type: type[_E], msg: str = "") -> _E:
    """Create an exception with a real traceback attached."""
    try:
        raise exc_type(msg)  # noqa: TRY301
    except exc_type as exc:
        return exc


@common.helper
def run_test(
    module_path: str,
    fn_name: str,
    session: _SessionProtocol | None = None,
    param_id: str | None = None,
    default_timeout: int | None = None,
) -> TestResult:
    """Wrap ``executor.run_test`` for tests.

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


@common.helper
def exec_inline(
    tmp: TempDir,
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


@common.helper
def write_test_file(
    tmp_path: TempDir,
    code: str,
    name: str = "test_auto.py",
) -> str:
    """Write a test file into *tmp_path* and return its path as str."""
    f = tmp_path / name
    f.write_text(code)
    return str(f)


@common.helper
def write_test_module(tmp: TempDir, code: str, *, name: str = "test_auto.py") -> str:
    """Write dedented code to a named file in tmp and return its path as str."""
    f = tmp / name
    f.write_text(textwrap.dedent(code))
    return str(f)


@common.helper
def make_plugin_module(
    name: str,
    entry: Callable[..., object] | None = None,
    **attrs: object,
) -> types.ModuleType:
    """Create a fake plugin module with the given entry point and attributes.

    Sets ``oxitest_plugin`` to *entry* when provided.  Any additional keyword
    arguments are set as module attributes (e.g. ``oxitest_cli_extension``).
    """
    mod = types.ModuleType(name)
    if entry is not None:
        setattr(mod, "oxitest_plugin", entry)  # noqa: B010 — dynamic module attr
    for attr_name, value in attrs.items():
        setattr(mod, attr_name, value)
    return mod


@common.helper
@dataclass
class RecordingDebugger:
    """Test double for DebuggerBackend that records calls."""

    trace_count: int = 0
    post_mortem_tracebacks: list[TracebackType] = field(default_factory=list)

    def trace(self) -> None:
        """Increment trace_count to record that trace was called."""
        self.trace_count += 1

    def post_mortem(self, tb: TracebackType) -> None:
        """Append tb to post_mortem_tracebacks to record post-mortem invocations."""
        self.post_mortem_tracebacks.append(tb)
