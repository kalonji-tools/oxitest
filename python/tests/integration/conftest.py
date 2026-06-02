"""Integration test helpers.

Helper functions are accessible via ``helpers.integ.<function>()``.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import oxitest

if TYPE_CHECKING:
    from oxitest._bridge._helper_namespace import HelperNamespace

    helpers: HelperNamespace

__helpers_namespace__ = "integ"

fx = oxitest.Fixtures()


# ── Helpers (helpers.integ namespace) ─────────────────────────────────────────

__all__ = [
    "write_project",
    "assert_passed",
    "assert_failed",
    "assert_collection_error",
    "assert_contains",
    "assert_excludes",
]


def write_project(
    tmp,
    *,
    tests: dict[str, str],
    pyproject: str | None = None,
    conftest: str | None = None,
):
    """Scaffold a project in tmp.

    Args:
        tmp: TempDir to write into.
        tests: Mapping of {filename: code}. Code is dedented.
        pyproject: Optional pyproject.toml content (dedented).
        conftest: Optional conftest.py content (dedented).
    """
    if pyproject:
        (tmp / "pyproject.toml").write_text(textwrap.dedent(pyproject))
    if conftest:
        (tmp / "conftest.py").write_text(textwrap.dedent(conftest))
    for name, code in tests.items():
        (tmp / name).write_text(textwrap.dedent(code))


def assert_passed(out: str, rc: int, *, count: int | None = None):
    """Assert the run passed (exit 0, 'passed' in output)."""
    assert rc == 0, f"expected exit 0, got {rc}\n{out}"
    if count is not None:
        assert f"{count} passed" in out, f"expected '{count} passed' in:\n{out}"
    else:
        assert "passed" in out, f"expected 'passed' in:\n{out}"


def assert_failed(out: str, rc: int, *, count: int | None = None):
    """Assert the run had failures (exit 1, 'failed' in output)."""
    assert rc == 1, f"expected exit 1, got {rc}\n{out}"
    if count is not None:
        assert f"{count} failed" in out, f"expected '{count} failed' in:\n{out}"
    else:
        assert "failed" in out, f"expected 'failed' in:\n{out}"


def assert_collection_error(out: str, rc: int):
    """Assert collection error (exit 3)."""
    assert rc == 3, f"expected exit 3 (collection error), got {rc}\n{out}"


def assert_contains(out: str, *terms: str):
    """Assert all terms are present in output."""
    for term in terms:
        assert term in out, f"expected {term!r} in:\n{out}"


def assert_excludes(out: str, *terms: str):
    """Assert none of the terms are present in output."""
    for term in terms:
        assert term not in out, f"unexpected {term!r} in:\n{out}"
