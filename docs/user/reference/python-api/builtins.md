# Built-in Fixtures

!!! abstract "Reference"
    Built-in injectable fixtures provided by oxitest. Annotate parameters
    directly with the public type alias — no ``Fixture[T]`` wrapping needed.

!!! note
    All built-in types are decorated with ``@injectable``, so you write
    ``tmp: TempDir`` rather than ``tmp: Fixture[TempDir]``.

## TempDir

::: oxitest._bridge._builtins._tempdir.TempDir
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3

## TempDirFactory

::: oxitest._bridge._builtins._tempdir.TempDirFactory
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3
      members:
        - mktemp

### Preserving temp dirs on failure

By default, `TempDir` contents are deleted after each test. Use `--keep-tmp` to
preserve them for debugging:

```bash
oxitest --keep-tmp          # preserve on failure (default mode)
oxitest --keep-tmp=failed   # same as above
oxitest --keep-tmp=always   # preserve every temp dir
```

When a temp dir is preserved, its path is printed to stderr:

```
KEPT /tmp/test_writes_file_abc123 (--keep-tmp)
```

This also works with `TempDirFactory` — when `--keep-tmp` is set (any mode),
all factory-created dirs are preserved unconditionally since the factory is
session-scoped and cannot track per-test outcomes.

Configure in `pyproject.toml`:

```toml
[tool.oxitest]
keep_tmp = "failed"
```

## StdCapture

::: oxitest._bridge._builtins._capture.StdCapture
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3
      members:
        - readouterr
        - disabled

## FdCapture

::: oxitest._bridge._builtins._capture.FdCapture
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3
      members:
        - readouterr
        - disabled

## CaptureResult

::: oxitest._bridge._builtins._capture.CaptureResult
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3

## Patcher

::: oxitest._bridge._builtins._patch.Patcher
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3
      members:
        - setattr
        - setenv
        - delenv
        - chdir

## LogCapture

::: oxitest._bridge._builtins._logcapture.LogCapture
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3
      members:
        - records
        - text
        - set_level
        - at_level

## WarnCapture

::: oxitest._bridge._builtins._warncapture.WarnCapture
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3
      members:
        - warnings
        - clear

## TestContext

::: oxitest._bridge._builtin_context.TestContext
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3
      members:
        - name
        - module_path
        - node_id
        - param_id
        - marks
        - param
        - addfinalizer
        - on_teardown

### Usage for Fixture Authors

A fixture consumes `TestContext` the same way a test does — annotate a
parameter with it. Declare the fixture with `@oxi.fixture` in a
`__fixtures__.py`; `lifetime` is a required keyword.

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/__fixtures__.py:ctx-fixture"
```

```python
--8<-- "python/tests/docs/how-to/fixture_anchors/api/test_api.py:ctx-test"
```

!!! warning "`ctx` inside a fixture currently describes the fixture, not the test"
    The `TestContext` injected into a **fixture** body carries that fixture's
    own identity: `ctx.name` reads `"db_schema"`, not the name of the test
    being set up, so every test in the run sees the same value. Do not derive
    per-test names from it. Use `ctx` in a fixture for `addfinalizer`, and read
    `ctx.name`, `ctx.node_id`, `ctx.marks` and `ctx.param_id` from the test's
    own `ctx` parameter, where they describe the test. This holds on both
    fixture routes.

#### Legacy: the same fixture on a `Fixtures()` registrar

!!! warning "Supported, but no longer the primary route"
    This still works and is not deprecated. It is scheduled for removal in
    [#1720](https://github.com/kalonji-tools/oxitest/issues/1720), at which
    point `Fixtures()` and `conftest.py` discovery both go away together. New
    fixtures belong in a `__fixtures__.py`.

```python
# conftest.py
import oxitest as oxi
from oxitest import TestContext

fixtures = oxi.Fixtures()

@fixtures.fixture
def db_schema(ctx: TestContext) -> str:
    schema = "test_schema"
    create_schema(schema)
    ctx.addfinalizer(lambda: drop_schema(schema))
    return schema
```

## See also

- [Use built-in fixtures](../../how-to/use-builtin-fixtures.md) — how-to guide with examples
- [Fixture declaration](fixture-declaration.md) — `@oxi.fixture`, where a declaration may live, the lifetime tiers, and how a `Fixture[T]` parameter resolves
- [Fixture types](fixture-types.md) — `Fixture[T]`, `Yields[T]`, and other type annotations
- [Configuration](../../reference/configuration.md) — `keep_tmp` setting
