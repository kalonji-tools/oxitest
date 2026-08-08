"""Wire protocol v6 — task-side version check and multi-module tasks (#1745, #1780)."""

import json
import os
import subprocess
import sys
from typing import Any

import oxitest as oxi
from oxitest._bridge.conftest_loader import create_session
from oxitest._bridge.result import PROTOCOL_VERSION
from oxitest._bridge.worker import _check_task_protocol, _end_task_session


def _task(**overrides: Any) -> dict[str, Any]:
    """Build a minimal well-formed v6 task; override single keys per test."""
    task: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "modules": [],
        "conftest_paths": [],
        "fixture_modules": [],
        "timeout_secs": None,
        "keep_tmp": "cleanup",
        "rootdir": "/rootdir",
    }
    task.update(overrides)
    return task


class _TeardownRecorder:
    """Records lifecycle calls in order; optionally raises for one module."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[str] = []
        self._fail_on = fail_on

    def end_module(self, module_path: str, /) -> None:
        """Record the drain, raising for the module named in ``fail_on``."""
        self.calls.append(f"end_module:{module_path}")
        if module_path == self._fail_on:
            message = "boom"
            raise RuntimeError(message)

    def end_task(self) -> None:
        """Record the task drain."""
        self.calls.append("end_task")

    def end_process(self) -> None:
        """Record the process drain."""
        self.calls.append("end_process")


def test_matching_version_is_accepted() -> None:
    """A task stamped with the current version passes through untouched."""
    # Arrange
    task = _task()

    # Act
    _check_task_protocol(task)

    # Assert — returning is the assertion. A false rejection would break every
    # run, since the coordinator always stamps the current version.
    assert task["protocol_version"] == PROTOCOL_VERSION, (
        "the check must not mutate the task it validates — run() reads the same "
        "dict immediately afterwards"
    )


def test_mismatched_version_exits() -> None:
    """A task from a coordinator speaking another version is refused."""
    # Arrange
    task = _task(protocol_version=PROTOCOL_VERSION - 1)

    # Act / Assert
    with oxi.raises(SystemExit):
        _check_task_protocol(task)


def test_absent_version_exits() -> None:
    """A pre-v5 task, which carries no version field at all, is refused."""
    # Arrange
    task = _task()
    del task["protocol_version"]

    # Act / Assert — absent must be treated as mismatch, not as "trust it".
    # Trusting it resurrects the KeyError-in-run() failure this check prevents.
    with oxi.raises(SystemExit):
        _check_task_protocol(task)


def test_mismatch_message_names_both_versions_and_the_fix() -> None:
    """The refusal message must be actionable on its own."""
    # Arrange
    task = _task(protocol_version=99)

    # Act
    with oxi.raises(SystemExit) as caught:
        _check_task_protocol(task)

    # Assert — the message is the whole point of the check: a stale extension
    # otherwise fails as KeyError deep inside run(), emitting no result line, so
    # the coordinator's result-side version warning never fires either.
    message = str(caught.value)
    assert "99" in message, "the message must name the version the coordinator sent"
    assert str(PROTOCOL_VERSION) in message, (
        "the message must name the version this worker speaks, or the reader "
        "cannot tell which half of the build is stale"
    )
    assert "just build" in message, (
        "the message must name the command that resolves the mismatch"
    )


def test_end_module_fires_per_module_then_the_wider_drains_once() -> None:
    """Every module drains before the task does, and the task before the process."""
    # Arrange
    target = _TeardownRecorder()

    # Act
    _end_task_session(target, ["a.py", "b.py"])

    # Assert — a package-lifetime fixture disposes at the task drain, so every
    # module's teardown must run before it; otherwise a wider value would
    # already be gone while a module teardown could still reach it.
    #
    # end_process is absent: the session outlives the task, and draining its
    # process tier per task is the defect #1777 removes. main() owns it.
    assert target.calls == ["end_module:a.py", "end_module:b.py", "end_task"], (
        "module teardown must complete for every module in the task before the "
        "task drains, and the process tier must not drain here at all"
    )


def test_end_task_session_continues_after_a_failing_end_module() -> None:
    """One module's teardown blowing up must not strand the rest."""
    # Arrange
    target = _TeardownRecorder(fail_on="a.py")

    # Act
    _end_task_session(target, ["a.py", "b.py"])

    # Assert — skipping the task drain would leak every task-lifetime resource
    # in the run, which is the bug #1728 fixed for the single-module case.
    assert target.calls == ["end_module:a.py", "end_module:b.py", "end_task"], (
        "a failing end_module must not skip the modules after it or the task drain"
    )


def test_end_task_session_drains_each_module_exactly_once() -> None:
    """Three modules drain in task order, once each."""
    # Arrange — mirrors the shape a package task takes under #1710.
    target = _TeardownRecorder()

    # Act
    _end_task_session(target, ["a.py", "b.py", "c.py"])

    # Assert — a late-binding closure over the loop variable would drain the
    # last module three times and never touch the first two.
    assert target.calls == [
        "end_module:a.py",
        "end_module:b.py",
        "end_module:c.py",
        "end_task",
    ], "each module must be drained once, in the order the task listed them"


def test_end_package_is_inert_without_package_declarations() -> None:
    """The package seam exists and is safe to call before #1710 lands."""
    # Arrange — no fixture can declare lifetime="package" yet, so the hook must
    # tolerate a session that has never seen one.

    session, _violations, _diagnostics = create_session([])

    # Act
    session.end_package("tests/api")

    # Assert — #1710 calls this mid-run, between a package's last module and the
    # next task, so the session must stay usable afterwards. Disposal behaviour
    # arrives with the tier; this slice only guarantees the seam.
    assert session.get_cache_stats() is not None, (
        "end_package must leave the session usable — a seam that poisons the "
        "session would surface only once #1710 wires it up"
    )


def _run_worker(task: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    """Feed one task to a real worker subprocess and return the finished process."""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    return subprocess.run(
        [sys.executable, "-m", "oxitest._bridge.worker"],
        input=json.dumps(task),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=env,
        timeout=30,
    )


def test_worker_subprocess_rejects_a_stale_task() -> None:
    """A real worker exits nonzero and says why, rather than dying obscurely."""
    # Arrange — what a v4 coordinator sends: no version field, flat shape.
    task = _task()
    del task["protocol_version"]

    # Act
    proc = _run_worker(task)

    # Assert — the unit tests cover the check in isolation; this covers the
    # thing that actually protects a developer running test-python against a
    # stale extension. A zero exit would let the coordinator wait out its
    # watchdog instead of surfacing the mismatch.
    assert proc.returncode != 0, (
        f"a worker that cannot parse the task must exit nonzero, got "
        f"{proc.returncode} — stderr: {proc.stderr}"
    )
    diagnostics = [
        parsed
        for line in proc.stdout.splitlines()
        if line.strip()
        for parsed in [json.loads(line)]
        if parsed.get("type") == "diagnostic"
    ]
    assert len(diagnostics) == 1, (
        f"the mismatch must reach stdout as a diagnostic, got {diagnostics} — "
        f"a message only on stderr never reaches the reporter"
    )
    assert "just build" in diagnostics[0]["message"], (
        "the emitted diagnostic must carry the actionable message, not just the "
        "SystemExit text the user never sees"
    )


def test_worker_accepts_a_task_missing_rootdir(tmp: oxi.Fixture[oxi.TempDir]) -> None:
    """A task without a `rootdir` key is still accepted and runs to completion.

    A pre-#1780 coordinator (or an in-process unit test building a task dict
    by hand) never sends this key at all. `worker.py:259` reads it with
    `task.get("rootdir")`, which tolerates absence by returning `None` — that
    is the documented contract this test pins, not merely "rootdir has some
    value".
    """
    # Arrange
    solo = tmp.path / "test_solo.py"
    solo.write_text(
        "def test_only():\n    assert True, 'must pass'\n", encoding="utf-8"
    )
    task = _task(
        modules=[
            {
                "module_path": str(solo),
                "items": [
                    {
                        "fn_name": "test_only",
                        "param_id": None,
                        "node_id": f"{solo}::test_only",
                        "markers": [],
                    }
                ],
            }
        ]
    )
    del task["rootdir"]

    # Act
    proc = _run_worker(task)

    # Assert
    results = [
        parsed
        for line in proc.stdout.splitlines()
        if line.strip()
        for parsed in [json.loads(line)]
        if parsed.get("type") == "result"
    ]
    assert proc.returncode == 0, (
        f"a task omitting rootdir must not crash the worker — worker.py uses "
        f".get() specifically so an absent field degrades to no sys.path "
        f"append instead of a KeyError. stderr: {proc.stderr}"
    )
    assert len(results) == 1 and results[0]["node_id"] == f"{solo}::test_only", (
        "the test itself must still run and report, not just avoid crashing"
    )


def test_worker_runs_every_module_in_one_task(tmp: oxi.Fixture[oxi.TempDir]) -> None:
    """A task carrying two modules runs both and reports both."""
    # Arrange — two real test modules, one task, one worker subprocess.

    alpha = tmp.path / "test_alpha.py"
    alpha.write_text(
        "def test_a():\n    assert True, 'alpha must pass'\n", encoding="utf-8"
    )
    beta = tmp.path / "test_beta.py"
    beta.write_text(
        "def test_b():\n    assert True, 'beta must pass'\n", encoding="utf-8"
    )

    def _module(path: object, fn: str) -> dict[str, Any]:
        return {
            "module_path": str(path),
            "items": [
                {
                    "fn_name": fn,
                    "param_id": None,
                    "node_id": f"{path}::{fn}",
                    "markers": [],
                }
            ],
        }

    task = _task(modules=[_module(alpha, "test_a"), _module(beta, "test_b")])

    # Act
    proc = _run_worker(task)

    # Assert
    results = [
        parsed
        for line in proc.stdout.splitlines()
        if line.strip()
        for parsed in [json.loads(line)]
        if parsed.get("type") == "result"
    ]
    assert len(results) == 2, (
        f"one task carrying two modules must emit two results, got {len(results)} — "
        f"the coordinator waits on an exact count and a short read hangs the run. "
        f"stderr: {proc.stderr}"
    )
    assert {r["node_id"] for r in results} == {
        f"{alpha}::test_a",
        f"{beta}::test_b",
    }, (
        "both modules' node ids must come back — a worker that stopped after the "
        "first module would strand the rest of a package under #1710"
    )
