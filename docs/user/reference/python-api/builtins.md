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

## FdCaptureResult

::: oxitest._bridge._builtins._capture.FdCaptureResult
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
        - current
        - name
        - module_path
        - node_id
        - param_id
        - marks
        - param
        - addfinalizer
        - on_teardown

### Reaching the context

`oxi.current_test()` is the way to reach the running test's context. It is a
module-level alias for `TestContext.current()` — same call, and the shorter
reach is usually what you want, since code asking for the context rarely needs
anything else off the class:

```python
import oxitest as oxi

def install(name: str) -> None:
    # A plain function. Nothing injects into it — current_test() still works.
    oxi.current_test().on_teardown(lambda: uninstall(name))

def test_example() -> None:
    install("thing")
    assert oxi.current_test().name == "test_example"
```

!!! warning "Do not register cleanup from inside a teardown"

    Calling `addfinalizer()` / `on_teardown()` from a callback that is itself
    running as a teardown registers the callback and then never runs it.
    oxitest reports this — see
    [teardown registration](../errors.md) in the error reference. Do the
    cleanup inline in the teardown you are already inside.

Use `TestContext.current()` where you already have the class in hand. The two
are the same call — there is one position rule, and the alias delegates to it.

A helper that must work both inside and outside a test can catch the refusal:

```python
import oxitest as oxi

def node_id_if_running() -> str | None:
    try:
        return oxi.current_test().node_id
    except oxi.TestContextUnavailableError:
        return None
```

It is legal from a test body and from any plain function that body calls.
Everywhere else it raises `TestContextUnavailableError` naming the position,
with one exception: during teardown it returns normally, because reading the
running test's identity there is legitimate. What is refused in that position
is *registering* new cleanup — see the warning below.

Inside a **fixture** body, declare `ctx: TestContext` as a parameter — that
context supports `on_teardown` and `module_path`, though not the test's
identity (#1874).

!!! warning "Legacy spelling"
    `ctx: TestContext` parameter injection **on a test** still works and stays
    semver-protected; no retirement is scheduled. Prefer
    `TestContext.current()` in new code. On a **fixture** the parameter is not
    legacy — it is the documented route.

    `fx.oxi.ctx` is **removed**.

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

Inside a fixture, `ctx` is for teardown. The four identity fields raise:

| Member | On a test | Inside a fixture |
| --- | --- | --- |
| `addfinalizer` / `on_teardown` | works | works — this is the point |
| `module_path` | works | works |
| `name`, `node_id`, `marks`, `param_id` | works | **`TestIdentityUnavailableError`** |

A fixture is built for whichever test arrives first at its lifetime tier, so
"the current test" has no answer above `function` lifetime — and at `function`
lifetime the identity is not threaded to the resolution site either. Reading
one used to return the *fixture's* own name, so `f"test_{ctx.name}"` produced
one identical string for the whole run.

If a fixture needs the test's identity, declare `ctx: TestContext` on the
**test** — where the four fields are real — and hand the value to the fixture
from the test body.

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
