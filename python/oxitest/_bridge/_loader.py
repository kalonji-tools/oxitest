from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from oxitest._bridge._assert_error import (
    _OXITEST_NO_RHS,
    _OxitestAssertionError,
)
from oxitest._bridge._errors import LoadError as _LoadError
from oxitest._bridge.result import _error_result
from oxitest._oxitest import rewrite_asserts

__all__ = [
    "LoadKind",
    "ModuleCache",
    "_LoadError",
    "_load_module",
    "_resolve_fn",
    "already_imported",
]

LoadKind = Literal["test", "doctest"]


class ModuleCache:
    """Cache of loaded+rewritten modules for the duration of a module group.

    Keyed by ``(absolute module path, load kind)``. Owned by FixtureSession.
    Evicted by end_module — providing pytest-level isolation (module state
    shared within a group, cleared between groups).

    The kind is load-bearing (#1962). The test route AST-rewrites asserts and
    injects ``_OxitestAssertionError`` into module globals; the doctest route
    executes the source as written. A shared key would let one route serve the
    other's module, and nothing would raise — the unrewritten module's failures
    surface as plain ``AssertionError`` and lose their operand detail.

    ``strict = "abort"`` is **not** a backstop here: bare-assert detection
    parses the source file in Rust at collection time (``src/bare_asserts.rs``)
    and never inspects the module object this cache serves.
    """

    def __init__(self) -> None:
        self._modules: dict[tuple[str, LoadKind], Any] = {}

    def get(self, module_path: str, *, kind: LoadKind) -> Any | None:
        return self._modules.get((module_path, kind))

    def set(self, module_path: str, module: Any, *, kind: LoadKind) -> None:
        self._modules[(module_path, kind)] = module

    def evict(self, module_path: str) -> None:
        """Drop every kind for *module_path*."""
        for key in [k for k in self._modules if k[0] == module_path]:
            del self._modules[key]


_SYNTHETIC_PREFIX = "_oxitest_"


def already_imported(module_path: str) -> ModuleType | None:
    """Return the module already imported from *module_path*, if any.

    Executing a file that is already in ``sys.modules`` under its real dotted
    name builds a second set of class objects for everything it defines. For
    ``_builtins/*`` that re-fires ``BuiltinFixture.__init_subclass__`` into a
    registry that is never evicted, so the duplicates accumulate for the life
    of the worker (#1962).

    Both load routes use this, and each applies its own policy at the call
    site. The doctest route reuses whatever it finds, because it never
    rewrites. The collection route reuses only modules inside this package,
    because ``_load_module`` also AST-rewrites asserts and skipping that is
    wrong for a test file (#2014).

    Executing against the canonical module rather than a private copy is
    deliberate: it is what "one identity per module" means, and it matches
    how pytest's own doctest collection behaves. The cost is that code which
    mutates module state at import now leaks into the rest of the run;
    accepted.

    ``conftest_loader`` also registers each conftest.py under the bare key
    ``"conftest"``. That is a legitimate match — the comparison below is on
    the resolved path, so it can never return the wrong file — but the key is
    last-writer-wins across siblings, and which conftest wins varies with the
    modules a run selects (``-k``, positional paths, ``--affected``). So in a
    multi-conftest project one conftest's doctests get canonical reuse and its
    siblings' get a fresh execution, and which is which is not fixed.

    Resolution is not skippable: raw ``__file__`` strings can differ for the
    same file (``/var`` vs ``/private/var`` and similar symlink spellings,
    #1957), so only the resolved path is a safe comparison. The basename
    pre-filter that avoids most of those resolutions is explained at its
    call site below.
    """
    try:
        target = Path(module_path).resolve()
    except OSError:
        return None
    target_name = target.name
    # list(...) snapshots the dict: a PEP 562 module __getattr__, reached via
    # the getattr below, can import and mutate sys.modules mid-iteration,
    # which raises RuntimeError: dictionary changed size during iteration
    # over a live view.
    for name, module in list(sys.modules.items()):
        if name.startswith(_SYNTHETIC_PREFIX):
            continue
        file = getattr(module, "__file__", None)
        # Basename first, so most candidates never reach the resolve() below.
        # os.path.basename ~165us/call against this repo's ~420-entry
        # sys.modules; Path(file).name ~1026us for the same string op, and
        # this runs once per collected or doctested module. PTH119 prefers
        # pathlib; measured
        # here, pathlib is the 6-8x slower way to read one string.
        if file is None or os.path.basename(file) != target_name:  # noqa: PTH119
            continue
        try:
            if Path(file).resolve() == target:
                return module
        except OSError:
            continue
    return None


def _load_module(module_path: str, unique_name: str) -> Any:
    """Load a Python file with AST assertion rewriting applied.

    Returns the loaded module.
    Raises _LoadError if the file cannot be read, parsed, or executed.
    unique_name is used as the sys.modules key; caller is responsible for cleanup.
    """
    path = Path(module_path)
    spec = importlib.util.spec_from_file_location(unique_name, path)
    if spec is None or spec.loader is None:
        raise _LoadError(_error_result(f"Cannot load module from {module_path}"))

    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _LoadError(_error_result(traceback.format_exc())) from exc
    try:
        tree, bare_asserts = rewrite_asserts(source, module_path)
        code = compile(tree, module_path, "exec")
    except SyntaxError as exc:
        raise _LoadError(_error_result(traceback.format_exc())) from exc

    module = importlib.util.module_from_spec(spec)
    module.__dict__["_OxitestAssertionError"] = _OxitestAssertionError
    module.__dict__["_oxitest_no_rhs"] = _OXITEST_NO_RHS
    module.__dict__["_oxitest_bare_asserts"] = bare_asserts
    sys.modules[unique_name] = module
    try:
        exec(code, module.__dict__)  # noqa: S102 — exec required for AST-rewritten module loading
    except Exception as exc:
        sys.modules.pop(unique_name, None)
        raise _LoadError(_error_result(traceback.format_exc())) from exc
    return module


def _resolve_fn(
    module: object, fn_name: str, module_path: str
) -> tuple[object, Callable[..., Any]]:
    """Return (fn_raw, fn) for the named function in module.

    fn_raw is the original unbound function (used for mark inspection).
    fn is the callable to invoke (same as fn_raw for module-level functions;
    a bound method for class methods).
    Raises _LoadError on failure.
    """
    if "::" in fn_name:
        cls_name, method_name = fn_name.split("::", 1)
        cls = getattr(module, cls_name, None)
        if cls is None:
            raise _LoadError(
                _error_result(f"Class '{cls_name}' not found in {module_path}")
            )
        fn_raw = getattr(cls, method_name, None)
        if fn_raw is None:
            raise _LoadError(
                _error_result(f"Method '{method_name}' not found in class '{cls_name}'")
            )
        return fn_raw, getattr(cls(), method_name)
    fn_raw = getattr(module, fn_name, None)
    if fn_raw is None:
        raise _LoadError(
            _error_result(f"Function '{fn_name}' not found in {module_path}")
        )
    return fn_raw, fn_raw
