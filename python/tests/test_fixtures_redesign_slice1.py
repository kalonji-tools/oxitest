"""Slice-1 acceptance: new ModuleSource fixture end-to-end.

Runs oxitest as a subprocess against the two slice-1 data-projects and
asserts on the JSON report / exit code. This IS the acceptance boundary
for slice 1 — the per-task unit tests give fast feedback during
development; this test proves the whole pipeline works end to end.
"""

from __future__ import annotations

import json
from pathlib import Path

from oxitest import TempDir
from tests import helpers

_TESTS_ROOT = Path(__file__).parent
_DATA_ROOT = _TESTS_ROOT / "data"


def test_module_source_end_to_end(tmp: TempDir) -> None:
    """New ModuleSource fixture path resolves + runs end-to-end.

    Runs oxitest against data/slice1_same_package/ and asserts both
    test_gets_conn and test_fresh_per_test pass under the new
    ModuleSource discovery path.
    """
    json_path = Path(tmp) / "report.json"
    out, err, rc = helpers.run_oxitest(
        _DATA_ROOT / "slice1_same_package",
        "--json",
        str(json_path),
    )

    assert rc == 0, f"acceptance run failed (rc={rc})\nstdout:\n{out}\nstderr:\n{err}"
    assert json_path.exists(), (
        f"--json should create the output file; stdout:\n{out}\nstderr:\n{err}"
    )
    data = json.loads(json_path.read_text())
    tests = data["results"]["tests"]
    outcomes = {t["name"]: t["status"] for t in tests}
    assert len(outcomes) == 2, (
        f"expected 2 tests (test_gets_conn + test_fresh_per_test); "
        f"got {len(outcomes)}: {outcomes}\n"
        f"stdout:\n{out}\nstderr:\n{err}"
    )
    assert all(s == "passed" for s in outcomes.values()), (
        f"expected all tests to pass, got: {outcomes} — "
        "new ModuleSource fixture path is not resolving correctly\n"
        f"stdout:\n{out}\nstderr:\n{err}"
    )


def test_old_fixtures_api_still_works(tmp: TempDir) -> None:
    """Coexistence regression: existing conftest-based fixtures still work.

    Runs oxitest on an inline test project that uses the old Fixtures()
    conftest API to confirm it is unaffected by the ModuleSource changes.
    """
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture\n"
        "def db() -> str:\n"
        "    return 'connected'\n"
    )
    (tmp / "test_old_api.py").write_text(
        "from oxitest import Fixture\n\n"
        "def test_uses_old_api(db: Fixture[str]) -> None:\n"
        "    assert db == 'connected', "
        "'old Fixtures() conftest API must still work after ModuleSource changes'\n"
    )

    out, err, rc = helpers.run_oxitest(tmp)
    assert rc == 0, (
        f"old Fixtures() API regressed — coexistence broken:\n"
        f"stdout:\n{out}\n"
        f"stderr:\n{err}"
    )


def test_collision_between_sources_is_loud(tmp: TempDir) -> None:
    """Same fixture name in conftest.py + __fixtures__.py must abort loudly.

    Runs oxitest on data/slice1_collision/ and asserts the run fails with
    a 'declared twice' diagnostic naming both source paths.
    """
    json_path = Path(tmp) / "collision_report.json"
    out, err, rc = helpers.run_oxitest(
        _DATA_ROOT / "slice1_collision",
        "--json",
        str(json_path),
    )

    assert rc != 0, (
        f"collision run should have failed but returned 0.\n"
        f"stdout:\n{out}\nstderr:\n{err}"
    )
    combined = out + err
    assert "declared twice" in combined, (
        f"expected 'declared twice' in collision diagnostic;\n"
        f"stdout:\n{out}\nstderr:\n{err}"
    )
    assert "conftest.py" in combined and "__fixtures__.py" in combined, (
        f"diagnostic must name BOTH source paths;\nstdout:\n{out}\nstderr:\n{err}"
    )


def test_warm_cache_preserves_fixture_registration() -> None:
    """HIGH-1 regression: __fixtures__.py fixtures registered on warm-cache runs.

    On the first (cold-cache) run, test items are imported and cached.
    On the second (warm-cache) run, the per-file `continue` fires early
    in collect_items — before this fix, fixture registration was placed
    AFTER the cache-hit check, so any @oxi.fixture declarations in
    __fixtures__.py were silently absent from the registry on repeat runs.
    """
    data_root = _DATA_ROOT / "slice1_warm_cache"

    # First run — cold cache, populates the item cache.
    out1, err1, rc1 = helpers.run_oxitest(data_root)
    assert rc1 == 0, (
        "first (cold-cache) run must pass; "
        "fixture registration via ModuleSource is broken at the root:\n"
        f"stdout:\n{out1}\nstderr:\n{err1}"
    )

    # Second run — warm cache; fixtures must still be registered.
    out2, err2, rc2 = helpers.run_oxitest(data_root)
    assert rc2 == 0, (
        "warm-cache run regressed — HIGH-1 not fixed: "
        "__fixtures__.py fixtures were not registered when the test module "
        "was served from cache (cache-hit continue bypassed registration):\n"
        f"stdout:\n{out2}\nstderr:\n{err2}"
    )


def test_syntax_error_in_fixtures_py_diagnoses(tmp: TempDir) -> None:
    """MED-1 regression: syntax error in __fixtures__.py must produce a diagnostic.

    Before the fix: Rust prescan returned Unavailable; collection.rs
    silently skipped the bridge call; the user saw fixture-not-found at
    test time with no pointer to the broken file.
    After the fix: a CollectError names __fixtures__.py explicitly.
    """
    inner = tmp / "broken_pkg"
    inner.mkdir()
    (inner / "__init__.py").write_text("")
    (inner / "__fixtures__.py").write_text("def broken(\n")  # syntax error
    (inner / "test_x.py").write_text(
        "def test_something():\n    assert True, 'sanity'\n"
    )
    (tmp / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["broken_pkg"]\npython_files = ["test_*.py"]\n'
    )

    out, err, _rc = helpers.run_oxitest(tmp)
    combined = out + err
    assert "__fixtures__.py" in combined, (
        "a syntax error in __fixtures__.py must produce a diagnostic naming "
        "the file so the user knows where to look — before the fix, the "
        "error was silent and manifested as fixture-not-found at test time:\n"
        f"stdout:\n{out}\nstderr:\n{err}"
    )


def test_decorated_fixture_raising_surfaces_error(tmp: TempDir) -> None:
    """Regression: fixture raising during instantiation must surface as test failure.

    This exercises the full pipeline path for ModuleSource fixtures:
    registration via __fixtures__.py → resolution via Fixtures proxy →
    instantiation error surfaced per-test rather than crashing collection.
    """
    inner = tmp / "raising_pkg"
    inner.mkdir()
    (inner / "__init__.py").write_text("")
    (inner / "__fixtures__.py").write_text(
        "import oxitest as oxi\n\n"
        '@oxi.fixture(lifetime="function")\n'
        "def conn() -> object:\n"
        "    raise RuntimeError('boom from fixture')\n"
    )
    (inner / "test_x.py").write_text(
        "from oxitest import Fixtures\n\n"
        "def test_uses_conn(fx: Fixtures) -> None:\n"
        "    conn = fx.raising_pkg.conn\n"
        "    assert conn is not None, 'should have raised before reaching here'\n"
    )
    (tmp / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["raising_pkg"]\npython_files = ["test_*.py"]\n'
    )

    out, err, rc = helpers.run_oxitest(tmp)
    assert rc != 0, (
        "a fixture that raises during instantiation must cause test failure "
        "(non-zero exit), not a collection crash with exit 0:\n"
        f"stdout:\n{out}\nstderr:\n{err}"
    )


def test_unrecognized_import_alias_diagnoses(tmp: TempDir) -> None:
    """MED-3: decorated functions but no recognized @oxi.fixture must diagnose.

    The Rust prescan only recognizes `oxi` and `oxitest` as namespaces for
    the fixture decorator. An alias outside that set parses fine and yields
    zero declarations — indistinguishable from a __fixtures__.py that simply
    has no fixtures. Without the diagnostic the user gets fixture-not-found
    at test time and no hint that the alias is the cause.
    """
    inner = tmp / "aliased_pkg"
    inner.mkdir()
    (inner / "__init__.py").write_text("")
    (inner / "__fixtures__.py").write_text(
        "import oxitest as testing\n\n"  # only `oxi`/`oxitest` are recognized
        '@testing.fixture(lifetime="function")\n'
        "def conn() -> str:\n"
        "    return 'conn'\n"
    )
    (inner / "test_x.py").write_text(
        "def test_something():\n    assert True, 'sanity'\n"
    )
    (tmp / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["aliased_pkg"]\npython_files = ["test_*.py"]\n'
    )

    out, err, _rc = helpers.run_oxitest(tmp)
    combined = out + err
    assert "__fixtures__.py" in combined, (
        "the diagnostic must name __fixtures__.py — the user cannot act on a "
        "message that does not say which file has the unrecognized alias:\n"
        f"stdout:\n{out}\nstderr:\n{err}"
    )
    assert "alias" in combined, (
        "the diagnostic must point at the import alias as the probable cause; "
        "a generic 'no fixtures found' sends the user hunting in the wrong "
        "place because the decorator looks correct at a glance:\n"
        f"stdout:\n{out}\nstderr:\n{err}"
    )
