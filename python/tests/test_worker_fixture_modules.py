"""Parallel workers must see the same ``@oxi.fixture`` declarations as serial.

Workers build their own ``FixtureSession``, so every ``__fixtures__.py`` the
coordinator discovered is sent with each task and registered before the item
loop. Without that, the whole ADR-0009 declaration layer is invisible under
``-n`` and every test using one fails with "no fixture namespace" (#1732).

The multi-package tests sit behind a PID-count guard: the scheduler can
collapse a run to a single process, and then they would pass on the serial
path and prove nothing. The single-module repro test cannot be guarded that
way — see its docstring.
"""

from __future__ import annotations

from pathlib import Path

from oxitest import TempDir
from tests import helpers

_TESTS_ROOT = Path(__file__).parent
_DATA_ROOT = _TESTS_ROOT / "data"

#: Enough modules that the scheduler has something to spread across workers.
_PACKAGES = ("pkg_a", "pkg_b", "pkg_c", "pkg_d")


def _write_multi_package_project(root: Path, log: Path, *, cross_package: bool) -> None:
    """A project of N packages, each with its own ``__fixtures__.py``.

    ``auto_arrange = false`` is what pushes modules into workers; arrangement
    otherwise pins them to the main process and the run never fans out.

    Each fixture records the PID that built it, so a test can prove the run
    genuinely used more than one process before asserting anything about
    fixture resolution.
    """
    testpaths = ", ".join(f'"{p}"' for p in _PACKAGES)
    (root / "pyproject.toml").write_text(
        "[tool.oxitest]\n"
        f"testpaths = [{testpaths}]\n"
        'python_files = ["test_*.py"]\n'
        "auto_arrange = false\n"
    )
    for pkg in _PACKAGES:
        d = root / pkg
        d.mkdir()
        (d / "__init__.py").write_text("")
        (d / "__fixtures__.py").write_text(
            "from __future__ import annotations\n"
            "import os\n"
            "import pathlib\n"
            "import oxitest as oxi\n\n"
            f"LOG = pathlib.Path({str(log)!r})\n\n\n"
            '@oxi.fixture(lifetime="function")\n'
            "def res() -> str:\n"
            "    with LOG.open('a') as fh:\n"
            "        fh.write(f'{os.getpid()}\\n')\n"
            f"    return {pkg!r}\n"
        )
        body = ["from oxitest import Fixtures", "", ""]
        body += [
            f"def test_{pkg}_own(fx: Fixtures) -> None:",
            f"    assert fx.{pkg}.res == {pkg!r}, 'own-package fixture must resolve'",
            "",
        ]
        if cross_package:
            # Reach into a different package's namespace. This resolves
            # serially because the coordinator registers every discovered
            # __fixtures__.py into one session — so a worker that saw only
            # its own sibling would diverge.
            other = _PACKAGES[(_PACKAGES.index(pkg) + 1) % len(_PACKAGES)]
            body += [
                f"def test_{pkg}_cross(fx: Fixtures) -> None:",
                f"    assert fx.{other}.res == {other!r}, "
                "'cross-package access must resolve in workers as it does serially'",
                "",
            ]
        (d / f"test_{pkg}.py").write_text("\n".join(body))


def _assert_fanned_out(log: Path, out: str) -> None:
    """Fail loudly if the run collapsed to one process.

    Load-bearing: with a single PID every fixture assertion in this file would
    be exercising the serial path and proving nothing about workers.
    """
    pids = set(log.read_text().split()) if log.exists() else set()
    assert len(pids) > 1, (
        f"the run used {len(pids)} process(es) ({pids or 'none'}) — the "
        "scheduler collapsed it to serial, so the fixture assertions here "
        "would pass on the serial path and prove nothing about workers. See "
        f"arrange.rs: a shared fixture or a threshold change can cause this\n"
        f"stdout:\n{out}"
    )


def test_slice1_repro_project_passes_under_n() -> None:
    """The literal #1732 reproduction: passed `--serial`, errored under `-n 2`.

    Runs the *shipped* data-project, so it guards the real artifact rather than
    a copy — its value is regression fidelity, not depth.

    Deliberately **not** behind a fan-out guard: the project is a single test
    module, so the scheduler can never spread it across two processes and
    `len(pids) > 1` is unsatisfiable by construction. The tests below carry the
    guarded coverage; this one would still pass if the run collapsed to serial.
    """
    out, err, rc = helpers.run_oxitest(_DATA_ROOT / "slice1_same_package", "-n", "2")

    assert rc == 0, (
        f"slice-1 fixtures did not resolve in workers (rc={rc}) — this is the "
        f"#1732 reproduction\nstdout:\n{out}\nstderr:\n{err}"
    )


def test_own_package_fixtures_resolve_in_workers(tmp: TempDir) -> None:
    """Each package's own fixtures resolve in a genuinely fanned-out run."""
    root = Path(tmp) / "proj"
    root.mkdir()
    log = Path(tmp) / "pids.log"
    _write_multi_package_project(root, log, cross_package=False)

    out, err, rc = helpers.run_oxitest(None, "-n", "4", cwd=str(root))

    assert rc == 0, (
        f"own-package fixtures did not resolve under -n (rc={rc})\n"
        f"stdout:\n{out}\nstderr:\n{err}"
    )
    _assert_fanned_out(log, out)
    assert "no fixture namespace" not in out, (
        "the #1732 error is still present — workers are not registering the "
        f"fixture modules\nstdout:\n{out}"
    )


def test_cross_package_access_is_rejected_identically_in_serial_and_parallel(
    tmp: TempDir,
) -> None:
    """A worker must reject a cross-sibling reach exactly as serial does.

    This used to be titled "...matches serial" and asserted both paths
    *succeeded* — before #1713, B1 did not exist yet and every non-inline
    fixture was visible run-wide. The old docstring flagged this in advance:
    "B1 boundary enforcement (#1713) may make this illegal later; until it
    does, the two paths must agree." It has come true: reaching from one
    sibling package into another is now an illegal cross-boundary access, so
    the invariant this test guards is unchanged — serial and parallel must
    agree — but what they must agree *on* flipped from a success to a
    rejection.

    That is a strictly stronger test than the one it replaces: slice 6's
    determinism claim is that the boundary verdict cannot depend on worker
    assignment, ``-k`` selection, or import order. If the full fixture-module
    catalog stopped reaching workers, a cross-boundary access would silently
    degrade from "rejected" to "not found" *in parallel only* — and a test
    that only checked "both succeed" would never have caught that regression
    once cross-package access itself became illegal.

    Deliberately not asserting on the error type or the ``fixture-boundary``
    diagnostic code: nothing raises ``BoundaryError`` yet (that lands in Task
    7), so today this is rejected as ``FixtureNotFoundError`` in both paths.
    Keeping the assertion at the process-exit-code level means this test stays
    green across that change. The diagnostic code itself is pinned in Task 9's
    dedicated slice-6 trees, which already cover serial and ``-n 2``.
    """
    root = Path(tmp) / "proj"
    root.mkdir()
    log = Path(tmp) / "pids.log"
    _write_multi_package_project(root, log, cross_package=True)

    serial_out, serial_err, serial_rc = helpers.run_oxitest(
        None, "--serial", cwd=str(root)
    )
    assert serial_rc != 0, (
        f"serial run succeeded (rc={serial_rc}) at a cross-sibling access — "
        f"B1 should reject this\nstdout:\n{serial_out}\nstderr:\n{serial_err}"
    )

    log.unlink(missing_ok=True)
    out, err, rc = helpers.run_oxitest(None, "-n", "4", cwd=str(root))

    assert rc != 0, (
        f"parallel run succeeded (rc={rc}) at a cross-sibling access that "
        f"serial correctly rejected\nstdout:\n{out}\nstderr:\n{err}"
    )
    assert rc == serial_rc, (
        f"serial rejected with rc={serial_rc} but parallel rejected with "
        f"rc={rc} — the boundary verdict must not depend on worker "
        f"assignment\nstdout:\n{out}\nstderr:\n{err}"
    )
    _assert_fanned_out(log, out)


def test_fixtures_resolve_on_a_warm_cache(tmp: TempDir) -> None:
    """A cached second run must still send the fixture-module list.

    The collection-time hook that gathers this list sits above a cache-hit
    ``continue``; anything placed below it is silently skipped for cached
    modules. A warm run is also the common case in CI, which is where parallel
    matters most.

    ``cross_package=False``: since #1713, cross-package access is a B1
    violation and would fail this run regardless of cache state, which proves
    nothing about the cache-hit branch. Own-package resolution is unaffected
    by B1 and demonstrates the collection-time hook just as well.
    """
    root = Path(tmp) / "proj"
    root.mkdir()
    log = Path(tmp) / "pids.log"
    _write_multi_package_project(root, log, cross_package=False)

    first_out, _, first_rc = helpers.run_oxitest(None, "-n", "4", cwd=str(root))
    assert first_rc == 0, (
        f"cold run failed, so the warm-cache assertion proves nothing\n{first_out}"
    )

    log.unlink(missing_ok=True)
    out, err, rc = helpers.run_oxitest(None, "-n", "4", cwd=str(root))

    assert rc == 0, (
        f"the warm-cache run failed where the cold run passed (rc={rc}) — the "
        "fixture-module list is being collected below the cache-hit branch\n"
        f"stdout:\n{out}\nstderr:\n{err}"
    )
    _assert_fanned_out(log, out)
