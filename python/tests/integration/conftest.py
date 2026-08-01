"""Integration test fixtures.

Helper functions live in ``tests.integration.helpers`` (#1787) — this file
now only holds the shared ``fx`` fixtures.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import oxitest
from oxitest import TempDir, Yields
from tests.integration.helpers import clean_git_env

fx = oxitest.Fixtures()


# ── Shared fixtures ──────────────────────────────────────────────────────────


@fx.fixture
def git_repo(tmp: TempDir) -> Yields[Path]:
    """Tmp dir initialized as a git repo with an initial commit."""
    git = ["git", "-C", str(tmp)]
    clean_env = clean_git_env()

    def run(*cmd: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(cmd, check=True, capture_output=True, env=clean_env)

    run(*git, "init")
    run(*git, "config", "user.email", "test@test.com")
    run(*git, "config", "user.name", "Test")
    (tmp / ".gitkeep").write_text("")
    run(*git, "add", ".")
    run(*git, "commit", "-m", "init")
    yield tmp
