"""Integration tests: happy path exit codes and summary lines."""

import subprocess
import sys

from oxitest import TempDir


def _run(tmp: TempDir, *extra: str) -> tuple[str, int]:
    """Run oxitest against a temp dir, return (stdout, exit_code)."""
    result = subprocess.run(
        [sys.executable, "-m", "oxitest", str(tmp), "--color", "never", *extra],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout, result.returncode


def test_all_pass_exits_zero(tmp: TempDir):
    (tmp / "test_ok.py").write_text(
        "def test_a(): assert 1 == 1\n"
        "def test_b(): assert True\n"
    )
    out, rc = _run(tmp)
    assert rc == 0, f"all-pass should exit 0, got {rc}"
    assert "passed" in out, "summary should mention passed"


def test_failure_exits_one(tmp: TempDir):
    (tmp / "test_fail.py").write_text("def test_x(): assert 1 == 2\n")
    out, rc = _run(tmp)
    assert rc == 1, f"failure should exit 1, got {rc}"
    assert "failed" in out, "summary should mention failed"


def test_all_skip_exits_zero(tmp: TempDir):
    (tmp / "test_skip.py").write_text(
        "import oxitest\n\n"
        "@oxitest.mark.skip(reason='not ready')\n"
        "def test_skipped(): pass\n"
    )
    out, rc = _run(tmp)
    assert rc == 0, f"all-skip should exit 0, got {rc}"
    assert "skipped" in out, "summary should mention skipped"


def test_xfail_exits_zero(tmp: TempDir):
    (tmp / "test_xfail.py").write_text(
        "import oxitest\n\n"
        "@oxitest.mark.xfail(reason='known bug')\n"
        "def test_expected_fail(): assert False\n"
    )
    out, rc = _run(tmp)
    assert rc == 0, f"xfail should exit 0, got {rc}"
    assert "xfailed" in out, "summary should mention xfailed"


def test_mixed_pass_and_fail(tmp: TempDir):
    (tmp / "test_mix.py").write_text(
        "def test_good(): assert True\n"
        "def test_bad(): assert False\n"
    )
    out, rc = _run(tmp)
    assert rc == 1, f"mixed should exit 1, got {rc}"
    assert "passed" in out, "summary should mention passed"
    assert "failed" in out, "summary should mention failed"


def test_no_tests_collected(tmp: TempDir):
    (tmp / "test_empty.py").write_text("# no test functions\n")
    out, rc = _run(tmp)
    assert rc == 0, f"no tests should exit 0, got {rc}"
