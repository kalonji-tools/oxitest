# Built-in Fixtures

!!! abstract "Reference"
    Built-in injectable fixtures provided by oxitest. Annotate parameters
    directly with the public type alias — no ``Fixture[T]`` wrapping needed.

!!! note
    All built-in types carry an injection marker, so you write
    ``tmp: TempDir`` rather than ``tmp: Fixture[TempDir]``.

## TempDir

::: oxitest._bridge._builtins._tempdir._TempDir
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3

## TempDirFactory

::: oxitest._bridge._builtins._tempdir._TempDirFactory
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3
      members:
        - mktemp

## StdCapture

::: oxitest._bridge._builtins._capture._StdCapture
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3
      members:
        - readouterr
        - disabled

## FdCapture

::: oxitest._bridge._builtins._capture._FdCapture
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

::: oxitest._bridge._builtins._patch._Patcher
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

::: oxitest._bridge._builtins._logcapture._LogCapture
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

::: oxitest._bridge._builtins._warncapture._WarnCapture
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3
      members:
        - list
        - clear

## TestContext

::: oxitest._bridge._fixture_session._TestContext
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

```python
import oxitest as oxi
from oxitest import Fixture, TestContext

fixtures = oxi.Fixtures()

@fixtures.fixture
def db_schema(ctx: Fixture[TestContext]) -> str:
    """Create a test-specific database schema."""
    schema = f"test_{ctx.name}"
    create_schema(schema)
    ctx.addfinalizer(lambda: drop_schema(schema))
    return schema

def test_create_user(schema: Fixture[str], ctx: TestContext) -> None:
    # ctx.name      → "test_create_user"
    # ctx.node_id   → "tests/test_db.py::test_create_user"
    # ctx.marks     → frozenset({"slow"})
    # ctx.param_id  → None (not parametrized)
    ...
```
