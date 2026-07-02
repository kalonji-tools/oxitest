"""Integration tests: happy path exit codes and summary lines."""

from oxitest import TempDir, helpers


def test_all_pass_exits_zero(tmp: TempDir):
    (tmp / "test_ok.py").write_text(
        "def test_a(): assert 1 == 1\ndef test_b(): assert True\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc)


def test_failure_exits_one(tmp: TempDir):
    (tmp / "test_fail.py").write_text("def test_x(): assert 1 == 2\n")
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_failed(out, rc)


def test_all_skip_exits_zero(tmp: TempDir):
    (tmp / "test_skip.py").write_text(
        "import oxitest\n\n"
        "@oxitest.mark.skip(reason='not ready')\n"
        "def test_skipped(): pass\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "skipped")


def test_xfail_exits_zero(tmp: TempDir):
    (tmp / "test_xfail.py").write_text(
        "import oxitest\n\n"
        "@oxitest.mark.xfail(reason='known bug')\n"
        "def test_expected_fail(): assert False\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "xfailed")


def test_mixed_pass_and_fail(tmp: TempDir):
    (tmp / "test_mix.py").write_text(
        "def test_good(): assert True\ndef test_bad(): assert False\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_failed(out, rc)
    helpers.integ.assert_contains(out, "passed", "failed")


def test_no_tests_collected(tmp: TempDir):
    (tmp / "test_empty.py").write_text("# no test functions\n")
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc)


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
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc, count=4)
