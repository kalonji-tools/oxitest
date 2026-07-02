"""Integration test helpers.

Helper functions are accessible via ``helpers.integ.<function>()``.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import oxitest
from oxitest import Helpers, TempDir, Yields

integ = Helpers()

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


@integ.helper
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


@integ.helper
def assert_passed(out: str, rc: int, *, count: int | None = None):
    """Assert the run passed (exit 0)."""
    assert rc == 0, f"expected exit 0, got {rc}\n{out}"
    if count is not None:
        assert f"{count} passed" in out, f"expected '{count} passed' in:\n{out}"


@integ.helper
def assert_failed(out: str, rc: int, *, count: int | None = None):
    """Assert the run had failures (non-zero exit)."""
    assert rc != 0, f"expected non-zero exit, got {rc}\n{out}"
    if count is not None:
        assert f"{count} failed" in out, f"expected '{count} failed' in:\n{out}"


@integ.helper
def assert_collection_error(out: str, rc: int):
    """Assert collection error (exit 3)."""
    assert rc == 3, f"expected exit 3 (collection error), got {rc}\n{out}"


@integ.helper
def assert_contains(out: str, *terms: str):
    """Assert all terms are present in output."""
    for term in terms:
        assert term in out, f"expected {term!r} in:\n{out}"


@integ.helper
def assert_excludes(out: str, *terms: str):
    """Assert none of the terms are present in output."""
    for term in terms:
        assert term not in out, f"unexpected {term!r} in:\n{out}"


# ── Shared fixtures ──────────────────────────────────────────────────────────


@fx.fixture
def git_repo(tmp: TempDir) -> Yields[Path]:
    """Tmp dir initialized as a git repo with an initial commit."""
    git = ["git", "-C", str(tmp)]
    clean_env = {
        k: v for k, v in __import__("os").environ.items() if not k.startswith("GIT_")
    }
    run = lambda *cmd: subprocess.run(  # noqa: E731
        cmd, check=True, capture_output=True, env=clean_env
    )
    run(*git, "init")
    run(*git, "config", "user.email", "test@test.com")
    run(*git, "config", "user.name", "Test")
    (tmp / ".gitkeep").write_text("")
    run(*git, "add", ".")
    run(*git, "commit", "-m", "init")
    yield tmp
