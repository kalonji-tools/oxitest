"""Shared test helpers for the oxitest test suite.

This module provides factory functions and utilities used across
multiple test files. It is NOT a test file (no ``test_`` prefix)
and will not be collected by oxitest.
"""

from __future__ import annotations

import subprocess
import sys

from oxitest._bridge._fixture_registry import FixtureDef, FixtureRegistry
from oxitest._bridge._fixture_session import FixtureSession

__all__ = [
    "make_fixture_def",
    "make_session",
    "make_session_with",
    "run_oxitest",
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


def write_test_file(
    tmp_path,
    code: str,
    name: str = "test_auto.py",
) -> str:
    """Write a test file into *tmp_path* and return its path as str."""
    f = tmp_path / name
    f.write_text(code)
    return str(f)
