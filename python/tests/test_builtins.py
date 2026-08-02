"""Unit and integration tests for oxitest built-in fixtures."""

from __future__ import annotations

import logging
import os
import shutil
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import oxitest
import oxitest._bridge._builtins as builtins_pkg

# Imports needed so that get_type_hints() can resolve annotations in locally
# defined helper functions inside the FixtureSession integration tests.
from oxitest import Fixture, TempDir, TestContext, raises
from oxitest._bridge._builtin_context import _BuiltinContext
from oxitest._bridge._builtins import (
    LogCapture,
    Patcher,
    StdCapture,
    TempDirFactory,
)
from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._builtins._capture import _FdCaptureFixture, _StdCaptureFixture
from oxitest._bridge._builtins._logcapture import (
    StdlibLogBackend,
    _Installed,
    _LogCaptureFixture,
    _Uninstalled,
)
from oxitest._bridge._builtins._patch import _PatcherFixture
from oxitest._bridge._builtins._tempdir import _TempDirFactoryFixture, _TempDirFixture
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._test_meta import TestMeta
from oxitest._bridge.plugin_loader import load_plugins
from oxitest._bridge.result import (
    Diagnostic,
    DiagnosticSeverity,
    ErrorResult,
    FailedResult,
    PassedResult,
    TestResult,
)
from oxitest.plugin import Plugin
from tests import helpers


def _make_builtin_ctx(
    *,
    fn_name: str = "",
    keep_tmp: str = "cleanup",
    inject_scope: str = "function",
    result_cell: list | None = None,
) -> tuple[_BuiltinContext, list[Callable[[], None]]]:
    """Create a ``_BuiltinContext`` with sensible test defaults.

    Returns ``(ctx, teardowns)`` — the teardowns list is needed by
    most tests to verify teardown registration and run cleanup.
    """
    teardowns: list[Callable[[], None]] = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name=fn_name, node_id=""),
        inject_scope=inject_scope,
        teardown_stack=teardowns,
        keep_tmp=keep_tmp,
        result_cell=result_cell if result_cell is not None else [],
    )
    return ctx, teardowns


# ── BuiltinFixture base ───────────────────────────────────────────────────────


def test_builtin_fixture_registration() -> None:
    """A BuiltinFixture subclass is retrievable by type via for_type()."""

    class _Sentinel:
        pass

    class _SentinelFixture(BuiltinFixture, fixture_type=_Sentinel):
        def create(self, **_: Any) -> str:
            return "sentinel"

    try:
        assert BuiltinFixture.for_type(_Sentinel) is _SentinelFixture, (
            "BuiltinFixture.for_type should return the registered subclass for "
            "_Sentinel"
        )
    finally:
        BuiltinFixture._registry.pop(_Sentinel, None)  # noqa: SLF001 — test cleanup of class-level registry


def test_builtin_fixture_for_type_unknown_returns_none() -> None:
    """for_type() returns None for a type that has no registered BuiltinFixture."""
    assert BuiltinFixture.for_type(int) is None, (
        "BuiltinFixture.for_type(int) should return None for an unregistered type"
    )


def test_builtin_fixture_create_raises_not_implemented() -> None:
    """BuiltinFixture.create() raises NotImplementedError on the abstract base class."""
    ctx, _ = _make_builtin_ctx()
    with raises(NotImplementedError):
        BuiltinFixture().create(ctx=ctx)


def test_builtin_context_keep_tmp_default_is_cleanup() -> None:
    """_BuiltinContext.keep_tmp defaults to 'cleanup' (no-op mode)."""
    ctx, _ = _make_builtin_ctx()
    assert ctx.keep_tmp == "cleanup", (
        "_BuiltinContext.keep_tmp should default to 'cleanup'"
    )


def test_builtin_context_keep_tmp_accepts_value() -> None:
    """_BuiltinContext.keep_tmp stores the provided value when explicitly set."""
    ctx, _ = _make_builtin_ctx(keep_tmp="failed")
    assert ctx.keep_tmp == "failed", (
        "_BuiltinContext.keep_tmp should accept and store 'failed'"
    )


# ── TempDir ───────────────────────────────────────────────────────────────────


def test_tempdir_fixture_creates_directory() -> None:
    """_TempDirFixture.create() makes a directory and registers one teardown."""
    ctx, teardowns = _make_builtin_ctx()
    tmp = _TempDirFixture().create(ctx=ctx)

    assert tmp.path.is_dir(), (
        f"_TempDirFixture.create() should produce a directory, got path={tmp.path!r} "
        "(not a dir)"
    )
    assert len(teardowns) == 1, (
        f"_TempDirFixture should register exactly 1 teardown, got {len(teardowns)}"
    )
    teardowns[0]()  # cleanup to avoid /tmp leakage


def test_tempdir_fixture_directory_name_includes_fn_name() -> None:
    """TempDir path name embeds the test function name for identifiable directories."""
    ctx, teardowns = _make_builtin_ctx(fn_name="my_test")
    tmp = _TempDirFixture().create(ctx=ctx)

    assert tmp.path.is_dir(), (
        f"_TempDirFixture.create() should produce a directory when fn_name is given, "
        f"got path={tmp.path!r}"
    )
    assert "my_test" in tmp.path.name, (
        f"TempDir path name should include the test function name 'my_test', got "
        f"{tmp.path.name!r}"
    )
    teardowns[0]()  # cleanup


def test_tempdir_fixture_teardown_removes_directory() -> None:
    """TempDir teardown removes the created directory so tests don't leak /tmp."""
    ctx, teardowns = _make_builtin_ctx()
    tmp = _TempDirFixture().create(ctx=ctx)
    path = tmp.path

    teardowns[0]()
    assert not path.exists(), (
        f"TempDir teardown should remove the directory, but {path!r} still exists"
    )


def test_tempdir_keep_tmp_failed_preserves_on_failure() -> None:
    """keep_tmp='failed' preserves the TempDir when the test result is FailedResult."""
    result_cell: list[TestResult | None] = [None]
    ctx, teardowns = _make_builtin_ctx(
        fn_name="fail_test", keep_tmp="failed", result_cell=result_cell
    )
    tmp = _TempDirFixture().create(ctx=ctx)
    path = tmp.path
    assert path.is_dir(), "TempDir should create a directory"

    # Simulate a failed test result
    result_cell[0] = FailedResult(message="assertion error")

    # Run teardown — should NOT remove the directory
    teardowns[0]()
    assert path.exists(), (
        "TempDir should be preserved when keep_tmp='failed' and the test failed"
    )
    # Manual cleanup
    shutil.rmtree(path, ignore_errors=True)


def test_tempdir_keep_tmp_failed_cleans_on_pass() -> None:
    """keep_tmp='failed' still removes TempDir when the test result is PassedResult."""
    result_cell: list[TestResult | None] = [None]
    ctx, teardowns = _make_builtin_ctx(
        fn_name="pass_test", keep_tmp="failed", result_cell=result_cell
    )
    tmp = _TempDirFixture().create(ctx=ctx)
    path = tmp.path

    # Simulate a passed test result
    result_cell[0] = PassedResult()

    teardowns[0]()
    assert not path.exists(), (
        "TempDir should be cleaned up when keep_tmp='failed' and the test passed"
    )


def test_tempdir_keep_tmp_always_preserves_on_pass() -> None:
    """keep_tmp='always' preserves the TempDir even when the test passes."""
    result_cell: list[TestResult | None] = [None]
    ctx, teardowns = _make_builtin_ctx(
        fn_name="pass_test", keep_tmp="always", result_cell=result_cell
    )
    tmp = _TempDirFixture().create(ctx=ctx)
    path = tmp.path

    result_cell[0] = PassedResult()

    teardowns[0]()
    assert path.exists(), (
        "TempDir should be preserved when keep_tmp='always' even if the test passed"
    )
    shutil.rmtree(path, ignore_errors=True)


def test_tempdir_keep_tmp_failed_preserves_on_error() -> None:
    """keep_tmp='failed' preserves the TempDir when the test result is ErrorResult."""
    result_cell: list[TestResult | None] = [None]
    ctx, teardowns = _make_builtin_ctx(
        fn_name="err_test", keep_tmp="failed", result_cell=result_cell
    )
    tmp = _TempDirFixture().create(ctx=ctx)
    path = tmp.path

    result_cell[0] = ErrorResult(message="boom")

    teardowns[0]()
    assert path.exists(), (
        "TempDir should be preserved when keep_tmp='failed' and the test errored"
    )
    shutil.rmtree(path, ignore_errors=True)


def test_tempdir_keep_tmp_emits_diagnostic(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """Preserved TempDir path emits a NOTICE diagnostic on teardown."""
    result_cell: list[TestResult | None] = [None]
    ctx, teardowns = _make_builtin_ctx(
        fn_name="fail_test", keep_tmp="failed", result_cell=result_cell
    )
    tmp = _TempDirFixture().create(ctx=ctx)
    path = tmp.path

    result_cell[0] = FailedResult(message="oops")
    teardowns[0]()

    assert len(diag_collector) == 1, (
        "TempDir must emit exactly one KEPT diagnostic per preserved directory —"
        f" duplicates clutter the summary: {diag_collector!r}"
    )
    diag = diag_collector[0]
    assert diag.severity == DiagnosticSeverity.NOTICE, (
        "KEPT diagnostics are informational — ERROR or WARNING would alarm users"
        f" for normal --keep-tmp behavior: {diag.severity!r}"
    )
    assert diag.context == "tempdir", (
        "the reporter groups diagnostics by context — wrong context would scatter"
        f" KEPT messages across unrelated sections: {diag.context!r}"
    )
    assert str(path) in diag.message, (
        "without the path, users cannot find the preserved directory on disk:"
        f" {diag.message!r}"
    )
    assert "--keep-tmp" in diag.message, (
        "the --keep-tmp hint tells users WHY the directory was preserved —"
        f" without it, a leftover dir looks like a cleanup bug: {diag.message!r}"
    )
    shutil.rmtree(path, ignore_errors=True)


# ── TempDirFactory ────────────────────────────────────────────────────────────


def test_tempdir_factory_mktemp_creates_distinct_dirs() -> None:
    """mktemp() with different names produces two distinct existing directories."""
    ctx, _ = _make_builtin_ctx(inject_scope="session")
    factory = _TempDirFactoryFixture().create(ctx=ctx)

    a = factory.mktemp("a")
    b = factory.mktemp("b")

    assert a.path.is_dir(), (
        f"factory.mktemp('a') should create a directory, got path={a.path!r}"
    )
    assert b.path.is_dir(), (
        f"factory.mktemp('b') should create a directory, got path={b.path!r}"
    )
    assert a.path != b.path, (
        f"factory.mktemp with different names should create distinct directories, "
        f"got a={a.path!r}, b={b.path!r}"
    )


def test_tempdir_factory_teardown_removes_all_dirs() -> None:
    """TempDirFactory teardown removes every directory created by mktemp()."""
    ctx, teardowns = _make_builtin_ctx(inject_scope="session")
    factory = _TempDirFactoryFixture().create(ctx=ctx)

    a = factory.mktemp("x")
    b = factory.mktemp("y")

    teardowns[0]()  # factory cleanup
    assert not a.path.exists(), (
        f"TempDirFactory teardown should remove all created directories; {a.path!r} "
        "still exists"
    )
    assert not b.path.exists(), (
        f"TempDirFactory teardown should remove all created directories; {b.path!r} "
        "still exists"
    )


def test_tempdir_factory_scope_is_session() -> None:
    """TempDirFactory is session-scoped; shared across all tests in a session."""
    assert _TempDirFactoryFixture.scope == "session", (
        f"_TempDirFactoryFixture should be session-scoped, got "
        f"scope={_TempDirFactoryFixture.scope!r}"
    )


# ── StdCapture ────────────────────────────────────────────────────────────────


def test_stdcapture_captures_print() -> None:
    """StdCapture intercepts print() output and exposes it via readouterr().out."""
    ctx, teardowns = _make_builtin_ctx()
    cap = _StdCaptureFixture().create(ctx=ctx)

    print("hello")  # noqa: T201
    result = cap.readouterr()
    teardowns[0]()  # restore stdout/stderr

    assert result.out == "hello\n", (
        f"StdCapture should capture print output as 'hello\\n', got {result.out!r}"
    )
    assert result.err == "", (
        f"StdCapture should have empty stderr when nothing was written to it, got "
        f"{result.err!r}"
    )


def test_stdcapture_readouterr_resets_buffer() -> None:
    """readouterr() flushes the buffer so successive reads don't accumulate."""
    ctx, teardowns = _make_builtin_ctx()
    cap = _StdCaptureFixture().create(ctx=ctx)

    print("first")  # noqa: T201
    cap.readouterr()
    print("second")  # noqa: T201
    result = cap.readouterr()
    teardowns[0]()

    assert result.out == "second\n", (
        f"readouterr() should reset the buffer; second read should only contain "
        f"'second\\n', got {result.out!r}"
    )


def test_stdcapture_disabled_passes_through() -> None:
    """StdCapture.disabled() restores sys.stdout to the pre-capture stream."""
    ctx, teardowns = _make_builtin_ctx()
    real_stdout = sys.stdout
    cap = _StdCaptureFixture().create(ctx=ctx)

    # Before disabled(): sys.stdout was replaced with the capture buffer.
    assert sys.stdout is not real_stdout, (
        "StdCapture.__init__ failed to install the capture buffer — subsequent "
        "print() output in tests would escape capture and pollute the runner's output"
    )

    with cap.disabled():
        # Inside disabled(): sys.stdout is restored to the pre-capture stream.
        assert sys.stdout is real_stdout, (
            "disabled() didn't suspend the capture — bytes written in this context "
            "would leak into the capture buffer instead of passing through, breaking "
            "the documented passthrough contract used by _runners.py"
        )

    # After disabled(): sys.stdout is the capture buffer again.
    assert sys.stdout is not real_stdout, (
        "disabled() context exit failed to re-install the capture buffer — "
        "later writes in the test would escape capture"
    )

    teardowns[0]()
    # No bytes were written inside disabled(); the capture stays empty.
    result = cap.readouterr()
    assert result.out == "", (
        "cross-test leak guard: nothing was written in this test, so the buffer must "
        "be empty; bytes here indicate state leaked from a prior test — the regression "
        "this PR fixes"
    )


def test_stdcapture_teardown_restores_streams() -> None:
    """StdCapture teardown restores sys.stdout to the original stream object."""
    real_stdout = sys.stdout
    ctx, teardowns = _make_builtin_ctx()
    _StdCaptureFixture().create(ctx=ctx)

    assert sys.stdout is not real_stdout, (
        "StdCapture should replace sys.stdout with a capture buffer while active"
    )
    teardowns[0]()
    assert sys.stdout is real_stdout, (
        "StdCapture teardown should restore sys.stdout to the original stream"
    )


# ── FdCapture ─────────────────────────────────────────────────────────────────


def test_fdcapture_captures_fd_write() -> None:
    """FdCapture intercepts raw fd writes and exposes them via readouterr().out."""
    ctx, teardowns = _make_builtin_ctx()
    cap = _FdCaptureFixture().create(ctx=ctx)

    os.write(1, b"raw\n")
    result = cap.readouterr()
    teardowns[0]()

    assert result.out == "raw\n", (
        f"FdCapture should capture raw fd write as 'raw\\n', got {result.out!r}"
    )


def test_fdcapture_readouterr_resets_buffer() -> None:
    """FdCapture readouterr() flushes the buffer; successive reads don't accumulate."""
    ctx, teardowns = _make_builtin_ctx()
    cap = _FdCaptureFixture().create(ctx=ctx)

    os.write(1, b"first\n")
    cap.readouterr()  # consume first write
    os.write(1, b"second\n")
    result = cap.readouterr()
    teardowns[0]()

    assert result.out == "second\n", (
        f"FdCapture readouterr() should reset buffer; second read should only contain "
        f"'second\\n', got {result.out!r}"
    )


def test_fdcapture_disabled_passes_through() -> None:
    """FdCapture.disabled() restores fd 1 to the pre-capture destination."""
    ctx, teardowns = _make_builtin_ctx()
    cap = _FdCaptureFixture().create(ctx=ctx)

    # Before disabled(): fd 1 points at the capture tmp, not the up-tree fd.
    assert not os.path.sameopenfile(1, cap._old_stdout_fd), (  # noqa: SLF001
        "FdCapture.__init__ failed to dup2 the tmp onto fd 1 — subsequent fd-level "
        "writes (C extensions, subprocesses) would escape capture"
    )

    with cap.disabled():
        # Inside disabled(): fd 1 is restored to _old_stdout_fd (the up-tree fd).
        assert os.path.sameopenfile(1, cap._old_stdout_fd), (  # noqa: SLF001
            "disabled() didn't suspend fd redirection — fd-level writes in this "
            "context would leak into the capture tmp instead of passing through, "
            "breaking the documented passthrough contract used by _runners.py"
        )

    # After disabled(): fd 1 points at the capture tmp again.
    assert not os.path.sameopenfile(1, cap._old_stdout_fd), (  # noqa: SLF001
        "disabled() context exit failed to re-direct fd 1 to the capture tmp — "
        "later fd writes in the test would escape capture"
    )

    result = cap.readouterr()
    teardowns[0]()

    # No bytes were written inside disabled(); the capture stays empty.
    assert result.out == "", (
        "cross-test leak guard: nothing was written in this test, so the buffer must "
        "be empty; bytes here indicate state leaked from a prior test — the regression "
        "this PR fixes"
    )


def test_fdcapture_teardown_restores_fds() -> None:
    """FdCapture teardown restores file descriptor 1 to the original underlying fd."""
    saved_fd = os.dup(1)  # save a reference to the current real stdout fd
    ctx, teardowns = _make_builtin_ctx()
    _FdCaptureFixture().create(ctx=ctx)

    teardowns[0]()
    try:
        assert os.path.sameopenfile(1, saved_fd), (
            "FdCapture teardown should restore fd 1 to the original file descriptor"
        )
    finally:
        os.close(saved_fd)


# ── Patcher ───────────────────────────────────────────────────────────────────


def test_patcher_setattr_overrides_attribute() -> None:
    """patch.setattr() immediately changes the target attribute to the new value."""
    ctx, _ = _make_builtin_ctx()
    patch = _PatcherFixture().create(ctx=ctx)

    obj = types.SimpleNamespace(x=1)
    patch.setattr(obj, "x", 99)
    assert obj.x == 99, (
        f"patch.setattr(obj, 'x', 99) should set obj.x to 99, got {obj.x!r}"
    )


def test_patcher_setattr_restores_on_teardown() -> None:
    """patch.setattr() teardown reverts the attribute to its original value."""
    ctx, teardowns = _make_builtin_ctx()
    patch = _PatcherFixture().create(ctx=ctx)

    obj = types.SimpleNamespace(x=1)
    patch.setattr(obj, "x", 99)
    teardowns[0]()
    assert obj.x == 1, (
        f"patch.setattr teardown should restore obj.x to original value 1, got "
        f"{obj.x!r}"
    )


def test_patcher_setenv_sets_and_restores() -> None:
    """patch.setenv() sets an env var during the test and removes it on teardown."""
    key = "_OXITEST_PATCHER_TEST"
    os.environ.pop(key, None)

    ctx, teardowns = _make_builtin_ctx()
    patch = _PatcherFixture().create(ctx=ctx)

    patch.setenv(key, "hello")
    assert os.environ[key] == "hello", (
        f"patch.setenv should set env var '{key}' to 'hello', got "
        f"{os.environ.get(key)!r}"
    )

    teardowns[0]()
    assert key not in os.environ, (
        f"patch.setenv teardown should remove env var '{key}' (it didn't exist before)"
    )


def test_patcher_delenv_removes_and_restores() -> None:
    """patch.delenv() removes an env var during the test and restores it on teardown."""
    key = "_OXITEST_PATCHER_DEL_TEST"
    os.environ[key] = "original"

    ctx, teardowns = _make_builtin_ctx()
    patch = _PatcherFixture().create(ctx=ctx)

    patch.delenv(key)
    assert key not in os.environ, (
        f"patch.delenv should remove env var '{key}' from os.environ"
    )

    teardowns[0]()
    assert os.environ[key] == "original", (
        f"patch.delenv teardown should restore env var '{key}' to 'original', got "
        f"{os.environ.get(key)!r}"
    )
    del os.environ[key]


def test_patcher_chdir_changes_and_restores(tmp: TempDir) -> None:
    """patch.chdir() changes the working directory and restores the original."""
    original = Path.cwd()

    ctx, teardowns = _make_builtin_ctx()
    patch = _PatcherFixture().create(ctx=ctx)

    patch.chdir(tmp)
    assert Path.cwd() == Path(tmp), (
        f"patch.chdir(tmp) should change cwd to {str(tmp)!r}, got {str(Path.cwd())!r}"
    )

    teardowns[0]()
    restored = Path.cwd()
    assert restored == original, (
        f"patch.chdir teardown should restore cwd to {str(original)!r},"
        f" got {str(restored)!r}"
    )


def test_patcher_teardown_undoes_in_lifo_order() -> None:
    """Patcher teardown undoes all patches in LIFO order to avoid ordering conflicts."""
    ctx, teardowns = _make_builtin_ctx()
    patch = _PatcherFixture().create(ctx=ctx)

    obj = types.SimpleNamespace(a="orig_a", b="orig_b")
    patch.setattr(obj, "a", "new_a")
    patch.setattr(obj, "b", "new_b")

    teardowns[0]()
    assert obj.a == "orig_a", (
        f"Patcher teardown (LIFO) should restore obj.a to 'orig_a', got {obj.a!r}"
    )
    assert obj.b == "orig_b", (
        f"Patcher teardown (LIFO) should restore obj.b to 'orig_b', got {obj.b!r}"
    )


# ── Type aliases ──────────────────────────────────────────────────────────────


def test_builtin_types_are_injectable() -> None:
    """All public builtin types carry __oxitest_injectable__ = True for dispatch."""
    for name in (
        "TempDir",
        "TempDirFactory",
        "StdCapture",
        "FdCapture",
        "Patcher",
        "LogCapture",
        "TestContext",
        "WarnCapture",
    ):
        cls = getattr(builtins_pkg, name)
        assert isinstance(cls, type), (
            f"{name} should be a class (not an Annotated alias) "
            f"after @injectable migration"
        )
        assert getattr(cls, "__oxitest_injectable__", False) is True, (
            f"{name} should have __oxitest_injectable__ = True"
        )


def test_testcontext_registered_as_builtin() -> None:
    """TestContext is registered as a BuiltinFixture and injected like TempDir."""
    assert BuiltinFixture.for_type(TestContext) is not None, (
        "TestContext should be registered as a BuiltinFixture (registration triggered "
        "by importing _builtins)"
    )


# ── FixtureSession integration ────────────────────────────────────────────────


def test_tempdir_injected_via_session() -> None:
    """FixtureSession resolves TempDir via builtin dispatch and tears it down."""
    reg = FixtureRegistry()
    session = FixtureSession(reg)

    def fn(tmp: TempDir) -> None:
        pass

    kwargs, teardowns = session.resolve_for_test(fn, helpers.make_meta("t.py"))
    tmp = kwargs["tmp"]
    assert tmp.path.is_dir(), (
        f"TempDir injected via session should be an existing directory, got "
        f"path={tmp.path!r}"
    )

    for td in reversed(teardowns):
        td()
    assert not tmp.path.exists(), (
        f"TempDir teardown via session should remove the directory, but {tmp.path!r} "
        "still exists"
    )


def test_tempdir_factory_session_scoped() -> None:
    """TempDirFactory returns the same instance across resolve_for_test calls."""
    reg = FixtureRegistry()
    session = FixtureSession(reg)

    def fn(factory: TempDirFactory) -> None:
        pass

    k1, _ = session.resolve_for_test(fn, helpers.make_meta("t.py"))
    k2, _ = session.resolve_for_test(fn, helpers.make_meta("t.py"))
    assert k1["factory"] is k2["factory"], (
        "TempDirFactory is session-scoped and should return the same instance across "
        "resolves"
    )
    session.end_task()  # trigger factory cleanup to avoid /tmp leakage
    session.end_process()


def test_stdcapture_injected_via_session() -> None:
    """FixtureSession resolves StdCapture via builtin dispatch and captures print."""
    reg = FixtureRegistry()
    session = FixtureSession(reg)

    def fn(cap: StdCapture) -> None:
        pass

    kwargs, teardowns = session.resolve_for_test(fn, helpers.make_meta("t.py"))
    cap = kwargs["cap"]
    print("captured")  # noqa: T201
    result = cap.readouterr()
    for td in reversed(teardowns):
        td()
    assert result.out == "captured\n", (
        f"StdCapture injected via session should capture print output 'captured\\n', "
        f"got {result.out!r}"
    )


def test_patcher_injected_via_session() -> None:
    """FixtureSession resolves Patcher via builtin dispatch and reverts patches."""
    reg = FixtureRegistry()
    session = FixtureSession(reg)

    def fn(patch: Patcher) -> None:
        pass

    kwargs, teardowns = session.resolve_for_test(fn, helpers.make_meta("t.py"))
    patch = kwargs["patch"]

    obj = types.SimpleNamespace(x=1)
    patch.setattr(obj, "x", 42)
    assert obj.x == 42, (
        f"Patcher injected via session should allow setattr to change obj.x to 42, got "
        f"{obj.x!r}"
    )

    for td in reversed(teardowns):
        td()
    assert obj.x == 1, (
        f"Patcher teardown via session should restore obj.x to 1, got {obj.x!r}"
    )


def test_testcontext_still_works_via_builtin_dispatch() -> None:
    """A fixture that depends on Fixture[TestContext] resolves via builtin dispatch."""

    def factory(_ctx: Fixture[TestContext]) -> str:
        return "ok"

    reg = FixtureRegistry()
    reg.register(helpers.make_fixture_def("thing", factory, conftest_path="/c.py"))
    session = FixtureSession(reg)

    def fn(thing: Fixture[str]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(fn, helpers.make_meta("t.py"))
    assert kwargs["thing"] == "ok", (
        f"fixture depending on Fixture[TestContext] via builtin dispatch should return "
        f"'ok', got {kwargs['thing']!r}"
    )


# ── LogCapture ───────────────────────────────────────────────────────────────


def test_logcapture_records_aggregates_backends() -> None:
    """LogCapture.records aggregates LogRecords from all installed backends."""
    cap = LogCapture([StdlibLogBackend(level=logging.DEBUG)])
    logging.getLogger().debug("agg test")
    recs = cap.records
    cap.close()

    assert any("agg test" in r.getMessage() for r in recs), (
        f"LogCapture.records should aggregate records from all backends; 'agg test' "
        f"not found in {[r.getMessage() for r in recs]}"
    )


def test_logcapture_text_formats_records() -> None:
    """LogCapture.text formats captured records as a human-readable string."""
    cap = LogCapture([StdlibLogBackend(level=logging.DEBUG)])
    logging.getLogger().warning("formatted")
    text = cap.text
    cap.close()

    assert "WARNING" in text, (
        f"LogCapture.text should include 'WARNING' level label, got {text!r}"
    )
    assert "formatted" in text, (
        f"LogCapture.text should include the log message 'formatted', got {text!r}"
    )


def test_logcapture_set_level_changes_threshold() -> None:
    """LogCapture.set_level() changes the filter threshold mid-test for all backends."""
    cap = LogCapture([StdlibLogBackend(level=logging.WARNING)])
    logging.getLogger().debug("filtered")
    cap.set_level(logging.DEBUG)
    logging.getLogger().debug("captured")
    recs = cap.records
    cap.close()

    messages = [r.getMessage() for r in recs]
    assert "filtered" not in messages, (
        f"debug record logged before set_level(DEBUG) should be filtered, got "
        f"messages={messages}"
    )
    assert "captured" in messages, (
        f"debug record logged after set_level(DEBUG) should be captured, got "
        f"messages={messages}"
    )


def test_logcapture_at_level_captures_and_restores() -> None:
    """at_level() temporarily lowers the capture threshold and restores it on exit."""
    cap = LogCapture([StdlibLogBackend(level=logging.WARNING)])
    root = logging.getLogger()
    with cap.at_level(logging.DEBUG):
        logging.getLogger().debug("inside block")
    level_after = root.level
    recs = cap.records
    cap.close()

    assert any("inside block" in r.getMessage() for r in recs), (
        f"at_level(DEBUG) context manager should capture debug records, "
        f"'inside block' not found in {[r.getMessage() for r in recs]}"
    )
    assert level_after == logging.WARNING, (
        f"at_level() context manager should restore level to WARNING after exiting, "
        f"got {level_after}"
    )


def test_logcapture_teardown_uninstalls_backends() -> None:
    """LogCapture.close() uninstalls all backends and removes their log handlers."""
    root = logging.getLogger()
    handler_count = len(root.handlers)
    cap = LogCapture([StdlibLogBackend(level=logging.DEBUG)])
    assert len(root.handlers) == handler_count + 1, (
        f"LogCapture should add 1 handler on creation, got {len(root.handlers)} (was "
        f"{handler_count})"
    )
    cap.close()
    assert len(root.handlers) == handler_count, (
        f"LogCapture.close() should remove the handler, restoring count to "
        f"{handler_count}, got {len(root.handlers)}"
    )


def test_logcapture_close_restores_root_logger_level() -> None:
    """LogCapture.close() restores the root logger level to its pre-capture value."""
    root = logging.getLogger()
    original_level = root.level
    cap = LogCapture([StdlibLogBackend(level=logging.DEBUG)])
    cap.close()
    assert root.level == original_level, (
        f"LogCapture.close() should restore root logger level to {original_level}, "
        f"got {root.level}"
    )


def test_logcapture_fixture_registers_teardown() -> None:
    """_LogCaptureFixture.create() registers one teardown that closes all backends."""
    ctx, teardowns = _make_builtin_ctx()
    _LogCaptureFixture().create(ctx=ctx)

    assert len(teardowns) == 1, (
        f"LogCaptureFixture should register exactly 1 teardown, got {len(teardowns)}"
    )
    root = logging.getLogger()
    handler_count = len(root.handlers)
    teardowns[0]()
    assert len(root.handlers) < handler_count + 1, (
        f"LogCaptureFixture teardown should remove the log handler; "
        f"handler count should be < {handler_count + 1}, got {len(root.handlers)}"
    )


def test_logcapture_injected_via_session() -> None:
    """FixtureSession resolves LogCapture by builtin dispatch into the kwargs dict."""
    reg = FixtureRegistry()
    session = FixtureSession(reg)

    def fn(log: LogCapture) -> None:
        pass

    kwargs, teardowns = session.resolve_for_test(fn, helpers.make_meta("t.py"))
    assert "log" in kwargs, (
        f"LogCapture should be injected as 'log' into kwargs via session, got keys: "
        f"{list(kwargs)}"
    )
    for td in reversed(teardowns):
        td()


@oxitest.mark.inprocess
def test_logcapture_includes_plugin_backends(ctx: TestContext) -> None:
    """Plugin-provided log backends are installed alongside StdlibLogBackend."""

    class FakePluginBackend:
        def __init__(self) -> None:
            self.installed = False
            self._records: list[logging.LogRecord] = []

        def install(self) -> None:
            self.installed = True

        def uninstall(self) -> None:
            self.installed = False

        @property
        def records(self) -> list[logging.LogRecord]:
            return list(self._records)

    fake_backend = FakePluginBackend()

    mod = helpers.make_plugin_module(
        "fake_log_plugin",
        lambda **_: Plugin(log_backends=(fake_backend,)),
    )
    helpers.install_module(ctx, "fake_log_plugin", mod)

    registry = load_plugins(["fake_log_plugin"], {})
    backends = [StdlibLogBackend(), *registry.log_backends]
    cap = LogCapture(backends)

    assert fake_backend.installed, (
        "Plugin log backend should be installed when LogCapture is created"
    )
    assert len(cap.backends) == 2, (
        f"Expected 2 backends (stdlib + plugin), got {len(cap.backends)}"
    )

    cap.close()
    assert not fake_backend.installed, (
        "Plugin log backend should be uninstalled after teardown"
    )


def test_stdlib_log_backend_starts_uninstalled() -> None:
    """Fresh StdlibLogBackend is _Uninstalled — no handler attached until install()."""
    backend = StdlibLogBackend()
    assert isinstance(backend._state, _Uninstalled), (  # noqa: SLF001
        "Fresh StdlibLogBackend must be _Uninstalled"
        " — no handler attached to root logger yet"
    )


def test_stdlib_log_backend_install_idempotent() -> None:
    """Calling install() twice must be a no-op: state object identity is preserved."""
    backend = StdlibLogBackend()
    backend.install()
    first_state = backend._state  # noqa: SLF001
    backend.install()  # second install must be a no-op
    assert backend._state is first_state, (  # noqa: SLF001
        "Idempotent install must not replace the state object"
        " — otherwise old_level is lost"
    )
    assert isinstance(backend._state, _Installed), (  # noqa: SLF001
        "State must be _Installed after install(), holding the captured old_level"
    )
    backend.uninstall()
