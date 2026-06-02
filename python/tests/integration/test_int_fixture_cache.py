"""Integration tests: fixture cache hit rate reporting."""

import subprocess
import sys

from oxitest import TempDir


def _run(tmp: TempDir, *extra_args: str) -> tuple[str, str, int]:
    """Run oxitest with cwd set to tmp."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oxitest",
            str(tmp),
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


def test_no_shared_fixtures_no_cache_stats(tmp: TempDir):
    """Without shared fixtures, no cache stats line appears."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture\n"
        "def db() -> str:\n"
        "    return 'conn'\n"
    )
    (tmp / "test_a.py").write_text(
        "from oxitest import Fixture\n\n"
        "def test_one(db: Fixture[str]):\n"
        "    assert db == 'conn', 'db should be conn'\n"
    )
    out, _, rc = _run(tmp)
    assert rc == 0, f"expected exit 0, got {rc}\nstdout: {out}"
    assert "shared fixture cache" not in out, (
        f"expected no cache stats without shared fixtures: {out!r}"
    )


def test_shared_fixture_shows_cache_stats(tmp: TempDir):
    """With shared fixtures used by multiple tests, cache stats appear."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture(shared=True)\n"
        "def db() -> str:\n"
        "    return 'conn'\n"
    )
    (tmp / "test_a.py").write_text(
        "from oxitest import Fixture\n\n"
        "def test_one(db: Fixture[str]):\n"
        "    assert db == 'conn', 'db should be conn'\n\n"
        "def test_two(db: Fixture[str]):\n"
        "    assert db == 'conn', 'db should be conn'\n"
    )
    out, _, rc = _run(tmp)
    assert rc == 0, f"expected exit 0, got {rc}\nstdout: {out}"
    assert "shared fixture cache" in out, f"expected cache stats in output: {out!r}"
    assert "hits" in out, f"expected 'hits' in output: {out!r}"


def test_verbose_shows_per_fixture_breakdown(tmp: TempDir):
    """With -v, per-fixture breakdown appears."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture(shared=True)\n"
        "def db() -> str:\n"
        "    return 'conn'\n"
    )
    (tmp / "test_a.py").write_text(
        "from oxitest import Fixture\n\n"
        "def test_one(db: Fixture[str]):\n"
        "    assert db == 'conn', 'db should be conn'\n\n"
        "def test_two(db: Fixture[str]):\n"
        "    assert db == 'conn', 'db should be conn'\n"
    )
    out, _, rc = _run(tmp, "-v")
    assert rc == 0, f"expected exit 0, got {rc}\nstdout: {out}"
    assert "db" in out, f"expected 'db' in per-fixture breakdown: {out!r}"
