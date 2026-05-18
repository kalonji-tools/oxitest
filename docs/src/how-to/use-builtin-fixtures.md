# Use built-in fixtures

!!! abstract "How-to"
    Use oxitest's built-in fixtures for temporary files, output capture, patching, and log capture.

All built-in fixtures are injected by annotating a parameter with the bare type
(no `Fixture[T]` wrapper needed). Import the type from `oxitest`.

## TempDir — temporary directories

`TempDir` provides a unique temporary directory that is deleted after the test.

```python
from oxitest import TempDir

def test_writes_file(tmp: TempDir) -> None:
    path = tmp.path / "output.txt"
    path.write_text("hello")
    assert path.read_text() == "hello"
```

`tmp.path` is a `pathlib.Path`. The directory is removed after the test regardless
of pass or fail.

## TempDirFactory — session-scoped temp dirs

`TempDirFactory` is a session-scoped factory. Use it when you need multiple named
temp directories or want to share a directory across tests:

```python
from oxitest import TempDirFactory

def test_shared_workspace(factory: TempDirFactory) -> None:
    workspace = factory.mktemp("workspace")
    (workspace.path / "data.csv").write_text("a,b\n1,2")
    assert (workspace.path / "data.csv").exists()
```

`factory.mktemp("label")` returns a `TempDir` with a unique subdirectory.

## StdCapture — stdout/stderr at stream level

`StdCapture` captures `sys.stdout` and `sys.stderr` at the Python stream level:

```python
from oxitest import StdCapture

def test_prints_greeting(cap: StdCapture) -> None:
    print("Hello, world!")
    result = cap.readouterr()
    assert result.out == "Hello, world!\n"
    assert result.err == ""
```

`readouterr()` returns a `CaptureResult(out, err)` and resets the buffers.

Use `cap.disabled()` to temporarily let output pass through:

```python
def test_with_passthrough(cap: StdCapture) -> None:
    with cap.disabled():
        print("this appears in the terminal")
    print("this is captured")
    assert cap.readouterr().out == "this is captured\n"
```

## FdCapture — stdout/stderr at fd level

`FdCapture` captures at file descriptor level (fd 1 and fd 2). Use this when the
code under test writes directly to the OS file descriptors (e.g. C extensions,
`os.write`):

```python
import os
from oxitest import FdCapture

def test_c_extension_output(cap: FdCapture) -> None:
    os.write(1, b"raw output\n")
    result = cap.readouterr()
    assert result.out == "raw output\n"
```

The API is identical to `StdCapture`.

## Patcher — attributes, env vars, and directories

`Patcher` provides four patching helpers that are automatically restored after
the test:

```python
from oxitest import Patcher

def test_env_override(patch: Patcher) -> None:
    patch.setenv("API_KEY", "test-key-123")
    import os
    assert os.environ["API_KEY"] == "test-key-123"

def test_env_removal(patch: Patcher) -> None:
    import os
    patch.setenv("TEMP_VAR", "value")
    patch.delenv("TEMP_VAR")
    assert "TEMP_VAR" not in os.environ

def test_attribute_override(patch: Patcher) -> None:
    import mymodule
    patch.setattr(mymodule, "TIMEOUT", 0.1)
    assert mymodule.TIMEOUT == 0.1

def test_working_directory(patch: Patcher, tmp: TempDir) -> None:
    patch.chdir(tmp.path)
    import os
    assert os.getcwd() == str(tmp.path)
```

All changes are reverted after the test, even if the test raises.

## LogCapture — logging records

`LogCapture` captures Python `logging` output:

```python
import logging
from oxitest import LogCapture

def test_logs_warning(log: LogCapture) -> None:
    log.set_level(logging.WARNING)
    logging.getLogger("myapp").warning("disk almost full")
    assert any("disk almost full" in r.getMessage() for r in log.records)

def test_log_text(log: LogCapture) -> None:
    logging.warning("hello")
    assert "hello" in log.text
```

- `log.records` — list of `logging.LogRecord` objects captured since last reset
- `log.text` — all captured records formatted as `LEVEL    message` lines
- `log.set_level(level, logger=None)` — set the minimum capture level; pass a logger name to target a specific logger
- `log.at_level(level, logger=None)` — context manager: temporarily change capture level, then restore

```python
def test_debug_logs(log: LogCapture) -> None:
    with log.at_level(logging.DEBUG, logger="myapp"):
        logging.getLogger("myapp").debug("low-level detail")
    assert any("low-level detail" in r.getMessage() for r in log.records)
```

### Plugin backends

`LogCapture` automatically picks up log backends provided by plugins. For example,
a loguru plugin would register a `LogBackend` that captures loguru output into the
same `log.records` list:

```toml
[tool.oxitest]
plugins = ["oxitest_loguru"]
```

With this configured, `log.records` includes records from both Python's stdlib
`logging` module and loguru. No changes to test code required — plugin backends
are installed and torn down automatically.

See [Plugin System](../reference/configuration.md#plugins) for how to declare plugins.

## WarnCapture — Python warnings

`WarnCapture` captures every `warnings.warn()` call made during a test, including
warnings emitted in fixture teardown:

```python
import warnings
from oxitest import WarnCapture

def test_emits_deprecation(warn: WarnCapture) -> None:
    warnings.warn("old_api is deprecated", DeprecationWarning)
    assert len(warn.list) == 1
    assert issubclass(warn.list[0].category, DeprecationWarning)
```

- `warn.list` — `list[warnings.WarningMessage]`, all warnings captured so far
- `warn.clear()` — reset `.list` to `[]` between assertion blocks

```python
def test_two_phases(warn: WarnCapture) -> None:
    phase_one()
    warn.clear()
    phase_two()
    assert len(warn.list) == 1
```

`WarnCapture` and `oxitest.warns()` are complementary:

| | `oxitest.warns()` | `WarnCapture` |
|---|---|---|
| Style | Inline context manager | Auto-installed fixture |
| Best for | Asserting a specific call site emits a warning | Inspecting all warnings in a test, including teardown |
| Captures teardown warnings | No | Yes |

## Access built-in fixtures via `fx.oxi`

If your test already uses the [namespace proxy](use-fixtures.md#access-built-in-fixtures-via-fxoxi)
(`fx: Fixtures`), all built-in fixtures are available under `fx.oxi` — no separate
parameter needed:

```python
from oxitest import Fixtures

def test_combined(fx: Fixtures) -> None:
    fx.oxi.patch.setenv("ENV", "test")
    fx.oxi.log.set_level(logging.DEBUG)
    result = run_job()
    assert "started" in fx.oxi.log.text
    (fx.oxi.tmp.path / "result.txt").write_text(result)
```

This is equivalent to declaring `tmp: TempDir, patch: Patcher, log: LogCapture` as
separate parameters.

## Plugin-provided fixtures

Plugins can register custom fixtures injectable via `Fixture[T]` annotations,
just like built-in fixtures. For example, a database plugin might provide:

```toml
[tool.oxitest]
plugins = ["oxitest_db"]
```

```python
from oxitest import Fixture
from oxitest_db import Database

def test_query(db: Fixture[Database]):
    result = db.execute("SELECT 1")
    assert result == 1
```

Plugin fixtures are resolved by **type**, not by name — the parameter name (`db`) doesn't
matter, only the `Fixture[Database]` annotation. If a conftest fixture and a plugin fixture
provide the same type, the conftest fixture wins.

Plugin fixtures are torn down automatically after each test.

See [Plugin System](../reference/configuration.md#plugins) for how to declare plugins.
