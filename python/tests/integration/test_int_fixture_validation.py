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
    assert rc == 3, f"expected exit code 3 (CollectError), got {rc}"
    assert "fixture" in out.lower() and "sotre" in out.lower(), (
        f"expected fixture error in output: {out}"
    )


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
    assert rc == 0, f"expected exit code 0, got {rc}. Output: {out}"


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
    assert rc == 3, f"expected exit code 3 (CollectError), got {rc}"
    assert "did you mean" in out.lower(), f"expected suggestion in output: {out}"
    assert "store" in out, f"expected 'store' suggestion in output: {out}"
