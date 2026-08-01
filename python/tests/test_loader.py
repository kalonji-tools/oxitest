"""Tests for _load_module and _resolve_fn in the module loader."""

from __future__ import annotations

import hashlib
import importlib.util

import oxitest
from oxitest import TempDir, TestContext, raises
from oxitest._bridge._loader import _load_module, _LoadError, _resolve_fn
from tests import helpers


def _unique_name(path: str) -> str:
    digest = hashlib.md5(path.encode(), usedforsecurity=False)
    return f"_oxitest_exec_{digest.hexdigest()[:12]}"


def test_load_module_returns_module_with_function(tmp: TempDir) -> None:
    """_load_module returns a module object containing the defined test function."""
    f = tmp / "test_sample.py"
    f.write_text("def test_foo(): pass\n")
    unique = _unique_name(str(f))
    module = _load_module(str(f), unique)
    assert hasattr(module, "test_foo"), (
        f"loaded module should have 'test_foo' attribute, got attrs: {dir(module)}"
    )
    assert callable(module.test_foo), (
        f"module.test_foo should be callable, got {type(module.test_foo).__name__}"
    )


@oxitest.arrange(TempDir)
def test_load_module_raises_load_error_on_bad_path() -> None:
    """_load_module should raise _LoadError when the file path does not exist."""
    unique = _unique_name("/nonexistent/path/test_x.py")
    with raises(_LoadError):
        _load_module("/nonexistent/path/test_x.py", unique)


def test_load_module_raises_load_error_on_syntax_error(tmp: TempDir) -> None:
    """_load_module should raise _LoadError when the file contains a syntax error."""
    f = tmp / "test_broken.py"
    f.write_text("def test_foo(: pass\n")
    unique = _unique_name(str(f))
    with raises(_LoadError):
        _load_module(str(f), unique)


@oxitest.arrange("_clean_sys_modules")
@oxitest.mark.inprocess
def test_resolve_fn_returns_callable(tmp: TempDir, ctx: TestContext) -> None:
    """_resolve_fn returns a (module, callable) tuple for a valid function name."""
    f = tmp / "mod.py"
    f.write_text("def test_bar(): return 42\n")
    spec = importlib.util.spec_from_file_location("_test_mod_tmp", f)
    assert spec is not None, "spec_from_file_location should return a spec"
    assert spec.loader is not None, "spec should have a loader"
    module = importlib.util.module_from_spec(spec)
    helpers.install_module(ctx, "_test_mod_tmp", module)
    spec.loader.exec_module(module)

    _, fn = _resolve_fn(module, "test_bar", str(f))
    assert callable(fn), (
        f"resolved function should be callable, got {type(fn).__name__}"
    )
    assert fn() == 42, f"resolved function should return 42, got {fn()!r}"


@oxitest.arrange("_clean_sys_modules")
@oxitest.mark.inprocess
def test_resolve_fn_raises_load_error_on_missing_function(
    tmp: TempDir, ctx: TestContext
) -> None:
    """_resolve_fn raises _LoadError with status='error' when the function is absent."""
    f = tmp / "mod.py"
    f.write_text("def test_bar(): pass\n")
    spec = importlib.util.spec_from_file_location("_test_mod_tmp2", f)
    assert spec is not None, "spec_from_file_location should return a spec"
    assert spec.loader is not None, "spec should have a loader"
    module = importlib.util.module_from_spec(spec)
    helpers.install_module(ctx, "_test_mod_tmp2", module)
    spec.loader.exec_module(module)

    with raises(_LoadError) as exc_info:
        _resolve_fn(module, "test_nonexistent", str(f))
    load_error = exc_info.value
    assert isinstance(load_error, _LoadError), (
        "raises(_LoadError) should capture a _LoadError instance"
    )
    assert load_error.result.status == "error", (
        f"_LoadError for missing function should have result.status='error', "
        f"got {load_error.result.status!r}"
    )


@oxitest.arrange("_clean_sys_modules")
@oxitest.mark.inprocess
def test_resolve_fn_handles_class_method(tmp: TempDir, ctx: TestContext) -> None:
    """_resolve_fn should resolve 'ClassName::method_name' to the unbound method."""
    f = tmp / "mod.py"
    f.write_text("class TestFoo:\n    def test_method(self): return 'ok'\n")
    spec = importlib.util.spec_from_file_location("_test_mod_tmp3", f)
    assert spec is not None, "spec_from_file_location should return a spec"
    assert spec.loader is not None, "spec should have a loader"
    module = importlib.util.module_from_spec(spec)
    helpers.install_module(ctx, "_test_mod_tmp3", module)
    spec.loader.exec_module(module)

    _, fn = _resolve_fn(module, "TestFoo::test_method", str(f))
    assert callable(fn), (
        f"resolved class method should be callable, got {type(fn).__name__}"
    )
    assert fn() == "ok", f"resolved class method should return 'ok', got {fn()!r}"
