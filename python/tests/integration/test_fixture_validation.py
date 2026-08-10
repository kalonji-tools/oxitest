"""Integration tests: fixture name validation at collection time."""

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def test_fixture_typo_aborts_with_exit_code_3(tmp: TempDir) -> None:
    """A fixture name typo aborts before any test runs."""
    declarations = """\
from oxitest import fixture

@fixture(lifetime='function')
def store() -> int:
    return 42
"""
    test_file = """\
import oxitest

def test_uses_typo(sotre: oxitest.Fixture[int]) -> None:
    assert sotre == 42
"""
    (tmp / "__fixtures__.py").write_text(declarations, encoding="utf-8")
    (tmp / "test_typo.py").write_text(test_file, encoding="utf-8")
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_collection_error(out, rc)
    integ.assert_contains(out.lower(), "fixture", "sotre")


def test_valid_fixture_names_pass(tmp: TempDir) -> None:
    """Valid fixture names do not trigger validation errors."""
    declarations = """\
from oxitest import fixture

@fixture(lifetime='function')
def store() -> int:
    return 42
"""
    test_file = """\
import oxitest

def test_uses_store(store: oxitest.Fixture[int]) -> None:
    assert store == 42
"""
    (tmp / "__fixtures__.py").write_text(declarations, encoding="utf-8")
    (tmp / "test_ok.py").write_text(test_file, encoding="utf-8")
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc)


def test_did_you_mean_suggestion(tmp: TempDir) -> None:
    """A close typo shows a 'did you mean?' suggestion."""
    declarations = """\
from oxitest import fixture

@fixture(lifetime='function')
def store() -> int:
    return 42
"""
    test_file = """\
import oxitest

def test_typo(sotre: oxitest.Fixture[int]) -> None:
    assert sotre == 42
"""
    (tmp / "__fixtures__.py").write_text(declarations, encoding="utf-8")
    (tmp / "test_typo.py").write_text(test_file, encoding="utf-8")
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_collection_error(out, rc)
    integ.assert_contains(out.lower(), "did you mean")
    integ.assert_contains(out, "store")
