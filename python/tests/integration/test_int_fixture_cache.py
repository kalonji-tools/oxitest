"""Integration tests: fixture cache hit rate reporting."""

from conftest import helpers
from oxitest import TempDir


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
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_excludes(out, "shared fixture cache")


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
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "shared fixture cache", "hits")


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
    out, _, rc = helpers.common.run_oxitest(tmp, "-v")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "db")
