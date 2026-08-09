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
    r"""Return the module already imported from *module_path*, if any.

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

    Identity is compared, not spelling. Raw ``__file__`` strings can differ for
    one file (``/var`` vs ``/private/var`` and similar symlink spellings,
    #1957), and resolving both sides does not reconcile them: on Windows the
    collector's path carries the extended-length ``\\?\`` prefix, and
    ``ntpath.realpath`` keeps that prefix when the input already has it and
    strips it otherwise, so the two resolved strings never compare equal
    (#2018). The kernel already knows which file each path names, so ask it.

    A zero inode on the *target* declines every match. ``os.path.samestat``
    compares ``st_ino`` and ``st_dev``, so two unusable stats compare *equal*,
    and the basename pre-filter does not separate them — ``sys.modules`` holds
    many entries sharing a basename, ``__init__.py`` most of all. Declining
    costs a duplicate registration, which this repository detects. Accepting
    costs a silently reused wrong module.

    The guard is on the target only, because a matching guard on each candidate
    would be unreachable: a zero target returns above before the loop runs, and
    against a non-zero target a zero candidate already fails ``samestat``.

    One hazard survives, unchanged from the comparison this replaces: a module
    imported through a symlink whose basename differs from its target's is
    rejected by the pre-filter before any comparison runs. The pre-filter is
    explained at its call site below.
    """
    try:
        # PTH116 prefers Path.stat(). os.stat is kept because the result is
        # consumed by os.path.samestat, which has no pathlib equivalent —
        # both halves of one comparison stay in one idiom.
        target_stat = os.stat(module_path)  # noqa: PTH116
    except OSError:
        return None
    if target_stat.st_ino == 0:
        return None
    target_name = os.path.basename(module_path)  # noqa: PTH119
    # list(...) snapshots the dict: a PEP 562 module __getattr__, reached via
    # the getattr below, can import and mutate sys.modules mid-iteration,
    # which raises RuntimeError: dictionary changed size during iteration
    # over a live view.
    for name, module in list(sys.modules.items()):
        if name.startswith(_SYNTHETIC_PREFIX):
            continue
        file = getattr(module, "__file__", None)
        # Basename first, so most candidates never reach the stat below.
        # os.path.basename ~165us/call against this repo's ~420-entry
        # sys.modules; Path(file).name ~1026us for the same string op, and
        # this runs once per collected or doctested module. PTH119 prefers
        # pathlib; measured
        # here, pathlib is the 6-8x slower way to read one string.
        if file is None or os.path.basename(file) != target_name:  # noqa: PTH119
            continue
        try:
            candidate_stat = os.stat(file)  # noqa: PTH116 — see the target stat
        except OSError:
            continue
        if os.path.samestat(candidate_stat, target_stat):
            return module
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
