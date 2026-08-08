"""Slice-5 acceptance: inline @oxi.fixture in test_*.py (#1712).

Runs oxitest as a subprocess: inline fixtures are resolved during collection and
execution of the target project, so the assertions have to be about a real run
rather than about registry state.

The isolation pair in ``slice5_inline_fixtures`` is the load-bearing part. A
filter that blocked every ``ModuleSource`` would satisfy "a sibling cannot see
the inline fixture" exactly as well as a correct one, so the same project also
asserts that a package-level fixture stays visible from both files.
"""

from __future__ import annotations

import json
from pathlib import Path

from oxitest import TempDir
from tests import helpers

_DATA_ROOT = Path(__file__).parent / "data"
_PROJECT = _DATA_ROOT / "slice5_inline_fixtures"
_INLINE_PACKAGE = _DATA_ROOT / "slice5_inline_package"
_INLINE_SESSION_AT_ROOT = _DATA_ROOT / "slice5_inline_session_at_root"
_CROSS_FILE_INJECTION = _DATA_ROOT / "slice5_inline_cross_file_injection"
_MARK_BESIDE_FIXTURE = _DATA_ROOT / "slice5_mark_beside_fixture"

#: 3 tests in test_inline.py + 2 in test_sibling.py.
_TOTAL_TESTS = 5


def test_inline_fixtures_work_end_to_end() -> None:
    """Lifetimes, isolation, and package-level visibility, in one real run."""
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_PROJECT)

    # Assert
    assert rc == 0, (
        f"the project must pass; rc={rc}\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert f"{_TOTAL_TESTS} passed" in stdout, (
        f"all {_TOTAL_TESTS} tests must run — a collection-level failure would "
        f"skip every isolation assertion and still leave rc==0 unexamined; "
        f"got:\n{stdout}"
    )


def test_inline_package_lifetime_is_rejected() -> None:
    """Inline `package` exceeds the module cap, wherever the file sits."""
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_INLINE_PACKAGE)
    output = stdout + stderr

    # Assert
    assert rc != 0, (
        f'an inline lifetime="package" must fail the run; rc={rc}\n'
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    for expected in ("engine", "test_bad.py", "package", "module", "Hint"):
        assert expected in output, (
            f"the diagnostic must name {expected!r} so the user can act on it "
            f"without reading oxitest's source; got:\n{output}"
        )
    assert "__fixtures__.py" in output, (
        f"the hint must name the concrete file to move the declaration to — a "
        f"generic 'move it elsewhere' is unactionable (#1711 review); got:\n{output}"
    )


def test_inline_session_at_the_rootdir_package_is_rejected() -> None:
    """The case only the home-kind cap catches.

    The location rule from #1711 permits ``session`` when the anchor IS the
    rootdir package, and this declaration sits exactly there. If the two cap axes
    were collapsed into one check, this run would pass and an inline fixture
    would outlive its module.
    """
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_INLINE_SESSION_AT_ROOT)
    output = stdout + stderr

    # Assert
    assert rc != 0, (
        f'an inline lifetime="process" must fail even at the rootdir package, '
        f"where the location rule alone would allow it; rc={rc}\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    # Full `lifetime="..."` literals rather than bare words: the project
    # directory is `slice5_inline_session_at_root`, so a bare "session" check
    # matched the printed path and would have survived the #1777 rename without
    # ever looking at the diagnostic.
    for expected in (
        "cluster",
        "test_bad.py",
        'lifetime="process"',
        'lifetime="module"',
    ):
        assert expected in output, (
            f"the diagnostic must name {expected!r}; got:\n{output}"
        )


def test_inline_fixture_is_not_injectable_across_files() -> None:
    """The Fixture[T] route honours the module restriction too.

    Proxy access resolves by (namespace, name); parameter injection resolves by
    bare name and never sees a namespace. They are two routes into the same
    registry, so filtering one leaves the other open — and this one was open:
    before the fix a sibling file received the fixture as ``FrozenProxy(2)``.
    """
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_CROSS_FILE_INJECTION)
    output = stdout + stderr

    # Assert
    assert rc != 0, (
        f"injecting another file's inline fixture by type must fail the run; "
        f"rc={rc}\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "owned" in output, (
        f"the failure must name the fixture that could not be resolved; got:\n{output}"
    )
    assert "1 passed" in stdout, (
        f"test_owner.py's own test must still pass — the restriction is about "
        f"other files, not about the declaring one; got:\n{stdout}"
    )


def test_a_module_level_mark_object_does_not_break_registration() -> None:
    """Regression for #1757 — the crash that took main red.

    ``_Mark`` defines ``__getattr__``, so it answers every attribute name,
    including the fixture marker. Registration probes module attributes with
    ``getattr``, so a ``None`` guard treats the mark as a declaration and then
    raises ``AttributeError: '_Mark' object has no attribute 'lifetime'`` — which
    kills the worker subprocess rather than failing a test.
    """
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_MARK_BESIDE_FIXTURE)
    output = stdout + stderr

    # Assert
    assert rc == 0, (
        f"a module-level mark object beside an inline fixture must not break "
        f"collection; rc={rc}\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "has no attribute 'lifetime'" not in output, (
        f"the mark object was probed as a fixture marker; the guard must check "
        f"the marker's type, not just that getattr returned something:\n{output}"
    )
    assert "1 passed" in stdout, (
        f"the real inline fixture must still register and resolve; got:\n{stdout}"
    )


def test_inline_fixtures_survive_a_warm_module_cache(tmp: TempDir) -> None:
    """#1850: the second run of a suite using inline fixtures must still pass.

    Inline registration rides on the import `collect_module` performs during
    collection, and a module-cache hit skips that import. Before the fix the
    fixture was therefore absent from the registry on every run after the
    first, and every consuming test failed collection with
    ``fixture 'client' not found``.

    The sibling guard for the package path is
    ``test_warm_cache_preserves_fixture_registration`` in the slice-1 file.

    Two things are load-bearing and easy to break by accident:

    * **no ``strict`` key in the pyproject.** ``strict = "enforce"`` and
      ``strict = "abort"`` both turn on violation collection, which bypasses
      the item cache entirely — under either value this test passes against
      the unfixed code. oxitest's own suite runs ``strict = "abort"``, which
      is exactly why the defect survived in-tree.
    * **the project is built in a TempDir**, not checked in, so the first run
      is guaranteed cold. A ``data/`` project carries an ``.oxitest_cache``
      from earlier suite runs and two suite processes would share it.
    """
    # Arrange
    project = Path(tmp)
    (project / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["proj"]\npython_files = ["test_*.py"]\n',
        encoding="utf-8",
    )
    package = project / "proj"
    package.mkdir()
    (package / "test_inline_client.py").write_text(
        "from __future__ import annotations\n\n"
        "import oxitest as oxi\n"
        "from oxitest import Fixture\n\n\n"
        '@oxi.fixture(lifetime="function")\n'
        "def client() -> str:\n"
        '    return "connected"\n\n\n'
        "def test_uses_the_inline_fixture(client: Fixture[str]) -> None:\n"
        '    assert client == "connected", (\n'
        '        "an inline fixture must resolve on every run, warm cache or not"\n'
        "    )\n",
        encoding="utf-8",
    )

    # Act — first run, cold cache.
    cold_out, cold_err, cold_rc = helpers.run_oxitest(project)

    # Assert — the fixture works at all, and the run really is the
    # cache-eligible configuration the defect needs. Without the cache checks
    # this guard would go vacuously green the day item caching stopped
    # happening (or a `strict` key crept into the pyproject) and would still
    # look like a passing regression test.
    assert cold_rc == 0, (
        f"the cold run must pass — inline fixtures are broken at the root, not "
        f"just on warm cache; rc={cold_rc}\n"
        f"stdout:\n{cold_out}\nstderr:\n{cold_err}"
    )
    timings = project / ".oxitest_cache" / "timings.json"
    assert timings.exists(), (
        f"the cold run must write the item cache; if nothing is cached the "
        f"second run cannot be a warm-cache run and proves nothing:\n"
        f"stdout:\n{cold_out}"
    )
    cached_modules = json.loads(timings.read_text(encoding="utf-8")).get("modules", {})
    assert any("test_inline_client.py" in key for key in cached_modules), (
        f"the test module itself must reach the item cache — that entry is what "
        f"the unfixed code serves on the second run instead of importing (and "
        f"registering) the module; cached keys: {sorted(cached_modules)}"
    )

    # Act — second run, warm cache.
    warm_out, warm_err, warm_rc = helpers.run_oxitest(project)

    # Assert
    assert warm_rc == 0, (
        f"the warm-cache run regressed (#1850): the module-cache hit skipped "
        f"the import that registers the inline @oxi.fixture, so the fixture "
        f"vanished from the registry on the second run; rc={warm_rc}\n"
        f"stdout:\n{warm_out}\nstderr:\n{warm_err}"
    )
    assert "1 passed" in warm_out, (
        f"the test must actually run on the warm pass — a collection error "
        f"reports no test at all; got:\n{warm_out}"
    )


def test_an_aliased_inline_fixture_survives_a_warm_cache_too(tmp: TempDir) -> None:
    """#1850, second spelling: `import oxitest as alias`.

    Registration is by marker attribute at import time, so this declares a real
    fixture. The static declaration scan recognizes only `oxi.`, `oxitest.` and
    bare `fixture`, so it sees nothing here — which made the first cut of the
    fix keep this file cache-eligible and leave the defect live under an
    aliased import. The cache signal is deliberately wider than the declaration
    scan for exactly this case.
    """
    # Arrange
    project = Path(tmp)
    (project / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["proj"]\npython_files = ["test_*.py"]\n',
        encoding="utf-8",
    )
    package = project / "proj"
    package.mkdir()
    (package / "test_aliased_client.py").write_text(
        "from __future__ import annotations\n\n"
        "import oxitest as ox\n"
        "from oxitest import Fixture\n\n\n"
        '@ox.fixture(lifetime="function")\n'
        "def client() -> str:\n"
        '    return "connected"\n\n\n'
        "def test_uses_the_aliased_fixture(client: Fixture[str]) -> None:\n"
        '    assert client == "connected", (\n'
        '        "the import alias must not change whether the fixture resolves"\n'
        "    )\n",
        encoding="utf-8",
    )

    # Act
    cold_out, cold_err, cold_rc = helpers.run_oxitest(project)
    warm_out, warm_err, warm_rc = helpers.run_oxitest(project)

    # Assert
    assert cold_rc == 0, (
        f"the cold run must pass — an aliased decorator still registers by "
        f"marker attribute at import; rc={cold_rc}\n"
        f"stdout:\n{cold_out}\nstderr:\n{cold_err}"
    )
    assert warm_rc == 0, (
        f"the warm-cache run regressed (#1850) for the aliased spelling: the "
        f"cache-eligibility signal must be wider than the declaration scan, or "
        f"an aliased import silently loses its fixture; rc={warm_rc}\n"
        f"stdout:\n{warm_out}\nstderr:\n{warm_err}"
    )
    assert "1 passed" in warm_out, (
        f"the test must actually run on the warm pass; got:\n{warm_out}"
    )
