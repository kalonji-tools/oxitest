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

::: oxitest._bridge.fixtures._TestContext
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3
      members:
        - addfinalizer
        - on_teardown
