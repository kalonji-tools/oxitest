"""Regression: the parallel worker must tear down its FixtureSession.

``worker.py`` builds a ``FixtureSession`` per task. Before #1728 it returned
without draining it, so ``_session_scope`` (session-scoped builtins such as
``TempDirFactory``) and ``_shared_scope`` (``shared=True`` user fixtures) never
ran their teardowns in parallel mode. The serial path has always been correct —
``src/pipeline/execution.rs`` calls ``end_module``/``end_session`` — so every
test here contrasts ``--serial`` against ``-n``.

Projects are built inline rather than kept under ``data/`` because each needs a
writable log the assertions read back; writing that into a checked-in fixture
directory would mutate the repo during a test run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from oxitest import StdCapture, TempDir
from oxitest._bridge.worker import _end_task_session
from tests import helpers

_MODULES = ("a", "b")
_TESTS_PER_MODULE = 2


@dataclass
class _StubSession:
    """Records teardown calls; optionally raises from one of them."""

    calls: list[str] = field(default_factory=list)
    fail_on: str | None = None

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_on == name:
            msg = f"boom in {name}"
            raise RuntimeError(msg)

    def end_module(self, _module_path: str) -> None:
        self._record("end_module")

    def end_session(self) -> None:
        self._record("end_session")


def _write_tempdir_project(root: Path, log: Path) -> None:
    """A project whose tests each mktemp() a dir and record its path."""
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "conftest.py").write_text("")
    for mod in _MODULES:
        body = [
            "import os",
            "import pathlib",
            "from oxitest import TempDirFactory",
            "",
            f"LOG = pathlib.Path({str(log)!r})",
            "",
        ]
        for i in range(_TESTS_PER_MODULE):
            body += [
                f"def test_{mod}_{i}(factory: TempDirFactory) -> None:",
                "    d = factory.mktemp('x').path",
                "    with LOG.open('a') as fh:",
                "        fh.write(f'{os.getpid()} {d}\\n')",
                "    assert d.exists(), 'temp dir must exist while the test runs'",
                "",
            ]
        (pkg / f"test_{mod}.py").write_text("\n".join(body))
    (root / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["pkg"]\npython_files = ["test_*.py"]\n'
    )


def _entries(log: Path) -> list[tuple[str, Path]]:
    """``(pid, path)`` pairs recorded by the run."""
    if not log.exists():
        return []
    return [
        (pid, Path(raw))
        for pid, _, raw in (ln.partition(" ") for ln in log.read_text().splitlines())
        if raw
    ]


def _surviving(log: Path) -> list[Path]:
    """Recorded paths that still exist on disk."""
    return [p for _, p in _entries(log) if p.exists()]


def _worker_pids(log: Path) -> set[str]:
    """Distinct PIDs that ran tests — >1 proves worker subprocesses were used."""
    return {pid for pid, _ in _entries(log)}


def test_tempdir_factory_cleaned_up_in_parallel(tmp: TempDir) -> None:
    """TempDirFactory dirs must be removed after a parallel run.

    ``_TempDirFactoryFixture`` is session-scoped and registers ``factory.close``
    on the session teardown stack, which only ``end_session()`` drains. With no
    teardown call in the worker, every temp dir a parallel run creates survives
    it — an unbounded disk leak under stock config, since nothing here opts in
    to any non-default setting.
    """
    root = Path(tmp) / "proj"
    root.mkdir()
    log = Path(tmp) / "dirs.log"
    _write_tempdir_project(root, log)

    out, err, rc = helpers.run_oxitest(None, "--serial", cwd=str(root))
    assert rc == 0, f"serial baseline must pass\nstdout:\n{out}\nstderr:\n{err}"
    created = len(log.read_text().splitlines())
    assert created == len(_MODULES) * _TESTS_PER_MODULE, (
        f"every test must record its temp dir, got {created} — "
        "the rest of this test is meaningless if the project did not run"
    )
    assert not _surviving(log), (
        "serial path already cleans up; if this fails the baseline is broken, "
        "not the parallel path"
    )

    log.unlink()
    out, err, rc = helpers.run_oxitest(None, "-n", "4", cwd=str(root))
    assert rc == 0, f"parallel run must pass\nstdout:\n{out}\nstderr:\n{err}"
    pids = _worker_pids(log)
    assert len(pids) > 1, (
        f"all tests ran in one process ({pids}) — the scheduler collapsed this "
        "run to serial, so the assertion below would pass on the serial path "
        "and prove nothing about worker teardown. See arrange.rs: a shared "
        f"fixture or a threshold change can cause this\nstdout:\n{out}"
    )
    survivors = _surviving(log)
    assert not survivors, (
        f"{len(survivors)} temp dir(s) outlived the parallel run — the worker "
        "returned without calling end_session(), so TempDirFactory.close never "
        "ran. Every parallel run leaks its temp dirs:\n"
        + "\n".join(str(p) for p in survivors[:5])
    )


def test_shared_fixture_teardown_runs_in_worker(tmp: TempDir) -> None:
    """A shared=True fixture must be torn down once per worker session.

    ``auto_arrange = false`` is required: modules using a shared fixture are
    normally pinned to the main process by the arrange stage, which masks this
    entirely. Disabling arrangement is what pushes them into workers.
    """
    root = Path(tmp) / "proj"
    root.mkdir()
    log = Path(tmp) / "events.log"
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "conftest.py").write_text(
        "import os\n"
        "import pathlib\n"
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n"
        f"LOG = pathlib.Path({str(log)!r})\n\n\n"
        "@fx.fixture(shared=True)\n"
        "def resource() -> str:\n"
        "    with LOG.open('a') as fh:\n"
        "        fh.write(f'SETUP {os.getpid()}\\n')\n"
        "    yield 'res'\n"
        "    with LOG.open('a') as fh:\n"
        "        fh.write(f'TEARDOWN {os.getpid()}\\n')\n"
    )
    for mod in _MODULES:
        body = ["from oxitest import Fixture", ""]
        for i in range(_TESTS_PER_MODULE):
            body += [
                f"def test_{mod}_{i}(resource: Fixture[str]) -> None:",
                "    assert resource == 'res', 'shared fixture must be injected'",
                "",
            ]
        (pkg / f"test_{mod}.py").write_text("\n".join(body))
    (root / "pyproject.toml").write_text(
        "[tool.oxitest]\n"
        'testpaths = ["pkg"]\n'
        'python_files = ["test_*.py"]\n'
        "auto_arrange = false\n"
    )

    out, err, rc = helpers.run_oxitest(None, "-n", "4", cwd=str(root))
    assert rc == 0, f"parallel run must pass\nstdout:\n{out}\nstderr:\n{err}"
    events = log.read_text().splitlines()
    setups = [e for e in events if e.startswith("SETUP")]
    teardowns = [e for e in events if e.startswith("TEARDOWN")]
    pids = {e.split()[1] for e in setups}
    assert len(pids) > 1, (
        f"the fixture was built in one process ({pids}) — auto_arrange=false "
        "should have pushed these modules into workers; if arrangement now "
        "pins them to the main process anyway, the teardown assertion below "
        f"passes on the serial path and proves nothing\nstdout:\n{out}"
    )
    assert len(teardowns) == len(setups), (
        f"{len(setups)} setup(s) but {len(teardowns)} teardown(s) — each worker "
        "session builds its own shared fixture, so each must drain it; the "
        "worker returned without calling end_session()"
    )


def test_end_task_session_calls_both_teardowns_in_order() -> None:
    """end_module must run before end_session, matching the serial path.

    execution.rs drains modules as the run proceeds and the session at the end;
    reversing that here would let a session-scoped teardown run before a
    module-scoped one that may still depend on it.
    """
    session = _StubSession()

    _end_task_session(session, ["pkg/test_a.py"])

    assert session.calls == ["end_module", "end_session"], (
        f"expected end_module then end_session, got {session.calls} — order "
        "mirrors src/pipeline/execution.rs and must not drift from it"
    )


def test_end_module_failure_does_not_skip_end_session(capture: StdCapture) -> None:
    """A failing end_module must not strand the session scopes.

    These are independent drains. Sharing one try block would mean a module
    cache eviction error silently leaks every temp dir the session created —
    the exact bug this whole change exists to fix.

    ``capture`` also keeps the emitted diagnostic off the real stdout, which
    would otherwise interleave a raw JSON line into the runner's own output.
    """
    session = _StubSession(fail_on="end_module")

    _end_task_session(session, ["pkg/test_a.py"])

    assert session.calls == ["end_module", "end_session"], (
        f"end_session must still run after end_module raised, got {session.calls}"
    )
    contexts = [
        json.loads(ln)["context"]
        for ln in capture.readouterr().out.splitlines()
        if ln.strip()
    ]
    assert contexts == ["end_module(pkg/test_a.py)"], (
        f"only the failing teardown should be reported, got {contexts} — "
        "a diagnostic for the successful end_session would be a false alarm"
    )


def test_teardown_failure_is_reported_and_swallowed(capture: StdCapture) -> None:
    """A raising teardown emits a WARNING diagnostic and does not propagate.

    Propagating would kill the worker and discard results it already emitted
    for this task. Note the diagnostic is best-effort: drain.rs returns as soon
    as it has read `expected` results, so a line written after the final result
    is usually never consumed. The swallowing is what matters; the diagnostic
    is a bonus when anything is still listening.
    """
    session = _StubSession(fail_on="end_session")

    _end_task_session(session, ["pkg/test_a.py"])

    lines = [ln for ln in capture.readouterr().out.splitlines() if ln.strip()]
    diagnostics = [
        json.loads(ln) for ln in lines if json.loads(ln).get("type") == "diagnostic"
    ]
    assert len(diagnostics) == 1, (
        f"expected exactly 1 diagnostic for the failed teardown, got {diagnostics} "
        "— a silent failure gives the user no signal at all"
    )
    assert diagnostics[0]["severity"] == "warning", (
        f"teardown failure is a warning, not an error: {diagnostics[0]} — an "
        "error severity would imply the run itself failed, which it did not"
    )
    assert diagnostics[0]["context"] == "end_session", (
        f"context must name the failing teardown so the user can locate it, "
        f"got {diagnostics[0]['context']!r}"
    )
