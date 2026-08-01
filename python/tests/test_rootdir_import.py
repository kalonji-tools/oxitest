"""Acceptance tests for the rootdir sys.path append (#1780).

**None of these pass `cwd=`, and that is the point.** ``run_oxitest`` shells out
to ``python -m oxitest`` (``conftest.py:218``), and ``python -m`` prepends the
CWD to ``sys.path``. Every other data-suite test in this repository passes
``cwd=<suite root>``, so those suites resolve test-tree imports by accident of
the invocation rather than by anything oxitest does. A test written that way
here would pass on unmodified ``main`` and prove nothing.

Omitting ``cwd=`` leaves the subprocess in the test runner's working directory
(the oxitest repo root) while ``find_rootdir`` still resolves the suite, because
it walks *up* from the positional path argument to the suite's own
``pyproject.toml``.
"""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import TempDir
from tests import helpers

_DATA_ROOT = Path(__file__).parent / "data"
_SUITE = _DATA_ROOT / "rootdir_import"

#: 2 in test_alpha.py + 1 in test_beta.py + 1 doctest (helpers.py::describe).
#: The suite's `[tool.oxitest.doctest]` table opts doctest collection in
#: unconditionally — `doctest_enabled()` is driven by the table's presence,
#: not by `--doctest-modules` — so the doctest item is always collected here,
#: not just under the explicit `--doctest-modules` test below.
_SUITE_TESTS = 4


def test_serial_run_resolves_tree_imports() -> None:
    """The suite's absolute tree imports resolve on the serial path."""
    stdout, stderr, rc = helpers.run_oxitest(_SUITE)

    assert rc == 0, (
        f"the suite imports `rootdir_import.helpers` by absolute path; a "
        f"non-zero exit means the rootdir never reached sys.path.\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert f"{_SUITE_TESTS} passed" in stdout, (
        f"expected all {_SUITE_TESTS} tests to run and pass, got:\n{stdout}"
    )


def test_parallel_run_resolves_tree_imports() -> None:
    """Workers are separate processes and get the rootdir over the wire.

    `-n 2` forces the parallel path regardless of test count, so this does not
    depend on the suite being large enough to trip `min_parallel_tests`.
    """
    stdout, stderr, rc = helpers.run_oxitest(_SUITE, "-n", "2")

    assert rc == 0, (
        f"worker subprocesses inherit nothing from the coordinator's sys.path; "
        f"a non-zero exit means `rootdir` never crossed the task wire.\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert f"{_SUITE_TESTS} passed" in stdout, (
        f"expected all {_SUITE_TESTS} tests to pass under -n 2, got:\n{stdout}"
    )


def test_inspect_resolves_tree_imports() -> None:
    """`FixtureSession::new` has two callers and the second is easy to miss.

    The inspect TUI builds its own Python session on a background thread
    (`inspect/mod.rs:131`) and reports failure by dropping a channel sender
    rather than raising — so a missed call site degrades the view silently
    instead of erroring. This asserts on the exit code, which is the only
    signal that survives that design.

    `query fixtures` is used rather than `query tests` — `tests` is an
    instant-tier resource that never calls `FixtureSession::new` at all
    (`needs_python(Tests, None)` is `False`), so it would pass even with a
    broken rootdir. `fixtures` requires Python and loads
    `__fixtures__.py`, which itself imports from the test tree, so a broken
    rootdir surfaces as an `ImportError` here.
    """
    stdout, stderr, rc = helpers.run_oxitest_subcmd(
        _SUITE, "query", "fixtures", "--count"
    )

    assert rc == 0, (
        f"`oxitest query fixtures` reaches SessionReady through the same "
        f"init_session seam; a failure here means the rootdir did not reach "
        f"it.\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )


_NESTED = _DATA_ROOT / "rootdir_import_nested"


def test_import_spelling_is_stable_across_invocations() -> None:
    """Pins spec D1: the entry comes from find_rootdir, not test_tree_root.

    `test_tree_root` is the deepest common ancestor of the *collected* files,
    so narrowing the run moves it. Under that derivation a full run would put
    the project root on sys.path and a single-file run would put the
    subpackage there — the same import statement resolving in one invocation
    and raising ImportError in the other.
    """
    deep = _NESTED / "rootdir_import_nested" / "api" / "test_deep.py"

    full_out, full_err, full_rc = helpers.run_oxitest(_NESTED)
    one_out, one_err, one_rc = helpers.run_oxitest(deep)

    assert full_rc == 0, f"full run failed:\n{full_out}\n{full_err}"
    assert one_rc == 0, (
        f"single-file run failed while the full run passed — the sys.path "
        f"entry is being derived from the collected files rather than from "
        f"the project root (spec D1).\nstdout:\n{one_out}\nstderr:\n{one_err}"
    )


def test_installed_distribution_wins_over_the_tree(tmp: TempDir) -> None:
    """Pins spec D2: append, never prepend.

    A pre-existing `sys.path` entry — simulated via `PYTHONPATH`, standing in
    for an installed distribution — must win over a same-named directory
    inside the suite. If the rootdir entry were prepended instead of
    appended, the tree copy would resolve first and the run would fail in a
    way that has nothing to do with the probe's real contents.

    A decoy literally named `oxitest` cannot detect this: `python -m oxitest`
    imports and caches the real `oxitest` package before
    `ensure_rootdir_importable` ever runs, so a same-named tree directory is
    unreachable regardless of append-vs-prepend order. A decoy named
    `coverage` has the same problem — `_bridge/_coverage.py` imports it
    unconditionally at module load time, ahead of the rootdir append. A
    `PYTHONPATH`-injected probe package sidesteps both: nothing in oxitest's
    own import graph touches it, so it is still unresolved when the rootdir
    append (or, under the mutant, prepend) happens.
    """
    winner = tmp.path / "winner"
    probe_installed = winner / "probe_pkg"
    probe_installed.mkdir(parents=True)
    (probe_installed / "__init__.py").write_text(
        "def source() -> str:\n    return 'installed'\n"
    )

    suite = tmp.path / "shadow"
    pkg = suite / "shadow_pkg"
    pkg.mkdir(parents=True)
    (suite / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["shadow_pkg"]\n'
        'python_files = ["test_*.py"]\nstrict = "abort"\n'
    )
    (pkg / "__init__.py").write_text("")
    decoy = suite / "probe_pkg"
    decoy.mkdir()
    (decoy / "__init__.py").write_text("raise RuntimeError('the decoy was imported')")
    (pkg / "test_shadow.py").write_text(
        "from __future__ import annotations\n\n"
        "import probe_pkg\n\n\n"
        "def test_runs() -> None:\n"
        "    assert probe_pkg.source() == 'installed', (\n"
        "        'the pre-existing sys.path entry must resolve, not the tree copy'\n"
        "    )\n"
    )

    inherited = os.environ.get("PYTHONPATH", "")
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, [str(winner), inherited])),
    }
    stdout, stderr, rc = helpers.run_oxitest(suite, env=env)

    assert rc == 0, (
        f"a directory named `probe_pkg` in the tree shadowed a pre-existing "
        f"sys.path entry — the rootdir entry is being prepended rather than "
        f"appended (spec D2).\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "the decoy was imported" not in stdout + stderr, (
        "the decoy package was imported, which only happens if the tree "
        "entry outranks the pre-existing sys.path entry"
    )


def test_doctests_inherit_the_path_entry() -> None:
    """A doctest importing from the test tree resolves via the executor path.

    No `--doctest-modules` flag here: it would unconditionally overwrite
    `[tool.oxitest.doctest].scope` to `"public"` (`merge.rs`), which would
    re-require a doctest on `make_user` and fail collection for a reason
    unrelated to this test. The suite's pyproject.toml already opts doctest
    collection in via its (narrowly-scoped) `[tool.oxitest.doctest]` table.
    """
    stdout, stderr, rc = helpers.run_oxitest(_SUITE)

    assert rc == 0, (
        f"the doctest in helpers.describe imports from the test tree; a "
        f"failure means the doctest runner does not inherit the appended "
        f"sys.path entry and needs its own plumbing.\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
