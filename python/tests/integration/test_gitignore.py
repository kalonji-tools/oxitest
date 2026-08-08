"""Integration tests: .gitignore-aware file discovery."""

import subprocess

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def test_gitignore_respected(tmp: TempDir) -> None:
    """Files in .gitignore'd directories are not collected."""
    (tmp / "test_visible.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (tmp / "ignored_dir").mkdir()
    (tmp / "ignored_dir/test_hidden.py").write_text(
        "def test_hidden(): pass\n", encoding="utf-8"
    )

    subprocess.run(
        ["git", "init"],  # noqa: S607
        cwd=str(tmp),
        check=True,
        capture_output=True,
        env=integ.clean_git_env(),
    )
    (tmp / ".gitignore").write_text("ignored_dir/\n", encoding="utf-8")

    out, _, rc = helpers.run_oxitest(tmp)
    assert rc == 0, f"should exit 0 with 1 passing test, got {rc}"
    assert "1 passed" in out, f"expected 1 passed, got: {out}"
    assert "test_hidden" not in out, f"ignored test should not appear in output: {out}"


def test_no_use_gitignore_disables_filtering(tmp: TempDir) -> None:
    """--no-use-gitignore includes git-ignored files."""
    (tmp / "test_visible.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (tmp / "ignored_dir").mkdir()
    (tmp / "ignored_dir/test_hidden.py").write_text(
        "def test_hidden(): pass\n", encoding="utf-8"
    )

    subprocess.run(
        ["git", "init"],  # noqa: S607
        cwd=str(tmp),
        check=True,
        capture_output=True,
        env=integ.clean_git_env(),
    )
    (tmp / ".gitignore").write_text("ignored_dir/\n", encoding="utf-8")

    out, _, rc = helpers.run_oxitest(tmp, "--no-use-gitignore")
    assert rc == 0, f"should exit 0 with 2 passing tests, got {rc}"
    assert "2 passed" in out, f"expected 2 passed, got: {out}"


def test_no_git_repo_works_normally(tmp: TempDir) -> None:
    """Without a .git directory, file discovery works as before."""
    (tmp / "test_ok.py").write_text("def test_pass(): pass\n", encoding="utf-8")

    out, _, rc = helpers.run_oxitest(tmp)
    assert rc == 0, f"should exit 0, got {rc}"
    assert "1 passed" in out, f"expected 1 passed, got: {out}"
