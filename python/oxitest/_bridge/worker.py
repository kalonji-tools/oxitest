"""Worker entry point for the parallel test runner.

Reads a single JSON task from stdin, runs each test item using executor.run_test,
and writes one JSON result line per test to stdout.

Task schema (stdin), version from ``PROTOCOL_VERSION`` in ``result.py``:
    {
        "protocol_version": int,
        "modules": [{
            "module_path": str,
            "items": [{
                "fn_name": str,
                "param_id": str | null,
                "node_id": str,
                "markers": [str]
            }]
        }],
        "fixture_modules": [{"module": str, "anchor": str}],
        "plugins": {"modules": [str], "settings": {str: {str: any}}},
        "timeout_secs": int | null,
        "keep_tmp": str,
        "rootdir": str,
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
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

if TYPE_CHECKING:
    # Type-only — `from __future__ import annotations` keeps these out of the
    # runtime path, which matters because worker startup is on the hot path.
    from collections.abc import Callable, Mapping
    from typing import Protocol

    from oxitest._bridge._fixture_registry import FixtureRegistry
    from oxitest._bridge.result import Diagnostic

    class _TeardownTarget(Protocol):
        """Just the lifecycle drains, so teardown can be tested with a stub."""

        def end_module(self, module_path: str, /) -> None: ...

        def end_task(self) -> None: ...

        def end_process(self) -> None: ...

    class _RegistryOwner(Protocol):
        """Just the registry, so fixture registration can take a stub."""

        @property
        def registry(self) -> FixtureRegistry: ...


try:
    import coverage as _coverage
except ImportError:
    _coverage: types.ModuleType | None = None


#: The worker's own handle on the protocol pipe, opened by :func:`main`.
#:
#: A duplicate of file descriptor 1, so it reaches the same pipe the coordinator
#: reads. It exists because fd 1 is not the worker's to keep: ``FdCapture``
#: calls ``os.dup2`` on fd 1 and ``StdCapture`` replaces ``sys.stdout``, and
#: either one used to take the protocol with it — a diagnostic emitted while
#: such a fixture was active went into the capture file, and the captured output
#: came back holding protocol lines (#2147). A fixture can name fd 1 and it can
#: name ``sys.stdout``; it can name neither of these.
#:
#: ``None`` until :func:`main` runs, so importing this module changes nothing and
#: `_emit` keeps its old target.
_protocol_stream: io.TextIOBase | None = None


def _open_protocol_stream() -> io.TextIOBase:
    """Duplicate file descriptor 1 into a stream only this module holds.

    UTF-8 and line buffering are declared here rather than inherited.
    ``force_utf8_streams`` reconfigures ``sys.stdin``, ``sys.stdout`` and
    ``sys.stderr`` and reaches nothing else, and #2004 is the recorded cost of a
    worker stream left on the locale codec: on Windows that is cp1252, and one
    non-ASCII character in a path made every result come back under a node id
    the coordinator never issued. Line buffering is what flushes one result line
    per test, which the coordinator's watchdog depends on.
    """
    return io.TextIOWrapper(
        io.FileIO(os.dup(1), mode="w", closefd=True),
        encoding="utf-8",
        line_buffering=True,
    )


def _emit(
    obj: dict[str, object],
    out: io.TextIOBase | None = None,
) -> None:
    """Write a JSON object as a single line to the worker IPC channel.

    All worker → Rust communication flows through this function.
    The ``out`` parameter exists for testing; production uses the private
    duplicate of file descriptor 1 that :func:`main` opens, falling back to
    ``sys.stdout`` when this module is imported without ``main`` running.
    """
    target = out or _protocol_stream or sys.stdout
    target.write(json.dumps(obj) + "\n")
    target.flush()


class WorkerTaskItem(TypedDict):
    """A single test item within a worker task."""

    fn_name: str
    param_id: str | None
    node_id: str
    markers: list[str]


class WorkerFixtureModule(TypedDict):
    """One ``__fixtures__.py`` to register into this worker's session.

    ``anchor`` is the package directory the namespace is derived from
    (ADR-0009 Rule 5). It is the module's parent today, but slice 3 adds
    ``__init__.py`` as a package-level fixture home, so it is sent rather
    than re-derived here.
    """

    module: str
    anchor: str


class WorkerTaskModule(TypedDict):
    """One module and its test items within a :class:`WorkerTask`.

    A task carries a list of these. The coordinator sends exactly one today;
    #1710 makes a package's whole subtree a single task so a package-lifetime
    fixture can be instantiated exactly once per run.
    """

    module_path: str
    items: list[WorkerTaskItem]


class WorkerPluginInputs(TypedDict):
    """What a worker needs to activate the run's plugins for itself (#1717)."""

    modules: list[str]
    settings: dict[str, dict[str, object]]


class WorkerTask(TypedDict):
    """JSON task sent from the Rust coordinator to a worker subprocess.

    Must stay in sync with ``WorkerTask`` in ``src/worker_result/wire.rs``.
    """

    protocol_version: int
    modules: list[WorkerTaskModule]
    fixture_modules: list[WorkerFixtureModule]
    plugins: NotRequired[WorkerPluginInputs]
    timeout_secs: int | None
    keep_tmp: str
    rootdir: NotRequired[str]
    show_locals: NotRequired[bool]
    show_internals: NotRequired[bool]


def _check_task_protocol(task: Mapping[str, object]) -> None:
    """Reject a task this worker cannot parse, with an actionable message.

    The task wire is versioned separately from results. Without this check a
    stale extension fails with ``KeyError`` deep inside :func:`run`, emitting no
    result line — so the coordinator's result-side version warning never fires
    and the user sees only a dead worker.

    Absent is treated as mismatch: a pre-v5 coordinator sends no version field
    at all, and trusting it would resurrect exactly the failure this prevents.

    Takes a bare mapping rather than :class:`WorkerTask` on purpose — this runs
    *before* the payload is known to have that shape, which is the whole point.
    """
    from oxitest._bridge.result import (
        PROTOCOL_VERSION,
        Diagnostic,
        DiagnosticSeverity,
    )

    got = task.get("protocol_version")
    if got == PROTOCOL_VERSION:
        return
    described = "absent" if got is None else repr(got)
    message = (
        f"task protocol version mismatch: coordinator sent {described}, this "
        f"worker speaks {PROTOCOL_VERSION}. The Rust extension and the Python "
        f"bridge are out of step — run `just build`."
    )
    _emit(
        Diagnostic(
            severity=DiagnosticSeverity.ERROR, context="worker", message=message
        ).to_wire()
    )
    raise SystemExit(message)


class _StreamingDiagnosticSink:
    """Write each diagnostic to the LDJSON pipe the moment it is emitted.

    Satisfies ``DiagnosticSink``. The serial path accumulates into a list that
    Rust drains after each PyO3 call; a worker has no such drain, so anything
    accumulated here would die with the process (#1840).
    """

    def append(self, diagnostic: Diagnostic, /) -> None:
        _emit(diagnostic.to_wire())


def _register_fixture_modules(
    session: _RegistryOwner, fixture_modules: list[WorkerFixtureModule]
) -> None:
    """Register every ``__fixtures__.py`` the coordinator discovered.

    Mirrors ``bridge::register_fixture_module_for_path`` on the serial path.
    Workers build their own sessions, so without this the whole
    ``@oxi.fixture`` declaration layer is invisible to them and every test
    using one fails with a misleading "no fixture namespace" error (#1732).

    The full list is registered, not just this task's sibling: cross-package
    access (``fx.other_pkg.thing``) resolves serially today, and a worker that
    saw only its own package would diverge from that.

    The guard is **defensive, not a known path**. Anything the coordinator can
    detect — a parse error, or a name collision between a ``conftest.py`` and a
    ``__fixtures__.py`` — is a collection error, and collection errors abort
    the run before a single worker spawns. What is left is the unmodelled: the
    file changing between collection and execution, or an import that behaves
    differently in a subprocess. Those should fail the tests needing the
    fixture, not strand the whole group.
    """
    from oxitest._bridge.importer import register_module_source_fixtures_for_module
    from oxitest._bridge.result import Diagnostic, DiagnosticSeverity

    for entry in fixture_modules:
        try:
            register_module_source_fixtures_for_module(
                registry=session.registry,
                fixture_module_path=entry["module"],
                anchor_package_path=entry["anchor"],
            )
        except BaseException as exc:  # noqa: BLE001 — must not kill the worker
            # BaseException, not Exception: a fixture module calling sys.exit()
            # at import time raises SystemExit, which would otherwise take the
            # worker down and strand every test in the group. importer.py
            # catches BaseException around the same import for this reason.
            _emit(
                Diagnostic(
                    severity=DiagnosticSeverity.ERROR,
                    context=f"fixture registration ({entry['module']})",
                    message=f"{type(exc).__name__}: {exc}",
                ).to_wire()
            )


def _activate_plugins(session: Any, plugins: Mapping[str, Any]) -> None:
    """Load the run's plugins into this worker and register their fixtures.

    Workers rebuild their own ``FixtureSession`` and inherit nothing from the
    coordinator, so before this a worker had **no plugins at all** — both
    ``FixtureProvider`` fixtures and plugin ``__fixtures__.py`` declarations
    were invisible under ``-n`` while passing serially. That was true of the
    shipped provider path too, not only the new one (#1717).

    Mirrors what ``pipeline::helpers::init_session`` does serially, in the same
    order: load plugins, register their providers, then prescan-free registration
    of each plugin's ``__fixtures__.py``. The prescan itself is a coordinator
    concern — the file is already known to exist and to declare fixtures, and a
    worker re-deciding that would be a second authority on the question.

    Failures are reported per plugin and do not kill the worker, for the same
    reason ``_register_fixture_modules`` is defensive: anything the coordinator
    could detect has already aborted the run, so what reaches here is the
    unmodelled, and it should fail the tests that need the fixture rather than
    strand the whole group.
    """
    from oxitest._bridge.importer import register_plugin_source_fixtures_for_module
    from oxitest._bridge.plugin_loader import load_plugins, plugin_fixture_homes
    from oxitest._bridge.result import Diagnostic, DiagnosticSeverity

    modules: list[str] = list(plugins.get("modules", ()))
    if not modules:
        return
    settings: dict[str, dict[str, object]] = dict(plugins.get("settings", {}))

    try:
        # The assignment is the whole registration: FixtureSession.__setattr__
        # re-registers plugin fixtures whenever _plugin_registry is replaced.
        # Calling _register_plugin_fixtures() as well registers every provider
        # a second time.
        session._plugin_registry = load_plugins(modules, settings)  # noqa: SLF001
    except BaseException as exc:  # noqa: BLE001 — must not kill the worker
        _emit(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                context="plugin activation",
                message=f"{type(exc).__name__}: {exc}",
            ).to_wire()
        )
        return

    try:
        homes = plugin_fixture_homes(
            activated_modules=modules, plugin_settings=settings
        )
    except BaseException as exc:  # noqa: BLE001 — must not kill the worker
        _emit(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                context="plugin fixture homes",
                message=f"{type(exc).__name__}: {exc}",
            ).to_wire()
        )
        return

    for home in homes:
        try:
            register_plugin_source_fixtures_for_module(
                registry=session.registry,
                fixture_module_path=os.path.join(  # noqa: PTH118 — worker imports stay lazy
                    home.anchor_dir, "__fixtures__.py"
                ),
                plugin_module=home.plugin_module,
                namespace=home.namespace,
                autouse_names=list(home.autouse),
                # The coordinator already told the user how to enable a
                # declared-but-disabled autouse fixture, and it runs before any
                # worker spawns. Repeating it here multiplies the notice by the
                # worker count, which reads as a property of `-n`.
                emit_notices=False,
            )
        except BaseException as exc:  # noqa: BLE001 — must not kill the worker
            _emit(
                Diagnostic(
                    severity=DiagnosticSeverity.ERROR,
                    context=f"plugin fixture registration ({home.plugin_module})",
                    message=f"{type(exc).__name__}: {exc}",
                ).to_wire()
            )


def _maybe_start_coverage() -> None:
    """Activate coverage collection if the parent process requested it."""
    if os.environ.get("COVERAGE_PROCESS_START") and _coverage is not None:
        _coverage.process_startup()


def build_session(task: WorkerTask) -> Any:
    """Build this worker process's one ``FixtureSession`` (#1777).

    Called once per process rather than once per task. That is sound because
    every field it reads is run-constant: the coordinator serialises
    ``fixture_modules`` a single time for the whole run
    and hands every worker the same bytes, and ``rootdir`` comes from the
    config. A task carries them so a worker needs no separate handshake, not
    because they can differ between tasks.

    Registering the fixture modules belongs here for the same reason. Repeating
    it per task would re-register an identical list against a session that
    already has it.
    """
    from oxitest._bridge._session_factory import create_session

    # Optional on the Python side purely so in-process unit tests can build a
    # task dict without mutating the interpreter's sys.path. The coordinator
    # always sends it.
    rootdir: str | None = task.get("rootdir")
    session = create_session(rootdir=rootdir)
    # Plugins before fixture modules, mirroring the serial order in
    # FixtureSession.__init__ (builtins → plugins → conftest): a user's own
    # declaration must be able to shadow a plugin's, which requires the
    # plugin's to be registered first (#1717).
    _activate_plugins(session, task.get("plugins", {}))
    _register_fixture_modules(session, task.get("fixture_modules", []))
    return session


def run(task: WorkerTask, session: Any) -> None:
    """Run one task's tests against the process-lifetime *session*.

    The caller owns the session; ``run`` disposes only what this task's own
    tiers own, through ``_end_task_session``. Anything wider survives for the
    next task, which is what makes ``lifetime="process"`` per process rather
    than per task group.

    **The caller must call :func:`_check_task_protocol` first.** That check
    moved out to ``main()`` when the session became per-process: the session is
    built *from* a task, so the task has to be known readable before there is
    anything to run it against. A caller that skips it gets a ``KeyError`` from
    deep inside this function with no result line emitted — the exact failure
    the check exists to prevent.
    """
    # Imports are kept lazy — top-level loading adds ~35ms to worker subprocess startup.
    # PLC0415 is suppressed for this file in ruff per-file-ignores.
    from oxitest._bridge._errors import is_usage_error
    from oxitest._bridge._runners import DebugContext
    from oxitest._bridge._test_kind import from_wire
    from oxitest._bridge._test_meta import TestMeta
    from oxitest._bridge.executor import run_test
    from oxitest._bridge.importer import collect_module
    from oxitest._bridge.result import _error_result

    modules: list[WorkerTaskModule] = task["modules"]
    timeout_secs: int | None = task.get("timeout_secs")
    keep_tmp: str = task.get("keep_tmp", "cleanup")
    debug = DebugContext(
        show_locals=task.get("show_locals", False),
        show_internals=task.get("show_internals", False),
    )

    # Read every module path up front: a malformed entry raises here, before any
    # test has run, rather than inside the finally where it would mask whatever
    # exception was already in flight.
    module_paths = [module["module_path"] for module in modules]

    # Register fixtures declared in the test module itself (e.g. a Fixtures()
    # instance at module level). This mirrors what the serial runner does via
    # collect_module during collection, so self-contained test files that define
    # their own fixtures work correctly in parallel mode too.
    try:
        for module in modules:
            module_path = module["module_path"]
            # Register fixtures — skip for pure doctest modules that aren't test
            # files. Inside the try: collect_module imports the test module, which
            # can raise anything (a SyntaxError in the file, say) and must not
            # skip teardown.
            with contextlib.suppress(ImportError, ModuleNotFoundError):
                collect_module(module_path, session)

            for item in module["items"]:
                meta = TestMeta(
                    module_path=module_path,
                    fn_name=item["fn_name"],
                    node_id=item["node_id"],
                    kind=from_wire(item.get("param_id")),
                    markers=frozenset(item.get("markers", [])),
                )

                start = time.perf_counter()
                try:
                    result = run_test(
                        meta,
                        session=session,
                        default_timeout=timeout_secs,
                        keep_tmp=keep_tmp,
                        debug=debug,
                    )
                except Exception as exc:  # noqa: BLE001 — must not kill the worker
                    # The serial path funnels this in `bridge.rs`; without the
                    # same funnel here the worker stops and every remaining test
                    # in its group reports as an error. One misuse cost six
                    # results where serial cost one, and the exit codes agreed
                    # at 1 throughout, so no exit-code assertion could see it
                    # (#2185).
                    #
                    # `Exception` and not `BaseException`, unlike the guards
                    # above: this one sits in the per-test loop, where a
                    # KeyboardInterrupt must still reach `main()`'s `finally`
                    # and drain the process tier.
                    result = _error_result(
                        f"{type(exc).__name__}: {exc}",
                        usage_error=is_usage_error(exc),
                    )
                duration_ms = (time.perf_counter() - start) * 1000.0
                _emit(result.to_wire(meta.node_id, duration_ms))
    finally:
        _end_task_session(session, module_paths)


def _end_task_session(session: _TeardownTarget, module_paths: list[str]) -> None:
    """Drain the task's fixture session, mirroring the serial path's teardown.

    Fires ``end_module`` for every module the task carried, in task order, then
    ``end_task`` once. Ordering matters: a wider-lifetime fixture disposes at
    the task drain, so every module teardown must finish first or a module
    teardown could reach a value that is already gone.

    ``end_process`` is deliberately absent. The session outlives this task, and
    draining its process tier here is exactly the bug #1777 fixes — it is what
    made ``lifetime="process"`` rebuild once per task group. ``main()`` owns
    that call.

    Each drain gets its own ``try`` so a failing ``end_module`` cannot skip the
    modules after it, or ``end_task``.
    """
    for path in module_paths:
        _drain(f"end_module({path})", session.end_module, path)
    _drain("end_task", session.end_task)


def _drain(context: str, teardown: Callable[..., None], *args: str) -> None:
    """Run one teardown, reporting failure as a diagnostic instead of raising.

    Raising would discard results the task has already emitted; the serial path
    routes teardown failures to ``record_teardown_warning`` rather than aborting,
    and this mirrors that.
    """
    from oxitest._bridge.result import Diagnostic, DiagnosticSeverity

    try:
        teardown(*args)
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
    # Declare UTF-8 before the first read: the coordinator writes raw UTF-8
    # (serde_json::to_writer), and a codec cannot be changed once reads have
    # begun. Without this the task decodes with the locale codec, which on
    # Windows is cp1252, and one non-ASCII character in a path makes every
    # result come back under a node id the coordinator never issued (#2004).
    from oxitest._bridge._streams import force_utf8_streams

    force_utf8_streams()
    # Take the worker's own handle on the pipe before any fixture can redirect
    # fd 1 or replace sys.stdout (#2147). Before the first task, so a diagnostic
    # emitted during session setup already goes through it.
    global _protocol_stream  # noqa: PLW0603 — one process-wide handle, set once
    _protocol_stream = _open_protocol_stream()
    # Force line buffering on stdout so each print() flushes on newline.
    # Piped stdout defaults to block buffering (8KB), which starves the
    # Rust watchdog — it expects one result line per test.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    # Install before the first task, not per task. Every collector downstream
    # defers to an already-active one: FixtureSession.__init__ only claims
    # the var when none is active, and end_process only clears it when that
    # session was the one that set it. So this single set survives every task
    # — which is right, because a worker's pipe lives exactly as long as the
    # process (#1840).
    from oxitest._bridge._diagnostic_collector import _diagnostic_collector_var

    _diagnostic_collector_var.set(_StreamingDiagnosticSink())
    # One session for the whole process, built from the first task that arrives
    # (#1777). Every task carries identical session inputs, so the first one is
    # as good as any; building lazily keeps a worker that receives no work from
    # paying for conftest loading.
    session: Any = None
    try:
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
                _check_task_protocol(task)
                if session is None:
                    session = build_session(task)
                run(task, session)
    finally:
        # `finally`, not `atexit` and not a signal handler: this process has
        # neither, so it is the only thing that survives the KeyboardInterrupt
        # a Ctrl-C delivers mid-task. Without it every process-lifetime
        # teardown would be skipped on interrupt.
        if session is not None:
            _drain("end_process", session.end_process)


if __name__ == "__main__":
    main()
