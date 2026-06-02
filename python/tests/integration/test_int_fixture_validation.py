"""Integration tests: fixture name validation at collection time."""

from conftest import helpers
from oxitest import TempDir


def test_fixture_typo_aborts_with_exit_code_3(tmp: TempDir):
    """A fixture name typo aborts before any test runs."""
    conftest = """\
import oxitest

db = oxitest.Fixtures()

@db.fixture
def store() -> int:
    return 42
"""
    test_file = """\
import oxitest

def test_uses_typo(sotre: oxitest.Fixture[int]) -> None:
    assert sotre == 42
"""
    (tmp / "conftest.py").write_text(conftest)
    (tmp / "test_typo.py").write_text(test_file)
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_collection_error(out, rc)
    helpers.integ.assert_contains(out.lower(), "fixture", "sotre")


def test_valid_fixture_names_pass(tmp: TempDir):
    """Valid fixture names do not trigger validation errors."""
    conftest = """\
import oxitest

db = oxitest.Fixtures()

@db.fixture
def store() -> int:
    return 42
"""
    test_file = """\
import oxitest

def test_uses_store(store: oxitest.Fixture[int]) -> None:
    assert store == 42
"""
    (tmp / "conftest.py").write_text(conftest)
    (tmp / "test_ok.py").write_text(test_file)
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc)


def test_did_you_mean_suggestion(tmp: TempDir):
    """A close typo shows a 'did you mean?' suggestion."""
    conftest = """\
import oxitest

db = oxitest.Fixtures()

@db.fixture
def store() -> int:
    return 42
"""
    test_file = """\
import oxitest

def test_typo(sotre: oxitest.Fixture[int]) -> None:
    assert sotre == 42
"""
    (tmp / "conftest.py").write_text(conftest)
    (tmp / "test_typo.py").write_text(test_file)
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_collection_error(out, rc)
    helpers.integ.assert_contains(out.lower(), "did you mean")
    helpers.integ.assert_contains(out, "store")
