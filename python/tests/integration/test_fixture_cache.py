"""Integration tests: fixture cache hit rate reporting."""

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def test_no_shared_fixtures_no_cache_stats(tmp: TempDir) -> None:
    """Without shared fixtures, no cache stats line appears."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture\n"
        "def db() -> str:\n"
        "    return 'conn'\n",
        encoding="utf-8",
    )
    (tmp / "test_a.py").write_text(
        "from oxitest import Fixture\n\n"
        "def test_one(db: Fixture[str]):\n"
        "    assert db == 'conn', 'db should be conn'\n",
        encoding="utf-8",
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc)
    integ.assert_excludes(out, "fixture cache")


def test_shared_fixture_shows_cache_stats(tmp: TempDir) -> None:
    """With shared fixtures used by multiple tests, cache stats appear."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture(shared=True)\n"
        "def db() -> str:\n"
        "    return 'conn'\n",
        encoding="utf-8",
    )
    (tmp / "test_a.py").write_text(
        "from oxitest import Fixture\n\n"
        "def test_one(db: Fixture[str]):\n"
        "    assert db == 'conn', 'db should be conn'\n\n"
        "def test_two(db: Fixture[str]):\n"
        "    assert db == 'conn', 'db should be conn'\n",
        encoding="utf-8",
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc)
    integ.assert_contains(out, "fixture cache", "hits")


def _write_module_lifetime_project(tmp: TempDir, *, tests: int) -> None:
    """A package whose only fixture is module-lifetime, used by *tests* tests."""
    pkg = tmp / "pkg"
    pkg.mkdir()
    (tmp / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["pkg"]\npython_files = ["test_*.py"]\n',
        encoding="utf-8",
    )
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__fixtures__.py").write_text(
        "from __future__ import annotations\n"
        "import oxitest as oxi\n\n\n"
        '@oxi.fixture(lifetime="module")\n'
        "def db() -> str:\n"
        "    return 'conn'\n",
        encoding="utf-8",
    )
    body = ["from oxitest import Fixtures", "", ""]
    for i in range(tests):
        body += [
            f"def test_{i}(fx: Fixtures) -> None:",
            "    assert fx.pkg.db == 'conn', 'module fixture should be conn'",
            "",
        ]
    (pkg / "test_a.py").write_text("\n".join(body), encoding="utf-8")


def test_module_lifetime_fixture_shows_cache_stats(tmp: TempDir) -> None:
    """Module-lifetime fixtures must appear in the cache report.

    Their scope is discarded at end_module, so the counters have to be folded
    into the session before the pop. Reading the live scopes instead would
    report no cache activity at all for a run built entirely on this tier.
    """
    _write_module_lifetime_project(tmp, tests=3)

    out, _, rc = helpers.run_oxitest(tmp, "--serial")

    integ.assert_passed(out, rc)
    integ.assert_contains(out, "fixture cache", "hits")
    # 3 tests, one module: the first builds it, the other two hit the cache.
    integ.assert_contains(out, "2/3 hits")


def test_module_lifetime_breakdown_uses_qualified_name(tmp: TempDir) -> None:
    """The -v breakdown names the fixture by the key its scope caches under."""
    _write_module_lifetime_project(tmp, tests=2)

    out, _, rc = helpers.run_oxitest(tmp, "--serial", "-v")

    integ.assert_passed(out, rc)
    integ.assert_contains(out, "pkg.db")


def test_verbose_shows_per_fixture_breakdown(tmp: TempDir) -> None:
    """With -v, per-fixture breakdown appears."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture(shared=True)\n"
        "def db() -> str:\n"
        "    return 'conn'\n",
        encoding="utf-8",
    )
    (tmp / "test_a.py").write_text(
        "from oxitest import Fixture\n\n"
        "def test_one(db: Fixture[str]):\n"
        "    assert db == 'conn', 'db should be conn'\n\n"
        "def test_two(db: Fixture[str]):\n"
        "    assert db == 'conn', 'db should be conn'\n",
        encoding="utf-8",
    )
    out, _, rc = helpers.run_oxitest(tmp, "-v")
    integ.assert_passed(out, rc)
    integ.assert_contains(out, "db")
