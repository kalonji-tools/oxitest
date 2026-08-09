# Use built-in fixtures

!!! abstract "How-to"
    Use oxitest's built-in fixtures for temporary files, output capture, patching, and log capture.

All built-in fixtures are injected by annotating a parameter with the bare type
(no `Fixture[T]` wrapper needed). Import the type from `oxitest`.

!!! note "\"After the test\" means after whatever asked for it"
    The cleanup descriptions below — the temp directory removed, the patches
    reverted, the capture closed — assume the built-in was requested **by a
    test**, which is the common case.

    Request one from inside a [fixture declaration](use-fixtures.md) instead
    and it is cleaned up at *that fixture's* boundary. For a fixture at
    `lifetime="module"` or wider that boundary is not the end of any single
    test, so the resource stays alive for every test the fixture serves. That
    is what makes it usable there at all: a temp directory disposed after the
    first test would leave the fixture handing out a deleted path.

## TempDir — temporary directories

`TempDir` provides a unique temporary directory that is deleted after the test.

```python
--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:tempdir"
```

`tmp.path` is a `pathlib.Path`. The directory is removed after the test regardless
of pass or fail.

## TempDirFactory — session-scoped temp dirs

`TempDirFactory` is a session-scoped factory. Use it when you need multiple named
temp directories or want to share a directory across tests:

```python
--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:tempdirfactory"
```

`factory.mktemp("label")` returns a `TempDir` with a unique subdirectory.

## StdCapture — stdout/stderr at stream level

`StdCapture` captures `sys.stdout` and `sys.stderr` at the Python stream level:

```python
--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:stdcapture"
```

`readouterr()` returns a `CaptureResult(out, err)` and resets the buffers.

Use `cap.disabled()` to temporarily let output pass through:

```python
--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:stdcapture-disabled"
```

## FdCapture — stdout/stderr at fd level

`FdCapture` captures at file descriptor level (fd 1 and fd 2). Use this when the
code under test writes directly to the OS file descriptors (e.g. C extensions,
`os.write`):

```python
--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:fdcapture"
```

The API is identical to `StdCapture`, but the output is not. `FdCapture` reports
the bytes that reached the file descriptor, and on Windows Python's text layer
turns `print()`'s `\n` into `\r\n` before they get there. The same `print()`
therefore gives you `"text\r\n"` here and `"text\n"` from `StdCapture`. Bytes
written with `os.write`, as above, bypass that layer and arrive unchanged.

### Output that is not UTF-8

`readouterr()` decodes the descriptor as UTF-8 and replaces anything it cannot
decode. That is correct for output your test wrote, because oxitest declares
UTF-8 on its own streams — but a C extension or a subprocess may have written in
another encoding, and those bytes come back as `�`.

For that case `FdCapture` returns an [`FdCaptureResult`](../reference/python-api/builtins.md#fdcaptureresult),
which carries the undecoded bytes alongside the decoded text. Decode them with
the encoding you know applies:

```python
def test_c_extension_in_the_ansi_codepage(cap: FdCapture) -> None:
    write_via_c_extension()          # writes cp1252 bytes to fd 1
    result = cap.readouterr()
    assert result.out_bytes.decode("cp1252") == "café\n"
```

oxitest does not guess that encoding. One capture can hold your UTF-8 `print()`
output and a C extension's cp1252 bytes at the same time, so no single decode is
right for all of it. `StdCapture` has no equivalent: it replaces `sys.stdout`
with a `StringIO`, so a foreign writer never reaches it.

## Patcher — attributes, env vars, and directories

`Patcher` provides four patching helpers that are automatically restored after
the test:

```python
--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:patcher-env"

--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:patcher-delenv"

--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:patcher-setattr"

--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:patcher-chdir"
```

All changes are reverted after the test, even if the test raises.

## LogCapture — logging records

`LogCapture` captures Python `logging` output:

```python
--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:logcapture"

--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:logcapture-text"
```

- `log.records` — list of `logging.LogRecord` objects captured since last reset
- `log.text` — all captured records formatted as `LEVEL    message` lines
- `log.set_level(level, logger=None)` — set the minimum capture level; pass a logger name to target a specific logger
- `log.at_level(level, logger=None)` — context manager: temporarily change capture level, then restore

```python
--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:logcapture-atlevel"
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
--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:warncapture"
```

- `warn.warnings` — `tuple[warnings.WarningMessage, ...]`, all warnings captured so far
- `warn.clear()` — reset `.warnings` to `()` between assertion blocks

```python
--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:warncapture-clear"
```

`WarnCapture` and `oxitest.warns()` are complementary:

| | `oxitest.warns()` | `WarnCapture` |
|---|---|---|
| Style | Inline context manager | Auto-installed fixture |
| Best for | Asserting a specific call site emits a warning | Inspecting all warnings in a test, including teardown |
| Captures teardown warnings | No | Yes |

## TestContext — test metadata and finalizers

Inject `TestContext` to access metadata about the running test and register
teardown callbacks:

```python
--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:testcontext"
```

### Register a finalizer

Use `ctx.addfinalizer()` to register a callback that runs after the test,
regardless of pass or fail:

```python
--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:testcontext-finalizer"
```

### Access parametrize info

For parametrized tests, `ctx.param_id` gives the current case identifier. The
case's *values* arrive as ordinary parameters, by name — there is no need to
reach for them through `ctx`:

```python
--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:testcontext-parametrize"
```

## Access built-in fixtures via `fx.oxi`

If your test already uses the [namespace proxy](use-fixtures.md#access-built-in-fixtures-via-fxoxi)
(`fx: Fixtures`), all built-in fixtures are available under `fx.oxi` — no separate
parameter needed:

```python
--8<-- "python/tests/docs/how-to/test_builtin_fixtures.py:fx-oxi"
```

This is equivalent to declaring `tmp: TempDir, patch: Patcher, log: LogCapture` as
separate parameters.

## See also

- [Write plugins](write-plugins.md) — extend oxitest with custom fixtures, reporters, collectors, and execution wrappers via the plugin API
- [Fixture types reference](../reference/python-api/fixture-types.md) — API docs for `Fixture[T]`, `FixtureRef[T]`, `Yields[T]`, and `Fixtures`
