"""Integration tests: strict mode (abort and enforce)."""

from pathlib import Path

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def test_strict_abort_exits_3(tmp: TempDir) -> None:
    """Bare assert with strict = abort exits with code 3 before running tests."""
    (tmp / "test_bare.py").write_text("def test_bare(): assert True\n")
    pyproject = Path(tmp) / "pyproject.toml"
    pyproject.write_text('[tool.oxitest]\nstrict = "abort"\n')
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_collection_error(out, rc)


def test_strict_abort_no_violations_exits_0(tmp: TempDir) -> None:
    """Test with message on assert and strict = abort exits 0."""
    (tmp / "test_clean.py").write_text('def test_clean(): assert True, "should pass"\n')
    pyproject = Path(tmp) / "pyproject.toml"
    pyproject.write_text('[tool.oxitest]\nstrict = "abort"\n')
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc)


def test_strict_enforce_reports_violations(tmp: TempDir) -> None:
    """Strict enforce mode runs all tests but exits 1 when violations are found."""
    (tmp / "test_mixed.py").write_text(
        'def test_bare(): assert True\ndef test_clean(): assert True, "has message"\n'
    )
    pyproject = Path(tmp) / "pyproject.toml"
    pyproject.write_text('[tool.oxitest]\nstrict = "enforce"\n')
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_failed(out, rc)


def test_no_strict_bare_assert_passes(tmp: TempDir) -> None:
    """Without strict config, bare asserts do not cause failures."""
    (tmp / "test_bare_ok.py").write_text("def test_bare_ok(): assert True\n")
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc)


def test_strict_enforce_with_fixtures_and_clean_asserts(tmp: TempDir) -> None:
    """strict=enforce with fixture-using tests passes when all asserts have messages."""
    integ.write_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            strict = "enforce"
        """,
        conftest="""\
            import oxitest as oxi
            fx = oxi.Fixtures()

            @fx.fixture
            def db() -> str:
                return "connected"
        """,
        tests={
            "test_with_fx.py": """\
                from oxitest import Fixture

                def test_uses_fixture(db: Fixture[str]) -> None:
                    assert db == "connected", "db should be connected"
            """,
        },
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc, count=1)
