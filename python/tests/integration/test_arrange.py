"""Integration tests: arrangement declared with ``@oxi.arrange``.

The input was ``shared=True`` until #1720 re-pointed it at ``lifetime="module"``,
and #1848 retired the tier-derived inference entirely: a component is now the
set of fixtures a collected test named in ``@oxi.arrange``. Arrangement itself
survives, because it is the only thing that gives the coordinator a second
phase, which #1777's acceptance project needs.

The scheduling behaviour lives in ``test_arrange_scheduling.py``; these cover
the surface at the CLI level.
"""

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def _write_shared_suite(tmp: TempDir) -> None:
    """Write a test suite with a shared fixture and mixed tests."""
    (tmp / "__fixtures__.py").write_text(
        "import oxitest as oxi\n"
        "from oxitest import fixture\n"
        "@fixture(lifetime='package')\n"
        "def db() -> dict:\n"
        "    return {'ready': True}\n"
        "@fixture(lifetime='function')\n"
        "def repo(db: oxi.Fixture[dict]) -> dict:\n"
        "    return db\n",
        encoding="utf-8",
    )
    (tmp / "test_shared.py").write_text(
        "import oxitest as oxi\n"
        "def test_direct(db: oxi.Fixture[dict]) -> None:\n"
        "    assert db['ready']\n"
        "def test_transitive(repo: oxi.Fixture[dict]) -> None:\n"
        "    assert repo['ready']\n",
        encoding="utf-8",
    )
    (tmp / "test_plain.py").write_text(
        "def test_plain_a() -> None:\n"
        "    assert True\n"
        "def test_plain_b() -> None:\n"
        "    assert True\n",
        encoding="utf-8",
    )


def test_arrangement_default_passes(tmp: TempDir) -> None:
    """A suite that arranges nothing runs unchanged."""
    _write_shared_suite(tmp)
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc, count=4)


def test_arrangement_without_components(tmp: TempDir) -> None:
    """With no @oxi.arrange anywhere, arrangement is a no-op."""
    (tmp / "test_simple.py").write_text(
        "def test_a() -> None:\n"
        "    assert True\n"
        "def test_b() -> None:\n"
        "    assert True\n",
        encoding="utf-8",
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc, count=2)
