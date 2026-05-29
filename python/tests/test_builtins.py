"""Unit and integration tests for oxitest built-in fixtures."""

from __future__ import annotations

import sys

import oxitest
from conftest import helpers

# Imports needed so that get_type_hints() can resolve annotations in locally
# defined helper functions inside the FixtureSession integration tests.
from oxitest import Fixture, TempDir, raises  # noqa: F401
from oxitest._bridge._builtins import (  # noqa: F401
    FdCapture,
    LogCapture,
    Patcher,
    StdCapture,
    TempDirFactory,
    TestContext,  # noqa: F401
)
from oxitest._bridge._builtins._base import BuiltinFixture, _BuiltinContext
from oxitest._bridge._test_meta import TestMeta

# ── BuiltinFixture base ───────────────────────────────────────────────────────


def test_builtin_fixture_registration():
    class _Sentinel:
        pass

    class _SentinelFixture(BuiltinFixture, fixture_type=_Sentinel):
        def create(self, ctx: _BuiltinContext) -> str:
            return "sentinel"

    try:
        assert BuiltinFixture.for_type(_Sentinel) is _SentinelFixture, (
            "BuiltinFixture.for_type should return the registered subclass for "
            "_Sentinel"
        )
    finally:
        del BuiltinFixture._registry[_Sentinel]


def test_builtin_fixture_for_type_unknown_returns_none():
    assert BuiltinFixture.for_type(int) is None, (
        "BuiltinFixture.for_type(int) should return None for an unregistered type"
    )


def test_builtin_fixture_create_raises_not_implemented():
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=[],
    )
    with raises(NotImplementedError):
        BuiltinFixture().create(ctx)


def test_builtin_context_keep_tmp_default_is_none():
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=[],
    )
    assert ctx.keep_tmp is None, "_BuiltinContext.keep_tmp should default to None"


def test_builtin_context_keep_tmp_accepts_value():
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=[],
        keep_tmp="failed",
    )
    assert ctx.keep_tmp == "failed", (
        "_BuiltinContext.keep_tmp should accept and store 'failed'"
    )


# ── TempDir ───────────────────────────────────────────────────────────────────


def test_tempdir_fixture_creates_directory():
    from oxitest._bridge._builtins._tempdir import _TempDirFixture

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    tmp = _TempDirFixture().create(ctx)

    assert tmp.path.is_dir(), (
        f"_TempDirFixture.create() should produce a directory, got path={tmp.path!r} "
        "(not a dir)"
    )
    assert len(teardowns) == 1, (
        f"_TempDirFixture should register exactly 1 teardown, got {len(teardowns)}"
    )
    teardowns[0]()  # cleanup to avoid /tmp leakage


def test_tempdir_fixture_directory_name_includes_fn_name():
    from oxitest._bridge._builtins._tempdir import _TempDirFixture

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="my_test", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    tmp = _TempDirFixture().create(ctx)

    assert tmp.path.is_dir(), (
        f"_TempDirFixture.create() should produce a directory when fn_name is given, "
        f"got path={tmp.path!r}"
    )
    assert "my_test" in tmp.path.name, (
        f"TempDir path name should include the test function name 'my_test', got "
        f"{tmp.path.name!r}"
    )
    teardowns[0]()  # cleanup


def test_tempdir_fixture_teardown_removes_directory():
    from oxitest._bridge._builtins._tempdir import _TempDirFixture

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    tmp = _TempDirFixture().create(ctx)
    path = tmp.path

    teardowns[0]()
    assert not path.exists(), (
        f"TempDir teardown should remove the directory, but {path!r} still exists"
    )


def test_tempdir_keep_tmp_failed_preserves_on_failure():
    from oxitest._bridge._builtins._tempdir import _TempDirFixture
    from oxitest._bridge.result import StatusKind, TestResult

    result_cell: list[TestResult | None] = [None]
    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="fail_test", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
        keep_tmp="failed",
        result_cell=result_cell,
    )
    tmp = _TempDirFixture().create(ctx)
    path = tmp.path
    assert path.is_dir(), "TempDir should create a directory"

    # Simulate a failed test result
    result_cell[0] = TestResult(status=StatusKind.FAILED, message="assertion error")

    # Run teardown — should NOT remove the directory
    teardowns[0]()
    assert path.exists(), (
        "TempDir should be preserved when keep_tmp='failed' and the test failed"
    )
    # Manual cleanup
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def test_tempdir_keep_tmp_failed_cleans_on_pass():
    from oxitest._bridge._builtins._tempdir import _TempDirFixture
    from oxitest._bridge.result import TestResult

    result_cell: list[TestResult | None] = [None]
    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="pass_test", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
        keep_tmp="failed",
        result_cell=result_cell,
    )
    tmp = _TempDirFixture().create(ctx)
    path = tmp.path

    # Simulate a passed test result
    result_cell[0] = TestResult.passed()

    teardowns[0]()
    assert not path.exists(), (
        "TempDir should be cleaned up when keep_tmp='failed' and the test passed"
    )


def test_tempdir_keep_tmp_always_preserves_on_pass():
    from oxitest._bridge._builtins._tempdir import _TempDirFixture
    from oxitest._bridge.result import TestResult

    result_cell: list[TestResult | None] = [None]
    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="pass_test", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
        keep_tmp="always",
        result_cell=result_cell,
    )
    tmp = _TempDirFixture().create(ctx)
    path = tmp.path

    result_cell[0] = TestResult.passed()

    teardowns[0]()
    assert path.exists(), (
        "TempDir should be preserved when keep_tmp='always' even if the test passed"
    )
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def test_tempdir_keep_tmp_failed_preserves_on_error():
    from oxitest._bridge._builtins._tempdir import _TempDirFixture
    from oxitest._bridge.result import StatusKind, TestResult

    result_cell: list[TestResult | None] = [None]
    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="err_test", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
        keep_tmp="failed",
        result_cell=result_cell,
    )
    tmp = _TempDirFixture().create(ctx)
    path = tmp.path

    result_cell[0] = TestResult(status=StatusKind.ERROR, message="boom")

    teardowns[0]()
    assert path.exists(), (
        "TempDir should be preserved when keep_tmp='failed' and the test errored"
    )
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def test_tempdir_keep_tmp_prints_path_to_stderr():
    import io
    from contextlib import redirect_stderr

    from oxitest._bridge._builtins._tempdir import _TempDirFixture
    from oxitest._bridge.result import StatusKind, TestResult

    result_cell: list[TestResult | None] = [None]
    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="fail_test", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
        keep_tmp="failed",
        result_cell=result_cell,
    )
    tmp = _TempDirFixture().create(ctx)
    path = tmp.path

    result_cell[0] = TestResult(status=StatusKind.FAILED, message="oops")

    buf = io.StringIO()
    with redirect_stderr(buf):
        teardowns[0]()
    stderr_output = buf.getvalue()
    assert str(path) in stderr_output, (
        f"Preserved TempDir path should be printed to stderr, got: {stderr_output!r}"
    )
    assert "--keep-tmp" in stderr_output, (
        f"Stderr message should mention --keep-tmp, got: {stderr_output!r}"
    )
    import shutil

    shutil.rmtree(path, ignore_errors=True)


# ── TempDirFactory ────────────────────────────────────────────────────────────


def test_tempdir_factory_mktemp_creates_distinct_dirs():
    from oxitest._bridge._builtins._tempdir import _TempDirFactoryFixture

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="session",
        teardown_stack=teardowns,
    )
    factory = _TempDirFactoryFixture().create(ctx)

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


def test_tempdir_factory_teardown_removes_all_dirs():
    from oxitest._bridge._builtins._tempdir import _TempDirFactoryFixture

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="session",
        teardown_stack=teardowns,
    )
    factory = _TempDirFactoryFixture().create(ctx)

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


def test_tempdir_factory_scope_is_session():
    from oxitest._bridge._builtins._tempdir import _TempDirFactoryFixture

    assert _TempDirFactoryFixture.scope == "session", (
        f"_TempDirFactoryFixture should be session-scoped, got "
        f"scope={_TempDirFactoryFixture.scope!r}"
    )


# ── StdCapture ────────────────────────────────────────────────────────────────


def test_stdcapture_captures_print():
    from oxitest._bridge._builtins._capture import _StdCaptureFixture

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    cap = _StdCaptureFixture().create(ctx)

    print("hello")
    result = cap.readouterr()
    teardowns[0]()  # restore stdout/stderr

    assert result.out == "hello\n", (
        f"StdCapture should capture print output as 'hello\\n', got {result.out!r}"
    )
    assert result.err == "", (
        f"StdCapture should have empty stderr when nothing was written to it, got "
        f"{result.err!r}"
    )


def test_stdcapture_readouterr_resets_buffer():
    from oxitest._bridge._builtins._capture import _StdCaptureFixture

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    cap = _StdCaptureFixture().create(ctx)

    print("first")
    cap.readouterr()
    print("second")
    result = cap.readouterr()
    teardowns[0]()

    assert result.out == "second\n", (
        f"readouterr() should reset the buffer; second read should only contain "
        f"'second\\n', got {result.out!r}"
    )


def test_stdcapture_disabled_passes_through(cap_outer: StdCapture):

    from oxitest._bridge._builtins._capture import _StdCaptureFixture

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    cap = _StdCaptureFixture().create(ctx)

    with cap.disabled():
        # While disabled, output goes to the real stdout (captured by cap_outer)
        sys.stdout.write("passthrough\n")

    teardowns[0]()
    # The text written inside disabled() was NOT captured by our cap
    assert cap.readouterr().out == "", (
        "text written while cap.disabled() should not appear in cap.readouterr().out"
    )
    teardowns[0]()  # idempotent second restore is fine


def test_stdcapture_teardown_restores_streams():

    from oxitest._bridge._builtins._capture import _StdCaptureFixture

    real_stdout = sys.stdout
    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    _StdCaptureFixture().create(ctx)

    assert sys.stdout is not real_stdout, (
        "StdCapture should replace sys.stdout with a capture buffer while active"
    )
    teardowns[0]()
    assert sys.stdout is real_stdout, (
        "StdCapture teardown should restore sys.stdout to the original stream"
    )


# ── FdCapture ─────────────────────────────────────────────────────────────────


def test_fdcapture_captures_fd_write():
    import os

    from oxitest._bridge._builtins._capture import _FdCaptureFixture

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    cap = _FdCaptureFixture().create(ctx)

    os.write(1, b"raw\n")
    result = cap.readouterr()
    teardowns[0]()

    assert result.out == "raw\n", (
        f"FdCapture should capture raw fd write as 'raw\\n', got {result.out!r}"
    )


def test_fdcapture_readouterr_resets_buffer():
    import os

    from oxitest._bridge._builtins._capture import _FdCaptureFixture

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    cap = _FdCaptureFixture().create(ctx)

    os.write(1, b"first\n")
    cap.readouterr()  # consume first write
    os.write(1, b"second\n")
    result = cap.readouterr()
    teardowns[0]()

    assert result.out == "second\n", (
        f"FdCapture readouterr() should reset buffer; second read should only contain "
        f"'second\\n', got {result.out!r}"
    )


def test_fdcapture_disabled_passes_through():
    import os

    from oxitest._bridge._builtins._capture import _FdCaptureFixture

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    cap = _FdCaptureFixture().create(ctx)

    with cap.disabled():
        # While disabled, writes go to real fd 1 (not captured)
        os.write(1, b"passthrough\n")

    result = cap.readouterr()
    teardowns[0]()

    assert result.out == "", (
        f"FdCapture: bytes written while disabled() should not appear in "
        f"readouterr().out, got {result.out!r}"
    )


def test_fdcapture_teardown_restores_fds():
    import os

    from oxitest._bridge._builtins._capture import _FdCaptureFixture

    saved_fd = os.dup(1)  # save a reference to the current real stdout fd
    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    _FdCaptureFixture().create(ctx)

    teardowns[0]()
    try:
        assert os.path.sameopenfile(1, saved_fd), (
            "FdCapture teardown should restore fd 1 to the original file descriptor"
        )
    finally:
        os.close(saved_fd)


# ── Patcher ───────────────────────────────────────────────────────────────────


def test_patcher_setattr_overrides_attribute():
    import types

    from oxitest._bridge._builtins._patch import _PatcherFixture

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    patch = _PatcherFixture().create(ctx)

    obj = types.SimpleNamespace(x=1)
    patch.setattr(obj, "x", 99)
    assert obj.x == 99, (
        f"patch.setattr(obj, 'x', 99) should set obj.x to 99, got {obj.x!r}"
    )


def test_patcher_setattr_restores_on_teardown():
    import types

    from oxitest._bridge._builtins._patch import _PatcherFixture

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    patch = _PatcherFixture().create(ctx)

    obj = types.SimpleNamespace(x=1)
    patch.setattr(obj, "x", 99)
    teardowns[0]()
    assert obj.x == 1, (
        f"patch.setattr teardown should restore obj.x to original value 1, got "
        f"{obj.x!r}"
    )


def test_patcher_setenv_sets_and_restores():
    import os

    from oxitest._bridge._builtins._patch import _PatcherFixture

    key = "_OXITEST_PATCHER_TEST"
    os.environ.pop(key, None)

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    patch = _PatcherFixture().create(ctx)

    patch.setenv(key, "hello")
    assert os.environ[key] == "hello", (
        f"patch.setenv should set env var '{key}' to 'hello', got "
        f"{os.environ.get(key)!r}"
    )

    teardowns[0]()
    assert key not in os.environ, (
        f"patch.setenv teardown should remove env var '{key}' (it didn't exist before)"
    )


def test_patcher_delenv_removes_and_restores():
    import os

    from oxitest._bridge._builtins._patch import _PatcherFixture

    key = "_OXITEST_PATCHER_DEL_TEST"
    os.environ[key] = "original"

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    patch = _PatcherFixture().create(ctx)

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


def test_patcher_chdir_changes_and_restores(tmp: TempDir):
    import os

    from oxitest._bridge._builtins._patch import _PatcherFixture

    original = os.getcwd()

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    patch = _PatcherFixture().create(ctx)

    patch.chdir(tmp)
    assert os.getcwd() == str(tmp), (
        f"patch.chdir(tmp) should change cwd to {str(tmp)!r}, got {os.getcwd()!r}"
    )

    teardowns[0]()
    assert os.getcwd() == original, (
        f"patch.chdir teardown should restore cwd to {original!r}, got {os.getcwd()!r}"
    )


def test_patcher_teardown_undoes_in_lifo_order():
    import types

    from oxitest._bridge._builtins._patch import _PatcherFixture

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    patch = _PatcherFixture().create(ctx)

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


def test_type_aliases_are_annotated_with_fixture_marker():
    from typing import Annotated, get_args, get_origin

    import oxitest._bridge._builtins as builtins_pkg
    from oxitest._bridge._fixture_type import _FixtureMarker

    for name in (
        "TempDir",
        "TempDirFactory",
        "StdCapture",
        "FdCapture",
        "Patcher",
        "LogCapture",
    ):
        alias = getattr(builtins_pkg, name)
        assert get_origin(alias) is Annotated, f"{name} is not Annotated"
        args = get_args(alias)
        assert any(isinstance(m, _FixtureMarker) for m in args[1:]), (
            f"{name} missing _FixtureMarker in metadata"
        )


def test_testcontext_registered_as_builtin():
    import oxitest._bridge._builtins  # noqa: F401 — trigger registrations
    from oxitest._bridge._builtins._base import BuiltinFixture
    from oxitest._bridge.fixtures import _TestContext

    assert BuiltinFixture.for_type(_TestContext) is not None, (
        "_TestContext should be registered as a BuiltinFixture (registration triggered "
        "by importing _builtins)"
    )


# ── FixtureSession integration ────────────────────────────────────────────────


def _make_session():
    """Import oxitest (triggers _builtins registration) and return session classes."""
    import oxitest  # noqa: F401 — triggers _builtins registration
    from oxitest._bridge.fixtures import FixtureRegistry, FixtureSession

    return FixtureRegistry, FixtureSession


def test_tempdir_injected_via_session():
    FixtureRegistry, FixtureSession = _make_session()

    reg = FixtureRegistry()
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(tmp: TempDir) -> None:  # type: ignore[valid-type]
        pass

    kwargs, teardowns = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
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


def test_tempdir_factory_session_scoped():
    FixtureRegistry, FixtureSession = _make_session()

    reg = FixtureRegistry()
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(factory: TempDirFactory) -> None:  # type: ignore[valid-type]
        pass

    k1, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    k2, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert k1["factory"] is k2["factory"], (
        "TempDirFactory is session-scoped and should return the same instance across "
        "resolves"
    )
    session.end_session()  # trigger factory cleanup to avoid /tmp leakage


def test_stdcapture_injected_via_session():
    FixtureRegistry, FixtureSession = _make_session()

    reg = FixtureRegistry()
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(cap: StdCapture) -> None:  # type: ignore[valid-type]
        pass

    kwargs, teardowns = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    cap = kwargs["cap"]
    print("captured")
    result = cap.readouterr()
    for td in reversed(teardowns):
        td()
    assert result.out == "captured\n", (
        f"StdCapture injected via session should capture print output 'captured\\n', "
        f"got {result.out!r}"
    )


def test_patcher_injected_via_session():
    import types

    FixtureRegistry, FixtureSession = _make_session()

    reg = FixtureRegistry()
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(patch: Patcher) -> None:  # type: ignore[valid-type]
        pass

    kwargs, teardowns = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
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


def test_testcontext_still_works_via_builtin_dispatch():
    from oxitest._bridge.fixtures import FixtureDef, FixtureRegistry, FixtureSession

    def factory(ctx: Fixture[TestContext]) -> str:
        return "ok"

    reg = FixtureRegistry()
    reg.register(FixtureDef("thing", factory, False, None, "/c.py"))
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(thing: Fixture[str]) -> None:  # type: ignore[type-arg]
        pass

    kwargs, _ = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert kwargs["thing"] == "ok", (
        f"fixture depending on Fixture[TestContext] via builtin dispatch should return "
        f"'ok', got {kwargs['thing']!r}"
    )


# ── LogBackend / StdlibLogBackend ─────────────────────────────────────────────


def test_stdlib_backend_captures_records():
    import logging

    from oxitest._bridge._builtins._logcapture import StdlibLogBackend

    backend = StdlibLogBackend(level=logging.DEBUG)
    backend.install()
    logging.getLogger().debug("hello from stdlib")
    recs = backend.records
    backend.uninstall()

    assert len(recs) == 1, (
        f"StdlibLogBackend should capture 1 log record, got {len(recs)}"
    )
    assert recs[0].getMessage() == "hello from stdlib", (
        f"captured record message should be 'hello from stdlib', got "
        f"{recs[0].getMessage()!r}"
    )


def test_stdlib_backend_uninstall_removes_handler():
    import logging

    from oxitest._bridge._builtins._logcapture import StdlibLogBackend

    backend = StdlibLogBackend(level=logging.DEBUG)
    root = logging.getLogger()
    handler_count_before = len(root.handlers)
    backend.install()
    assert len(root.handlers) == handler_count_before + 1, (
        f"install() should add 1 handler to root logger, got {len(root.handlers)} (was "
        f"{handler_count_before})"
    )
    backend.uninstall()
    assert len(root.handlers) == handler_count_before, (
        f"uninstall() should remove the handler, restoring count to "
        f"{handler_count_before}, got {len(root.handlers)}"
    )


def test_stdlib_backend_uninstall_restores_level():
    import logging

    from oxitest._bridge._builtins._logcapture import StdlibLogBackend

    root = logging.getLogger()
    old_level = root.level
    backend = StdlibLogBackend(level=logging.DEBUG)
    backend.install()
    backend.uninstall()
    assert root.level == old_level, (
        f"uninstall() should restore root logger level to {old_level}, got {root.level}"
    )


def test_stdlib_backend_set_level_filters_records():
    import logging

    from oxitest._bridge._builtins._logcapture import StdlibLogBackend

    backend = StdlibLogBackend(level=logging.WARNING)
    backend.install()
    logging.getLogger().debug("should be filtered")
    backend.set_level(logging.DEBUG)
    logging.getLogger().debug("should be captured")
    recs = backend.records
    backend.uninstall()

    assert len(recs) == 1, (
        f"set_level(DEBUG) after WARNING should capture 1 record (not 2), got "
        f"{len(recs)}"
    )
    assert recs[0].getMessage() == "should be captured", (
        f"only the post-set_level record should be captured, got "
        f"{recs[0].getMessage()!r}"
    )


# ── _LogCapture ───────────────────────────────────────────────────────────────


def test_logcapture_records_aggregates_backends():
    import logging

    from oxitest._bridge._builtins._logcapture import StdlibLogBackend, _LogCapture

    cap = _LogCapture([StdlibLogBackend(level=logging.DEBUG)])
    logging.getLogger().debug("agg test")
    recs = cap.records
    cap._teardown()

    assert any("agg test" in r.getMessage() for r in recs), (
        f"_LogCapture.records should aggregate records from all backends; 'agg test' "
        f"not found in {[r.getMessage() for r in recs]}"
    )


def test_logcapture_text_formats_records():
    import logging

    from oxitest._bridge._builtins._logcapture import StdlibLogBackend, _LogCapture

    cap = _LogCapture([StdlibLogBackend(level=logging.DEBUG)])
    logging.getLogger().warning("formatted")
    text = cap.text
    cap._teardown()

    assert "WARNING" in text, (
        f"_LogCapture.text should include 'WARNING' level label, got {text!r}"
    )
    assert "formatted" in text, (
        f"_LogCapture.text should include the log message 'formatted', got {text!r}"
    )


def test_logcapture_set_level_changes_threshold():
    import logging

    from oxitest._bridge._builtins._logcapture import StdlibLogBackend, _LogCapture

    cap = _LogCapture([StdlibLogBackend(level=logging.WARNING)])
    logging.getLogger().debug("filtered")
    cap.set_level(logging.DEBUG)
    logging.getLogger().debug("captured")
    recs = cap.records
    cap._teardown()

    messages = [r.getMessage() for r in recs]
    assert "filtered" not in messages, (
        f"debug record logged before set_level(DEBUG) should be filtered, got "
        f"messages={messages}"
    )
    assert "captured" in messages, (
        f"debug record logged after set_level(DEBUG) should be captured, got "
        f"messages={messages}"
    )


def test_logcapture_at_level_captures_and_restores():
    import logging

    from oxitest._bridge._builtins._logcapture import StdlibLogBackend, _LogCapture

    cap = _LogCapture([StdlibLogBackend(level=logging.WARNING)])
    root = logging.getLogger()
    with cap.at_level(logging.DEBUG):
        logging.getLogger().debug("inside block")
    level_after = root.level
    recs = cap.records
    cap._teardown()

    assert any("inside block" in r.getMessage() for r in recs), (
        f"at_level(DEBUG) context manager should capture debug records, "
        f"'inside block' not found in {[r.getMessage() for r in recs]}"
    )
    assert level_after == logging.WARNING, (
        f"at_level() context manager should restore level to WARNING after exiting, "
        f"got {level_after}"
    )


def test_logcapture_teardown_uninstalls_backends():
    import logging

    from oxitest._bridge._builtins._logcapture import StdlibLogBackend, _LogCapture

    root = logging.getLogger()
    handler_count = len(root.handlers)
    cap = _LogCapture([StdlibLogBackend(level=logging.DEBUG)])
    assert len(root.handlers) == handler_count + 1, (
        f"_LogCapture should add 1 handler on creation, got {len(root.handlers)} (was "
        f"{handler_count})"
    )
    cap._teardown()
    assert len(root.handlers) == handler_count, (
        f"_LogCapture._teardown() should remove the handler, restoring count to "
        f"{handler_count}, got {len(root.handlers)}"
    )


def test_logcapture_fixture_registers_teardown():
    import logging

    from oxitest._bridge._builtins._base import _BuiltinContext
    from oxitest._bridge._builtins._logcapture import _LogCaptureFixture

    teardowns: list = []
    ctx = _BuiltinContext(
        meta=TestMeta(module_path="t.py", fn_name="", node_id=""),
        inject_scope="function",
        teardown_stack=teardowns,
    )
    _LogCaptureFixture().create(ctx)

    assert len(teardowns) == 1, (
        f"_LogCaptureFixture should register exactly 1 teardown, got {len(teardowns)}"
    )
    root = logging.getLogger()
    handler_count = len(root.handlers)
    teardowns[0]()
    assert len(root.handlers) < handler_count + 1, (
        f"_LogCaptureFixture teardown should remove the log handler; "
        f"handler count should be < {handler_count + 1}, got {len(root.handlers)}"
    )


def test_logcapture_injected_via_session():
    FixtureRegistry, FixtureSession = _make_session()

    reg = FixtureRegistry()
    session = FixtureSession(reg)
    session.begin_module("t.py")

    def fn(log: LogCapture) -> None:  # type: ignore[valid-type]
        pass

    kwargs, teardowns = session.resolve_for_test(fn, helpers.common.make_meta("t.py"))
    assert "log" in kwargs, (
        f"LogCapture should be injected as 'log' into kwargs via session, got keys: "
        f"{list(kwargs)}"
    )
    for td in reversed(teardowns):
        td()


@oxitest.mark.inprocess
def test_logcapture_includes_plugin_backends():
    """Plugin-provided log backends are installed alongside StdlibLogBackend."""
    import logging
    import types

    from oxitest._bridge._builtins._logcapture import StdlibLogBackend, _LogCapture
    from oxitest._bridge.plugin_loader import load_plugins
    from oxitest.plugin import Plugin

    class FakePluginBackend:
        def __init__(self):
            self.installed = False
            self._records: list[logging.LogRecord] = []

        def install(self):
            self.installed = True

        def uninstall(self):
            self.installed = False

        @property
        def records(self):
            return list(self._records)

    fake_backend = FakePluginBackend()

    mod = types.ModuleType("fake_log_plugin")
    mod.oxitest_plugin = lambda config=None: Plugin(log_backends=[fake_backend])  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    sys.modules["fake_log_plugin"] = mod
    try:
        registry = load_plugins(["fake_log_plugin"], {})
        backends = [StdlibLogBackend()] + list(registry.log_backends)
        cap = _LogCapture(backends)

        assert fake_backend.installed, (
            "Plugin log backend should be installed when LogCapture is created"
        )
        assert len(cap._backends) == 2, (
            f"Expected 2 backends (stdlib + plugin), got {len(cap._backends)}"
        )

        cap._teardown()
        assert not fake_backend.installed, (
            "Plugin log backend should be uninstalled after teardown"
        )
    finally:
        sys.modules.pop("fake_log_plugin", None)
