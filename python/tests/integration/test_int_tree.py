"""Integration tests: `fixtures --tree` fixture dependency visualization."""

import subprocess
import sys

from conftest import helpers
from oxitest import TempDir


def _run_fixtures_tree(tmp: TempDir, *extra_args: str) -> tuple[str, str, int]:
    """Run `oxitest fixtures --tree` with cwd set to tmp."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oxitest",
            "fixtures",
            "--tree",
            "--color",
            "never",
            *extra_args,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp),
    )
    return result.stdout, result.stderr, result.returncode


def test_tree_basic_output(tmp: TempDir):
    """`fixtures --tree` shows fixture dependency tree and exits 0."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture\n"
        "def config() -> dict:\n"
        "    return {'host': 'localhost'}\n\n"
        "@fx.fixture\n"
        "def db(config):\n"
        "    return f'connected to {config}'\n"
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = _run_fixtures_tree(tmp)
    assert rc == 0, f"`fixtures --tree` should exit 0, got {rc}. stderr: {out}"
    helpers.integ.assert_contains(out, "db", "config")
    assert "└── " in out or "├── " in out, f"tree chars missing: {out!r}"


def test_tree_verbose_shows_tags(tmp: TempDir):
    """`fixtures --tree -v` shows shared/async tags."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture(shared=True)\n"
        "def db() -> str:\n"
        "    return 'db'\n"
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = _run_fixtures_tree(tmp, "-v")
    assert rc == 0, f"exit code: {rc}"
    helpers.integ.assert_contains(out, "shared")


def test_tree_cycle_exits_failure(tmp: TempDir):
    """`fixtures --tree` detects circular deps and exits 1."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture\n"
        "def a(b):\n"
        "    return 'a'\n\n"
        "@fx.fixture\n"
        "def b(a):\n"
        "    return 'b'\n"
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, err, rc = _run_fixtures_tree(tmp)
    assert rc != 0, f"cycle should cause failure, got rc={rc}"
    combined = out + err
    assert "ircular" in combined, f"cycle error missing: {combined!r}"


def test_tree_no_fixtures_shows_builtins(tmp: TempDir):
    """`fixtures --tree` with no conftest still shows built-in fixtures."""
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = _run_fixtures_tree(tmp)
    assert rc == 0, f"exit code: {rc}"
    helpers.integ.assert_contains(out, "TempDir")
