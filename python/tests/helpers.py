"""Shared test helpers for the oxitest test suite.

This module provides factory functions and utilities used across
multiple test files. It is NOT a test file (no ``test_`` prefix)
and will not be collected by oxitest.
"""

from __future__ import annotations

import subprocess
import sys

from oxitest._bridge._fixture_registry import FixtureDef, FixtureRegistry
from oxitest._bridge._fixture_session import FixtureSession, _SessionProtocol
from oxitest._bridge._test_meta import TestMeta
from oxitest._bridge.result import TestResult

__all__ = [
    "make_fixture_def",
    "make_meta",
    "make_session",
    "make_session_with",
    "run_oxitest",
    "run_test",
    "write_test_file",
]


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
    params: list | None = None,
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
    return make_session(
        FixtureDef(
            name=name,
            func=factory,
            autouse=False,
            params=None,
            conftest_path="/conftest.py",
            shared=False,
            namespace="",
            is_async=False,
        )
    )


def run_oxitest(
    tmp_path,
    *extra_args: str,
) -> tuple[str, int]:
    """Run oxitest as a subprocess and return ``(stdout, returncode)``.

    Disables color output and enforces a 60-second timeout.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oxitest",
            str(tmp_path),
            "--color",
            "never",
            *extra_args,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout, result.returncode


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


def write_test_file(
    tmp_path,
    code: str,
    name: str = "test_auto.py",
) -> str:
    """Write a test file into *tmp_path* and return its path as str."""
    f = tmp_path / name
    f.write_text(code)
    return str(f)
