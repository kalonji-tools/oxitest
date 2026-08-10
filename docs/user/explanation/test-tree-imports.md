# Test Tree Imports

!!! abstract "Explanation"
    Why test modules can import sibling utility modules, what layouts that
    enables, and where the boundary sits.

## What oxitest does

Before any user code is imported, oxitest determines the project **rootdir** —
the directory found by walking up from the invocation path for
`pyproject.toml`, `setup.cfg`, or `tox.ini` (see [Root directory
detection](../reference/configuration.md#root-directory-detection)) — and
appends it to `sys.path`, once per run.

This happens on every execution path: the serial in-process run, each
parallel worker subprocess, and the inspect TUI session. All three build their
Python session through the same entry point, so the behavior is uniform
regardless of how the suite is invoked.

## What that makes importable

With the rootdir on `sys.path`, a test module can import a sibling module by
its package-qualified path, in whatever shape the project's layout takes.

Src or flat layout:

```python
from tests.helpers import make_user
```

Django-style, with app-local test directories:

```python
from app_users.tests.helpers import make_user
```

No `__init__.py` is required in either case — PEP 420 namespace packages
resolve without one.

## Append, not prepend

The rootdir is appended to the *end* of `sys.path`, never inserted at the
front. An installed distribution — anything found earlier on `sys.path`,
including an editable install — always wins over a same-named directory in
the tree.

If you intend the local copy of your package to be the one under test, install
it with `pip install -e .` rather than relying on import order.

This is deliberate: appending can only make previously-unresolvable names
resolvable. It cannot change a resolution that already succeeds. Enabling
this feature therefore cannot change which code your existing suite runs —
only what new imports become possible.

## Relative imports

`from .helpers import x` works from a test module, and so does anything that
reads the calling module's `__name__` — a logging library deciding whether to
silence a caller, for instance.

Both work because a test module is loaded under the dotted name it would have
if you imported it normally. oxitest derives that name from the file's path
relative to the rootdir, then checks it: the name is used only when it resolves,
through the current `sys.path`, back to that same file.

When the name does not check out, the module keeps an internal name instead and
relative imports from it do not resolve. Three things cause that:

- **another `sys.path` entry owns the name** — an installed distribution that
  also provides a top-level `tests`, say. The rootdir is *appended* to
  `sys.path`, so the installed copy wins;
- **the rootdir is not importable** — nothing on `sys.path` provides the
  top-level name at all;
- **a directory in the path cannot appear in a dotted name** — `my-tests`, for
  example.

In all three, the absolute spelling still works: `from tests.helpers import x`
resolves through `sys.path` from scratch, independent of how the importing
module was loaded.

### A relatively-imported sibling is loaded twice

This is worth knowing before it surprises you.

`from .helpers import x` resolves through Python's ordinary import machinery,
which builds its **own** copy of `helpers` — a different object from the one
oxitest builds when it collects that file. oxitest's copy has its assertions
rewritten, so failures report operand detail; the copy your relative import
receives does not.

It only matters when the sibling holds tests as well as helpers. oxitest runs
its own rewritten copy, so your test results are unaffected; the module-level
code in that file simply runs twice. This matches pytest's default import mode,
and reconciling the two copies would mean claiming a name the fix deliberately
leaves alone.

## Known boundary: nested projects

A nested `pyproject.toml` in a monorepo moves the rootdir. Running oxitest
from the repo root and running it from a subproject resolve to different
rootdirs, and therefore support different import spellings for the same
files. This matches how `testpaths` and the rest of configuration discovery
already behave — rootdir detection is not specific to imports.

## See also

- [Configuration reference](../reference/configuration.md#root-directory-detection) —
  how the rootdir is determined
