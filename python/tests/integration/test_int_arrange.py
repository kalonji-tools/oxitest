"""Integration tests: auto-arrangement by shared fixture dependencies."""

from conftest import helpers
from oxitest import TempDir


def _write_shared_suite(tmp: TempDir):
    """Write a test suite with a shared fixture and mixed tests."""
    (tmp / "conftest.py").write_text(
        "import oxitest as oxi\n"
        "fx = oxi.Fixtures()\n"
        "@fx.fixture(shared=True)\n"
        "def db() -> dict:\n"
        "    return {'ready': True}\n"
        "@fx.fixture\n"
        "def repo(db: oxi.Fixture[dict]) -> dict:\n"
        "    return db\n"
        "__all__ = ['fx']\n"
    )
    (tmp / "test_shared.py").write_text(
        "import oxitest as oxi\n"
        "def test_direct(db: oxi.Fixture[dict]) -> None:\n"
        "    assert db['ready']\n"
        "def test_transitive(repo: oxi.Fixture[dict]) -> None:\n"
        "    assert repo['ready']\n"
    )
    (tmp / "test_plain.py").write_text(
        "def test_plain_a() -> None:\n"
        "    assert True\n"
        "def test_plain_b() -> None:\n"
        "    assert True\n"
    )


def test_auto_arrange_default_passes(tmp: TempDir):
    """Auto-arrangement is on by default and doesn't break test execution."""
    _write_shared_suite(tmp)
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc, count=4)


def test_auto_arrange_no_shared_fixtures(tmp: TempDir):
    """When no shared fixtures exist, auto-arrange is a no-op."""
    (tmp / "test_simple.py").write_text(
        "def test_a() -> None:\n"
        "    assert True\n"
        "def test_b() -> None:\n"
        "    assert True\n"
    )
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc, count=2)
