"""Helpers that run oxitest, or a single test, and assert on the result.

Split out of conftest.py by #1787. These are plain functions — there is no
helper registry (#1700). Import them via ``from tests import helpers``.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import TypeVar

from oxitest import TempDir, TestResult
from oxitest._bridge._fixture_session import _SessionProtocol
from oxitest._bridge._test_kind import from_wire
from oxitest._bridge._test_meta import TestMeta
from oxitest._bridge.executor import run_test as _run_test
from tests.helpers.modules import write_test_module

_R = TypeVar("_R", bound=TestResult)


def _describe(result: TestResult) -> str:
    """Name a result and quote its message, for an ``assert_result`` failure.

    Packs the name-plus-optional-message conditional into a single expression so
    it can sit in an ``assert``'s message slot, which is the half of an
    ``assert`` that stays lazy. Inlining it would mean a ternary nested inside
    an f-string. Call it only from a message slot -- hoisting the call to a
    statement above the ``assert`` makes it eager again.
    """
    message = getattr(result, "message", "")
    return f"{type(result).__name__}({message!r})" if message else type(result).__name__


def assert_result(
    result: TestResult | None,
    expected_type: type[_R],
    *,
    why: str,
    **fields: object,
) -> _R:
    """Narrow a TestResult to a specific variant and assert field values.

    Returns the narrowed result typed as *expected_type* rather than the
    ``TestResult`` union, so callers can reach variant-specific fields without
    a second narrowing step. The runtime check was always here; before #1791
    the return annotation discarded it and ty learned nothing.

    Args:
        result: The result to narrow. ``None`` fails the assertion.
        expected_type: The variant *result* must be an instance of. Read via
            ``getattr(..., "__name__")`` because a ``types.UnionType`` has no
            ``__name__`` -- ``isinstance`` would accept one, so without this the
            call would pass forever and then ``AttributeError`` the day it
            failed. ty rejects a union here, so this is defence, not support.
        why: Explains why this variant is the contract under test. Required, and
            asserted non-empty (#1793) -- requiring the parameter only forces it
            to be *passed*, and ``why=""`` reproduces the omission verbatim,
            down to the dangling separator. Appended to every failure message on
            its own line -- CLAUDE.md requires each assert to say why. The
            separator is a newline rather than " -- " because the prose itself
            conventionally uses " -- " to divide claim from consequence, and
            nesting the two read as a run-on.
            A result variant field named "why" would bind this parameter instead
            and its assertion would silently not run; no variant has one. This is
            asymmetric with *result*/*expected_type*, which are positional-or-
            keyword -- a colliding field name there raises a loud ``TypeError``.
        **fields: Field values asserted on the narrowed result.

    """
    assert why, (
        "assert_result needs a why -- an empty one is the omission the required"
        " parameter exists to prevent, and it still appends a bare separator"
    )
    suffix = f"\n{why}"
    expected_name = getattr(expected_type, "__name__", str(expected_type))
    assert result is not None, f"expected {expected_name}, got None{suffix}"
    assert isinstance(result, expected_type), (
        f"expected {expected_name}, got {_describe(result)}{suffix}"
    )
    for name, expected in fields.items():
        actual = getattr(result, name)
        assert actual == expected, (
            f"{expected_name}.{name}: expected {expected!r}, got {actual!r}{suffix}"
        )
    return result


def run_oxitest(
    tmp_path: TempDir | Path | None,
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
        encoding="utf-8",
        timeout=timeout,
        cwd=cwd,
        env=env,
        check=False,
    )
    return result.stdout, result.stderr, result.returncode


def run_oxitest_subcmd(
    tmp_path: TempDir | Path | None,
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
        encoding="utf-8",
        timeout=timeout,
        cwd=str(tmp_path) if use_cwd else None,
        env=env,
        check=False,
    )
    return result.stdout, result.stderr, result.returncode


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
    meta = TestMeta(
        module_path=module_path,
        fn_name=fn_name,
        node_id=f"{module_path}::{fn_name}" + (f"[{param_id}]" if param_id else ""),
        kind=from_wire(param_id),
    )
    return _run_test(meta, session=session, default_timeout=default_timeout)


def exec_inline(  # noqa: PLR0913 — test helper, all kwargs have defaults
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
