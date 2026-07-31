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

## Absolute imports only

`from .helpers import x` does **not** work from a test module. Test modules
are loaded under synthetic names (`_bridge/_loader.py:50`) rather than their
dotted package path, so they have no parent package for a relative import to
resolve against.

Absolute imports work because the rootdir on `sys.path` lets Python resolve
`tests.helpers` (or whatever the package path is) from scratch, independent of
how the importing module itself was loaded.

Inside a *helper* module — one reached via such an absolute import, not
loaded directly by oxitest — relative imports work normally, because that
module is imported through the regular package machinery.

## Known boundary: nested projects

A nested `pyproject.toml` in a monorepo moves the rootdir. Running oxitest
from the repo root and running it from a subproject resolve to different
rootdirs, and therefore support different import spellings for the same
files. This matches how `testpaths` and the rest of configuration discovery
already behave — rootdir detection is not specific to imports.

## See also

- [Configuration reference](../reference/configuration.md#root-directory-detection) —
  how the rootdir is determined
- [Conftest helpers](conftest-helpers.md) — the framework-managed alternative
  for shared test infrastructure
