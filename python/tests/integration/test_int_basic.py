"""Integration tests: happy path exit codes and summary lines."""

from conftest import helpers
from oxitest import TempDir


def test_all_pass_exits_zero(tmp: TempDir):
    (tmp / "test_ok.py").write_text(
        "def test_a(): assert 1 == 1\ndef test_b(): assert True\n"
    )
    out, rc = helpers.common.run_oxitest(tmp)
    assert rc == 0, f"all-pass should exit 0, got {rc}"
    assert "passed" in out, "summary should mention passed"


def test_failure_exits_one(tmp: TempDir):
    (tmp / "test_fail.py").write_text("def test_x(): assert 1 == 2\n")
    out, rc = helpers.common.run_oxitest(tmp)
    assert rc == 1, f"failure should exit 1, got {rc}"
    assert "failed" in out, "summary should mention failed"


def test_all_skip_exits_zero(tmp: TempDir):
    (tmp / "test_skip.py").write_text(
        "import oxitest\n\n"
        "@oxitest.mark.skip(reason='not ready')\n"
        "def test_skipped(): pass\n"
    )
    out, rc = helpers.common.run_oxitest(tmp)
    assert rc == 0, f"all-skip should exit 0, got {rc}"
    assert "skipped" in out, "summary should mention skipped"


def test_xfail_exits_zero(tmp: TempDir):
    (tmp / "test_xfail.py").write_text(
        "import oxitest\n\n"
        "@oxitest.mark.xfail(reason='known bug')\n"
        "def test_expected_fail(): assert False\n"
    )
    out, rc = helpers.common.run_oxitest(tmp)
    assert rc == 0, f"xfail should exit 0, got {rc}"
    assert "xfailed" in out, "summary should mention xfailed"


def test_mixed_pass_and_fail(tmp: TempDir):
    (tmp / "test_mix.py").write_text(
        "def test_good(): assert True\ndef test_bad(): assert False\n"
    )
    out, rc = helpers.common.run_oxitest(tmp)
    assert rc == 1, f"mixed should exit 1, got {rc}"
    assert "passed" in out, "summary should mention passed"
    assert "failed" in out, "summary should mention failed"


def test_no_tests_collected(tmp: TempDir):
    (tmp / "test_empty.py").write_text("# no test functions\n")
    out, rc = helpers.common.run_oxitest(tmp)
    assert rc == 0, f"no tests should exit 0, got {rc}"


def test_composed_parametrize_runs_cartesian_product(tmp: TempDir):
    """Composed parametrize produces correct number of tests and all pass."""
    (tmp / "test_composed.py").write_text(
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import partial\n"
        "\n"
        "@dataclass\n"
        "class Case:\n"
        "    label: str\n"
        "    multiplier: int\n"
        "\n"
        "@oxitest.parametrize(\n"
        "    fast=partial(Case, label='fast'),\n"
        "    slow=partial(Case, label='slow'),\n"
        ")\n"
        "@oxitest.parametrize(\n"
        "    x2=partial(Case, multiplier=2),\n"
        "    x3=partial(Case, multiplier=3),\n"
        ")\n"
        "def test_math(label: str, multiplier: int) -> None:\n"
        "    assert isinstance(label, str), 'label should be str'\n"
        "    assert multiplier > 0, 'multiplier should be positive'\n"
    )
    out, rc = helpers.common.run_oxitest(tmp)
    assert rc == 0, f"expected exit 0, got {rc}\n{out}"
    assert "4 passed" in out, f"2x2 should produce 4 tests, got: {out}"
