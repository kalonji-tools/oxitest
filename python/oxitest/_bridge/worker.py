"""Worker entry point for the parallel test runner.

Reads a single JSON task from stdin, runs each test item using executor.run_test,
and writes one JSON result line per test to stdout.

Task schema (stdin):
    {
        "module_path": str,
        "items": [{
            "fn_name": str,
            "param_id": str | null,
            "node_id": str,
            "markers": [str]
        }],
        "conftest_paths": [str],
        "timeout_secs": int | null,
        "keep_tmp": str,
        "show_locals": bool | null,
        "show_internals": bool | null
    }

Result schema (stdout, one line per test):
    {
        "node_id": str,
        "outcome": "passed" | "failed" | "error" | "skipped" | ...,
        "duration_ms": float,
        "failure_repr": str | null,
        "message": str | null,
        "file": str | null,
        "lineno": int | null,
        "source_line": str | null,
        "no_message_lines": [int],
        "left": str | null,
        "right": str | null,
        "op": str | null,
        "strict": bool
    }
"""

from __future__ import annotations

__all__ = ["_emit", "main", "run"]

import contextlib
import io
import json
import os
import sys
import time
import types
from typing import TYPE_CHECKING, NotRequired, TypedDict

if TYPE_CHECKING:
    # Type-only — `from __future__ import annotations` keeps these out of the
    # runtime path, which matters because worker startup is on the hot path.
    from typing import Protocol

    class _TeardownTarget(Protocol):
        """Just the lifecycle drains, so teardown can be tested with a stub."""

        def end_module(self, module_path: str, /) -> None: ...

        def end_session(self) -> None: ...


try:
    import coverage as _coverage
except ImportError:
    _coverage: types.ModuleType | None = None


def _emit(
    obj: dict[str, object],
    out: io.TextIOBase | None = None,
) -> None:
    """Write a JSON object as a single line to the worker IPC channel.

    All worker → Rust communication flows through this function.
    The ``out`` parameter exists for testing; production uses ``sys.stdout``.
    """
    target = out or sys.stdout
    target.write(json.dumps(obj) + "\n")
    target.flush()


class WorkerTaskItem(TypedDict):
    """A single test item within a worker task."""

    fn_name: str
    param_id: str | None
    node_id: str
    markers: list[str]


class WorkerTask(TypedDict):
    """JSON task sent from the Rust coordinator to a worker subprocess.

    Must stay in sync with ``WorkerTask`` in ``src/worker_result.rs``.
    """

    module_path: str
    items: list[WorkerTaskItem]
    conftest_paths: list[str]
    timeout_secs: int | None
    keep_tmp: str
    show_locals: NotRequired[bool]
    show_internals: NotRequired[bool]


def _maybe_start_coverage() -> None:
    """Activate coverage collection if the parent process requested it."""
    if os.environ.get("COVERAGE_PROCESS_START") and _coverage is not None:
        _coverage.process_startup()


def run(task: WorkerTask) -> None:
    # Imports are kept lazy — top-level loading adds ~35ms to worker subprocess startup.
    # PLC0415 is suppressed for this file in ruff per-file-ignores.
    from oxitest._bridge._runners import DebugContext
    from oxitest._bridge._test_kind import from_wire
    from oxitest._bridge._test_meta import TestMeta
    from oxitest._bridge.conftest_loader import create_session
    from oxitest._bridge.executor import run_test
    from oxitest._bridge.importer import collect_module

    module_path: str = task["module_path"]
    items: list[WorkerTaskItem] = task["items"]
    conftest_paths: list[str] = task.get("conftest_paths", [])
    timeout_secs: int | None = task.get("timeout_secs")
    keep_tmp: str = task.get("keep_tmp", "cleanup")
    debug = DebugContext(
        show_locals=task.get("show_locals", False),
        show_internals=task.get("show_internals", False),
    )

    session, _violations, _diagnostics = create_session(conftest_paths)

    # Register fixtures declared in the test module itself (e.g. a Fixtures()
    # instance at module level). This mirrors what the serial runner does via
    # collect_module during collection, so self-contained test files that define
    # their own fixtures work correctly in parallel mode too.
    try:
        # Register fixtures — skip for pure doctest modules that aren't test files.
        # Inside the try: collect_module imports the test module, which can raise
        # anything (a SyntaxError in the file, say) and must not skip teardown.
        with contextlib.suppress(ImportError, ModuleNotFoundError):
            collect_module(module_path, session)

        for item in items:
            meta = TestMeta(
                module_path=module_path,
                fn_name=item["fn_name"],
                node_id=item["node_id"],
                kind=from_wire(item.get("param_id")),
                markers=frozenset(item.get("markers", [])),
            )

            start = time.monotonic()
            result = run_test(
                meta,
                session=session,
                default_timeout=timeout_secs,
                keep_tmp=keep_tmp,
                debug=debug,
            )
            duration_ms = (time.monotonic() - start) * 1000.0
            _emit(result.to_wire(meta.node_id, duration_ms))
    finally:
        _end_task_session(session, module_path)


def _end_task_session(session: _TeardownTarget, module_path: str) -> None:
    """Drain the task's fixture session, mirroring the serial path's teardown.

    Failures are swallowed: raising here would discard results this task has
    already emitted, and the serial path routes them to
    ``record_teardown_warning`` rather than aborting. Each teardown gets its
    own ``try`` so a failing ``end_module`` cannot skip ``end_session``.
    """
    from oxitest._bridge.result import Diagnostic, DiagnosticSeverity

    for context, teardown in (
        (f"end_module({module_path})", lambda: session.end_module(module_path)),
        ("end_session", session.end_session),
    ):
        try:
            teardown()
        except Exception as exc:  # noqa: BLE001 — teardown must not kill the worker
            _emit(
                Diagnostic(
                    severity=DiagnosticSeverity.WARNING,
                    context=context,
                    message=f"teardown error: {exc}",
                ).to_wire()
            )


def main() -> None:
    """Read newline-delimited JSON tasks from stdin, write one result line per test."""
    _maybe_start_coverage()
    # Force line buffering on stdout so each print() flushes on newline.
    # Piped stdout defaults to block buffering (8KB), which starves the
    # Rust watchdog — it expects one result line per test.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    for raw in sys.stdin:
        line = raw.strip()
        if line:
            try:
                task: WorkerTask = json.loads(line)
            except json.JSONDecodeError as exc:
                from oxitest._bridge.result import Diagnostic, DiagnosticSeverity

                _emit(
                    Diagnostic(
                        severity=DiagnosticSeverity.WARNING,
                        context="worker",
                        message=f"malformed JSON from coordinator: {exc}",
                    ).to_wire()
                )
                continue
            run(task)


if __name__ == "__main__":
    main()
