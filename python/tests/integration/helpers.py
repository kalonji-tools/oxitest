"""Shared test utilities for oxitest's integration suite.

Plain functions, reached by ``from tests.integration import helpers as integ``.
There is no helper registry — #1700 retired the concept; mediation is
justified by lifecycle, never by visibility. Split out of
``integration/conftest.py`` by #1787.

A flat module, not a package: seven functions do not need submodules.
"""

from __future__ import annotations

import os
import textwrap

from oxitest import TempDir

__all__ = [
    "assert_collection_error",
    "assert_contains",
    "assert_excludes",
    "assert_failed",
    "assert_passed",
    "clean_git_env",
    "write_project",
]


def clean_git_env() -> dict[str, str]:
    """Strip GIT_* vars that leak from pre-push hooks."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def write_project(
    tmp: TempDir,
    *,
    tests: dict[str, str],
    pyproject: str | None = None,
    declarations: str | None = None,
    extra_files: dict[str, str] | None = None,
) -> None:
    """Scaffold a project in tmp.

    Args:
        tmp: TempDir to write into.
        tests: Mapping of {filename: code}. Code is dedented.
        pyproject: Optional pyproject.toml content (dedented).
        declarations: Optional ``__fixtures__.py`` content (dedented). Named for
            the declaration file, not for a directory: ``__fixtures__.py`` needs
            no ``__init__.py`` beside it, measured at the rootdir and nested.
        extra_files: Optional mapping of {relative_path: content} for arbitrary
            project files (e.g. package modules like ``mypkg/__init__.py``).
            Parent directories are created automatically. Content is *not*
            dedented — pass verbatim source (docstring indentation matters).

    """
    if pyproject:
        (tmp / "pyproject.toml").write_text(
            textwrap.dedent(pyproject), encoding="utf-8"
        )
    if declarations:
        (tmp / "__fixtures__.py").write_text(
            textwrap.dedent(declarations), encoding="utf-8"
        )
    for name, code in tests.items():
        (tmp / name).write_text(textwrap.dedent(code), encoding="utf-8")
    if extra_files:
        for rel_path, content in extra_files.items():
            target = tmp / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")


def assert_passed(out: str, rc: int, *, count: int | None = None) -> None:
    """Assert the run passed (exit 0)."""
    assert rc == 0, f"expected exit 0, got {rc}\n{out}"
    if count is not None:
        assert f"{count} passed" in out, f"expected '{count} passed' in:\n{out}"


def assert_failed(out: str, rc: int, *, count: int | None = None) -> None:
    """Assert the run had failures (non-zero exit)."""
    assert rc != 0, f"expected non-zero exit, got {rc}\n{out}"
    if count is not None:
        assert f"{count} failed" in out, f"expected '{count} failed' in:\n{out}"


def assert_collection_error(out: str, rc: int) -> None:
    """Assert collection error (exit 3)."""
    assert rc == 3, f"expected exit 3 (collection error), got {rc}\n{out}"


def assert_contains(out: str, *terms: str) -> None:
    """Assert all terms are present in output."""
    for term in terms:
        assert term in out, f"expected {term!r} in:\n{out}"


def assert_excludes(out: str, *terms: str) -> None:
    """Assert none of the terms are present in output."""
    for term in terms:
        assert term not in out, f"unexpected {term!r} in:\n{out}"
