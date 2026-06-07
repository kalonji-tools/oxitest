"""Integration tests: --affected flag with parallel execution."""

import subprocess
import sys
from pathlib import Path

import oxitest
from conftest import helpers
from oxitest import Fixture, TempDir, Yields

fx = oxitest.Fixtures()


def test_affected_parallel_runs_subcommands_correctly(tmp: TempDir):
    """Nested oxitest subprocesses work in parallel workers.

    Regression test for #642: workers used PYO3_PYTHON (build-time Python)
    instead of sys.executable (runtime Python), causing nested subprocesses
    to load a stale extension or wrong Python version.
    """
    (tmp / "test_nested.py").write_text(
        "import subprocess, sys\n"
        "\n"
        "def test_nested_list():\n"
        "    result = subprocess.run(\n"
        "        [sys.executable, '-m', 'oxitest', 'query', 'tests', '.'],\n"
        "        capture_output=True, text=True, timeout=30,\n"
        "    )\n"
        "    assert result.returncode == 0, (\n"
        "        f'nested oxitest list failed with rc={result.returncode}\\n'\n"
        "        f'stderr: {result.stderr}'\n"
        "    )\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc, count=1)


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


@fx.fixture
def git_worktree(git_repo: Fixture[Path], tmp: TempDir) -> Yields[Path]:
    """Git worktree created from git_repo, placed inside a second TempDir."""
    main_repo = git_repo
    clean_env = {
        k: v for k, v in __import__("os").environ.items() if not k.startswith("GIT_")
    }
    run = lambda *cmd: subprocess.run(  # noqa: E731
        cmd, check=True, capture_output=True, env=clean_env
    )
    git = ["git", "-C", str(main_repo)]

    # Create a baseline test file and commit it.
    (main_repo / "test_base.py").write_text("def test_base(): assert True\n")
    run(*git, "add", ".")
    run(*git, "commit", "-m", "baseline")

    # Create a worktree inside the second TempDir.
    worktree_path = Path(str(tmp)) / "worktree_checkout"
    run(*git, "worktree", "add", str(worktree_path), "HEAD", "-b", "wt-branch")
    yield worktree_path


@oxitest.mark.timeout(120)
def test_affected_works_in_git_worktree(git_worktree: Fixture[Path]):
    """--affected does not fail with ENOENT in a git worktree.

    Regression test for #778: `git rev-parse --show-toplevel` may return
    a symlinked path in worktrees, causing `current_dir` to fail with
    ENOENT when the canonical path differs.
    """
    worktree_path = git_worktree
    clean_env = {
        k: v for k, v in __import__("os").environ.items() if not k.startswith("GIT_")
    }
    run = lambda *cmd: subprocess.run(  # noqa: E731
        cmd, check=True, capture_output=True, env=clean_env
    )

    # Add a new test file in the worktree and stage it.
    (worktree_path / "test_new.py").write_text("def test_new(): assert True\n")
    wt_git = ["git", "-C", str(worktree_path)]
    run(*wt_git, "add", "test_new.py")

    # Act — run oxitest --affected in the worktree.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oxitest",
            str(worktree_path),
            "--color",
            "never",
            "--affected=HEAD",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(worktree_path),
        env=clean_env,
    )
    out, err, rc = result.stdout, result.stderr, result.returncode

    # Assert — should not fail with ENOENT, and should detect the new file.
    assert "No such file or directory" not in err, (
        f"--affected failed in worktree with ENOENT:\nstderr: {err}\nstdout: {out}"
    )
    helpers.integ.assert_passed(out, rc, count=1)
