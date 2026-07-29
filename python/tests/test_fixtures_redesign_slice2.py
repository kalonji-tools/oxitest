"""Slice-2 acceptance: lifetime="module" end-to-end.

Runs oxitest as a subprocess and asserts on a log the fixture writes itself,
rather than on reporter output — the question is what the fixture actually
did, not how a reporter phrased it. This IS the acceptance boundary for
slice 2; ``test_module_lifetime_scope.py`` gives the fast unit-level feedback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from oxitest import TempDir, helpers

_TESTS_ROOT = Path(__file__).parent
_DATA_ROOT = _TESTS_ROOT / "data"
_PROJECT = _DATA_ROOT / "slice2_module_lifetime"

#: Test counts baked into the data-project's two modules.
_ALPHA_TESTS = 3
_BETA_TESTS = 2


@dataclass(frozen=True)
class _Run:
    """One acceptance run: the process output plus the fixture's own log."""

    stdout: str
    stderr: str
    rc: int
    events: tuple[str, ...]

    @property
    def setups(self) -> tuple[str, ...]:
        return tuple(e for e in self.events if e.startswith("SETUP"))

    @property
    def teardowns(self) -> tuple[str, ...]:
        return tuple(e for e in self.events if e.startswith("TEARDOWN"))

    def uses(self, module: str) -> tuple[str, ...]:
        return tuple(e for e in self.events if e == f"USE {module}")

    def ids(self, kind: str) -> tuple[str, ...]:
        prefix = f"{kind} "
        return tuple(e[len(prefix) :] for e in self.events if e.startswith(prefix))


def _run_project(tmp: TempDir, *extra_args: str) -> _Run:
    """Run the slice-2 data-project with a fresh log file."""
    log = Path(tmp) / "events.log"
    env = {**os.environ, "SLICE2_LOG": str(log)}
    stdout, stderr, rc = helpers.common.run_oxitest(_PROJECT, *extra_args, env=env)
    events = tuple(log.read_text().splitlines()) if log.exists() else ()
    return _Run(stdout=stdout, stderr=stderr, rc=rc, events=events)


def test_one_instance_and_one_disposal_per_module(tmp: TempDir) -> None:
    """3 tests in one module → 1 instantiation, 1 disposal; 2 modules → 2 of each."""
    run = _run_project(tmp, "--serial")

    assert run.rc == 0, (
        f"acceptance run failed (rc={run.rc})\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert len(run.uses("alpha")) == _ALPHA_TESTS, (
        f"expected {_ALPHA_TESTS} alpha tests to run, saw "
        f"{len(run.uses('alpha'))} — the counts below are meaningless if the "
        f"project did not run as written\nstdout:\n{run.stdout}"
    )
    assert len(run.uses("beta")) == _BETA_TESTS, (
        f"expected {_BETA_TESTS} beta tests to run, saw {len(run.uses('beta'))}"
        f"\nstdout:\n{run.stdout}"
    )
    assert len(run.setups) == 2, (
        f"fixture was built {len(run.setups)} times across 2 modules "
        f"({run.setups}) — module lifetime means exactly one instance per "
        "module, so a higher count is a per-test rebuild and a lower one is "
        "cross-module sharing"
    )
    assert len(run.teardowns) == 2, (
        f"fixture was disposed {len(run.teardowns)} times ({run.teardowns}) — "
        "every module-lifetime instance must be disposed exactly once, or its "
        "cleanup leaks for the rest of the run"
    )
    assert len(set(run.ids("SETUP"))) == 2, (
        f"the two modules shared an instance id ({run.ids('SETUP')}) — each "
        "module must get its own instance"
    )
    assert set(run.ids("SETUP")) == set(run.ids("TEARDOWN")), (
        f"setup ids {run.ids('SETUP')} do not match teardown ids "
        f"{run.ids('TEARDOWN')} — some instance was disposed that was never "
        "built, or built and never disposed"
    )


def test_disposal_happens_at_the_module_boundary(tmp: TempDir) -> None:
    """Each module's instance is disposed before the next module's is built.

    Counting setups and teardowns alone would still pass if every disposal were
    deferred to the end of the run. The event *shape* is what proves disposal is
    tied to the module boundary.
    """
    run = _run_project(tmp, "--serial")

    assert run.rc == 0, f"acceptance run failed (rc={run.rc})\nstdout:\n{run.stdout}"

    blocks: list[list[str]] = []
    for event in run.events:
        if event.startswith("SETUP"):
            blocks.append([])
        assert blocks, (
            f"event {event!r} arrived before any SETUP — a fixture was used "
            f"before it was built\nevents: {run.events}"
        )
        blocks[-1].append(event)

    assert len(blocks) == 2, (
        f"expected one SETUP..TEARDOWN block per module, got {len(blocks)}: "
        f"{run.events}"
    )
    for block in blocks:
        assert block[-1].startswith("TEARDOWN"), (
            f"block {block} does not end in TEARDOWN — the instance outlived "
            "its module, so disposal is not bound to the module boundary"
        )
        used = {e for e in block if e.startswith("USE")}
        assert len(used) == 1, (
            f"block {block} mixes tests from more than one module ({used}) — "
            "two modules shared an instance"
        )


def test_exitfirst_still_disposes(tmp: TempDir) -> None:
    """--exitfirst mid-module must still drain the module scope.

    ``src/pipeline/execution.rs`` calls ``end_module`` on the early-exit path
    before breaking out of the run. Without that, aborting a run would skip
    every module-lifetime teardown.
    """
    root = Path(tmp) / "proj"
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    log = Path(tmp) / "events.log"

    (root / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["pkg"]\npython_files = ["test_*.py"]\n'
    )
    (pkg / "__init__.py").write_text("")
    (pkg / "__fixtures__.py").write_text(
        "from __future__ import annotations\n"
        "import pathlib\n"
        "from collections.abc import Iterator\n"
        "import oxitest as oxi\n\n"
        f"LOG = pathlib.Path({str(log)!r})\n\n\n"
        '@oxi.fixture(lifetime="module")\n'
        "def resource() -> Iterator[str]:\n"
        "    with LOG.open('a') as fh:\n"
        "        fh.write('SETUP\\n')\n"
        "    yield 'res'\n"
        "    with LOG.open('a') as fh:\n"
        "        fh.write('TEARDOWN\\n')\n"
    )
    (pkg / "test_aborts.py").write_text(
        "from oxitest import Fixtures\n\n\n"
        "def test_first(fx: Fixtures) -> None:\n"
        "    assert fx.pkg.resource is not None, 'fixture must be injected'\n\n\n"
        "def test_second_fails(fx: Fixtures) -> None:\n"
        "    assert fx.pkg.resource is None, 'deliberate failure to trip -x'\n\n\n"
        "def test_never_runs(fx: Fixtures) -> None:\n"
        "    assert fx.pkg.resource is not None, 'should not be reached'\n"
    )

    out, err, rc = helpers.common.run_oxitest(None, "-x", cwd=str(root))

    assert rc != 0, (
        f"the project must fail so -x actually aborts; rc={rc}\n"
        f"stdout:\n{out}\nstderr:\n{err}"
    )
    events = log.read_text().splitlines()
    assert events.count("SETUP") == 1, (
        f"expected exactly one instantiation, got {events} — the rest of this "
        f"test is meaningless otherwise\nstdout:\n{out}"
    )
    assert events.count("TEARDOWN") == 1, (
        f"module scope was not drained on the --exitfirst path (events={events}) "
        "— aborting a run silently skips module-lifetime cleanup\n"
        f"stdout:\n{out}"
    )


def test_old_shared_fixture_api_unaffected(tmp: TempDir) -> None:
    """Coexistence: shared=True still routes to the session-wide shared scope.

    Module scope is a new branch ahead of the ``shared`` branch in
    ``_scope_for``; this guards against it swallowing shared fixtures.
    """
    log = Path(tmp) / "shared.log"
    (tmp / "conftest.py").write_text(
        "import pathlib\n"
        "from collections.abc import Iterator\n"
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n"
        f"LOG = pathlib.Path({str(log)!r})\n\n\n"
        "@fx.fixture(shared=True)\n"
        "def resource() -> Iterator[str]:\n"
        "    with LOG.open('a') as fh:\n"
        "        fh.write('SETUP\\n')\n"
        "    yield 'res'\n"
        "    with LOG.open('a') as fh:\n"
        "        fh.write('TEARDOWN\\n')\n"
    )
    for mod in ("alpha", "beta"):
        (tmp / f"test_{mod}.py").write_text(
            "from oxitest import Fixture\n\n\n"
            f"def test_{mod}(resource: Fixture[str]) -> None:\n"
            "    assert resource == 'res', 'shared fixture must still inject'\n"
        )

    out, err, rc = helpers.common.run_oxitest(tmp, "--serial")

    assert rc == 0, f"old shared=True API regressed:\nstdout:\n{out}\nstderr:\n{err}"
    events = log.read_text().splitlines()
    assert events.count("SETUP") == 1, (
        f"shared fixture was built {events.count('SETUP')} times across two "
        "modules — module scope must not capture shared=True fixtures, which "
        "live for the whole session"
    )


def test_failing_module_teardown_is_reported(tmp: TempDir) -> None:
    """A module-lifetime teardown failure must reach the user, naming its module.

    Scope drains swallow the exception and emit a ``Diagnostic`` instead of
    raising, so ``end_module`` returns ``Ok`` and Rust's teardown-warning path
    never fires. Diagnostics were drained only inside the per-test loop, which
    left this failure to surface on some later module's first test — or vanish
    entirely when the failing module ran last, which is the case here.
    """
    root = Path(tmp) / "proj"
    pkg = root / "pkg"
    pkg.mkdir(parents=True)

    (root / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["pkg"]\npython_files = ["test_*.py"]\n'
    )
    (pkg / "__init__.py").write_text("")
    (pkg / "__fixtures__.py").write_text(
        "from __future__ import annotations\n"
        "from collections.abc import Iterator\n"
        "import oxitest as oxi\n\n\n"
        '@oxi.fixture(lifetime="module")\n'
        "def mod_raiser() -> Iterator[str]:\n"
        "    yield 'm'\n"
        "    msg = 'module teardown boom'\n"
        "    raise RuntimeError(msg)\n"
    )
    (pkg / "test_only.py").write_text(
        "from oxitest import Fixtures\n\n\n"
        "def test_uses_raiser(fx: Fixtures) -> None:\n"
        "    assert fx.pkg.mod_raiser == 'm', 'fixture injects before it fails'\n"
    )

    out, err, rc = helpers.common.run_oxitest(
        None, "--serial", "--warnings", cwd=str(root)
    )

    assert rc == 0, (
        f"a failing teardown must not fail the run (rc={rc})\n"
        f"stdout:\n{out}\nstderr:\n{err}"
    )
    assert "module teardown boom" in out, (
        "the teardown failure was swallowed — this is the only module, so "
        "nothing runs afterwards to trigger a drain. Diagnostics must be "
        f"drained after end_module, not only inside the test loop\nstdout:\n{out}"
    )
    assert "test_only.py" in out, (
        "the diagnostic named no module. No single test owns a module-scope "
        "teardown, so without the module path the user cannot tell where the "
        f"failure came from\nstdout:\n{out}"
    )


def test_failing_shared_teardown_is_reported(tmp: TempDir) -> None:
    """The same guarantee at ``end_session`` for the old shared=True API.

    Pre-existing gap with the same root cause: nothing drained diagnostics
    after ``end_session``, so a shared fixture's teardown failure was lost on
    every run.
    """
    (tmp / "conftest.py").write_text(
        "from collections.abc import Iterator\n"
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n\n"
        "@fx.fixture(shared=True)\n"
        "def shared_raiser() -> Iterator[str]:\n"
        "    yield 's'\n"
        "    msg = 'shared teardown boom'\n"
        "    raise RuntimeError(msg)\n"
    )
    (tmp / "test_s.py").write_text(
        "from oxitest import Fixture\n\n\n"
        "def test_s(shared_raiser: Fixture[str]) -> None:\n"
        "    assert shared_raiser == 's', 'shared fixture injects'\n"
    )

    out, err, rc = helpers.common.run_oxitest(tmp, "--serial", "--warnings")

    assert rc == 0, (
        f"a failing teardown must not fail the run (rc={rc})\n"
        f"stdout:\n{out}\nstderr:\n{err}"
    )
    assert "shared teardown boom" in out, (
        "the shared fixture's teardown failure was swallowed — diagnostics "
        f"must be drained after end_session too\nstdout:\n{out}"
    )


def test_lifetime_inversion_resolves_without_crashing(tmp: TempDir) -> None:
    """A module-lifetime fixture depending on a function-lifetime one must not crash.

    This *is* semantically wrong — the long-lived instance captures the first
    test's per-test value and holds it for the whole module. Cap enforcement is
    ADR-0009 Rule 4 and belongs to #1711; slice 2 only pins that the inversion
    degrades quietly rather than exploding, so #1711 can see what it is changing.
    """
    root = Path(tmp) / "proj"
    pkg = root / "pkg"
    pkg.mkdir(parents=True)

    (root / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["pkg"]\npython_files = ["test_*.py"]\n'
    )
    (pkg / "__init__.py").write_text("")
    (pkg / "__fixtures__.py").write_text(
        "from __future__ import annotations\n"
        "import oxitest as oxi\n"
        "from oxitest import Fixture\n\n\n"
        '@oxi.fixture(lifetime="function")\n'
        "def per_test() -> str:\n"
        "    return 'fresh'\n\n\n"
        '@oxi.fixture(lifetime="module")\n'
        "def long_lived(per_test: Fixture[str]) -> str:\n"
        "    return f'holds:{per_test}'\n"
    )
    (pkg / "test_inversion.py").write_text(
        "from oxitest import Fixtures\n\n\n"
        "def test_one(fx: Fixtures) -> None:\n"
        "    assert fx.pkg.long_lived == 'holds:fresh', 'inverted dep resolves'\n\n\n"
        "def test_two(fx: Fixtures) -> None:\n"
        "    assert fx.pkg.long_lived == 'holds:fresh', 'inverted dep resolves'\n"
    )

    out, err, rc = helpers.common.run_oxitest(None, "--serial", cwd=str(root))

    assert rc == 0, (
        f"a module-lifetime fixture depending on a function-lifetime one "
        f"crashed the run (rc={rc}). Slice 2 does not enforce lifetime caps — "
        f"#1711 does — so this must degrade quietly, not raise\n"
        f"stdout:\n{out}\nstderr:\n{err}"
    )


def test_module_lifetime_holds_in_parallel(tmp: TempDir) -> None:
    """Same instantiation/disposal counts under -n 2 as serially.

    The PID guard is load-bearing: if the scheduler collapses the run to one
    process, every assertion below would pass on the serial path and prove
    nothing about the worker. See ``test_worker_session_teardown.py`` for the
    same pattern.
    """
    run = _run_project(tmp, "-n", "2")

    assert run.rc == 0, (
        f"parallel run failed (rc={run.rc})\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    pids = {instance_id.split("-")[0] for instance_id in run.ids("SETUP")}
    assert len(pids) > 1, (
        f"all tests ran in one process ({pids}) — the scheduler collapsed this "
        "run to serial, so the assertions below would pass on the serial path "
        f"and prove nothing about worker teardown\nstdout:\n{run.stdout}"
    )
    assert len(run.setups) == 2, (
        f"fixture was built {len(run.setups)} times ({run.setups}) — module "
        "lifetime must hold per module in workers as it does serially"
    )
    assert len(run.teardowns) == 2, (
        f"fixture was disposed {len(run.teardowns)} times ({run.teardowns}) — "
        "the worker returned without draining its module scope"
    )
