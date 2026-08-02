"""Tested examples for the use-builtin-fixtures how-to guide."""

import logging
import os
import types
import warnings

import oxitest
from oxitest import (
    FdCapture,
    LogCapture,
    Patcher,
    StdCapture,
    TempDir,
    TempDirFactory,
    TestContext,
    WarnCapture,
)

# Stubs for doc examples
mymodule = types.SimpleNamespace(TIMEOUT=30)


def acquire_expensive_resource():
    """Stub — returns an object with is_ready() and release()."""
    return types.SimpleNamespace(is_ready=lambda: True, release=lambda: None)


def phase_one():
    """Stub — emits a warning."""
    warnings.warn("phase one done", UserWarning, stacklevel=1)


def phase_two():
    """Stub — emits a warning."""
    warnings.warn("phase two done", UserWarning, stacklevel=1)


def run_job():
    """Stub — logs and returns a result."""
    logging.getLogger().info("started")
    return "ok"


# fmt: off
# --8<-- [start:tempdir]
def test_writes_file(tmp: TempDir) -> None:
    path = tmp.path / "output.txt"
    path.write_text("hello")
    assert path.read_text() == "hello", "TempDir should provide a writable directory"
# --8<-- [end:tempdir]

# --8<-- [start:tempdirfactory]
def test_shared_workspace(factory: TempDirFactory) -> None:
    workspace = factory.mktemp("workspace")
    (workspace.path / "data.csv").write_text("a,b\n1,2")
    assert (workspace.path / "data.csv").exists(), "mktemp should create a usable directory"
# --8<-- [end:tempdirfactory]

# --8<-- [start:stdcapture]
def test_prints_greeting(cap: StdCapture) -> None:
    print("Hello, world!")
    result = cap.readouterr()
    assert result.out == "Hello, world!\n", "StdCapture should capture stdout"
    assert result.err == "", "stderr should be empty"
# --8<-- [end:stdcapture]

# --8<-- [start:stdcapture-disabled]
def test_with_passthrough(cap: StdCapture) -> None:
    with cap.disabled():
        print("this appears in the terminal")
    print("this is captured")
    assert cap.readouterr().out == "this is captured\n", "only post-disabled output captured"
# --8<-- [end:stdcapture-disabled]

# --8<-- [start:fdcapture]
def test_c_extension_output(cap: FdCapture) -> None:
    os.write(1, b"raw output\n")
    result = cap.readouterr()
    assert "raw output\n" in result.out, "FdCapture should capture fd-level writes"
# --8<-- [end:fdcapture]

# --8<-- [start:patcher-env]
def test_env_override(patch: Patcher) -> None:
    patch.setenv("API_KEY", "test-key-123")
    assert os.environ["API_KEY"] == "test-key-123", "setenv should set the variable"
# --8<-- [end:patcher-env]

# --8<-- [start:patcher-delenv]
def test_env_removal(patch: Patcher) -> None:
    patch.setenv("TEMP_VAR", "value")
    patch.delenv("TEMP_VAR")
    assert "TEMP_VAR" not in os.environ, "delenv should remove the variable"
# --8<-- [end:patcher-delenv]

# --8<-- [start:patcher-setattr]
def test_attribute_override(patch: Patcher) -> None:
    patch.setattr(mymodule, "TIMEOUT", 0.1)
    assert mymodule.TIMEOUT == 0.1, "setattr should override the attribute"
# --8<-- [end:patcher-setattr]

# --8<-- [start:patcher-chdir]
def test_working_directory(patch: Patcher, tmp: TempDir) -> None:
    patch.chdir(tmp.path)
    assert os.getcwd() == str(tmp.path), "chdir should change the working directory"
# --8<-- [end:patcher-chdir]

# --8<-- [start:logcapture]
def test_logs_warning(log: LogCapture) -> None:
    log.set_level(logging.WARNING)
    logging.getLogger("myapp").warning("disk almost full")
    assert any("disk almost full" in r.getMessage() for r in log.records), "should capture warning"
# --8<-- [end:logcapture]

# --8<-- [start:logcapture-text]
def test_log_text(log: LogCapture) -> None:
    logging.warning("hello")
    assert "hello" in log.text, "log.text should contain the message"
# --8<-- [end:logcapture-text]

# --8<-- [start:logcapture-atlevel]
def test_debug_logs(log: LogCapture) -> None:
    with log.at_level(logging.DEBUG, logger="myapp"):
        logging.getLogger("myapp").debug("low-level detail")
    assert any("low-level detail" in r.getMessage() for r in log.records), "should capture debug"
# --8<-- [end:logcapture-atlevel]

# --8<-- [start:warncapture]
def test_emits_deprecation(warn: WarnCapture) -> None:
    warnings.warn("old_api is deprecated", DeprecationWarning, stacklevel=1)
    assert len(warn.warnings) == 1, "should capture exactly one warning"
    assert issubclass(warn.warnings[0].category, DeprecationWarning), "should be a DeprecationWarning"
# --8<-- [end:warncapture]

# --8<-- [start:warncapture-clear]
def test_two_phases(warn: WarnCapture) -> None:
    phase_one()
    warn.clear()
    phase_two()
    assert len(warn.warnings) == 1, "clear should reset, only phase_two warning remains"
# --8<-- [end:warncapture-clear]

# --8<-- [start:testcontext]
def test_metadata(ctx: TestContext):
    assert ctx.name == "test_metadata", "ctx.name should match the function name"
# --8<-- [end:testcontext]

# --8<-- [start:testcontext-finalizer]
def test_with_cleanup(ctx: TestContext):
    resource = acquire_expensive_resource()
    ctx.addfinalizer(resource.release)
    assert resource.is_ready(), "resource should be ready after acquisition"
# --8<-- [end:testcontext-finalizer]

# --8<-- [start:testcontext-parametrize]
@oxitest.parametrize(one={"n": 1}, two={"n": 2}, three={"n": 3})
def test_positive(n: int, ctx: TestContext):
    assert ctx.param_id in ("one", "two", "three"), "param_id should match case name"
    assert n in (1, 2, 3), "the case's values arrive as named parameters, not via ctx"
    assert ctx.param is None, (
        "ctx.param is None even here, on a parametrized test — the page must not "
        "promise a case value, and this is the assertion that makes the doc gate "
        "able to see it"
    )
# --8<-- [end:testcontext-parametrize]

# --8<-- [start:fx-oxi]
def test_combined(fx: oxitest.Fixtures) -> None:
    fx.oxi.patch.setenv("ENV", "test")
    fx.oxi.log.set_level(logging.DEBUG)
    result = run_job()
    assert "started" in fx.oxi.log.text, "log should capture the job start message"
    (fx.oxi.tmp.path / "result.txt").write_text(result)
# --8<-- [end:fx-oxi]
# fmt: on
