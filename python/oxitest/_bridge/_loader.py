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
        """Drop every kind for *module_path*, and its dotted registration.

        The dotted name has to go with the module (#1680). This cache is the
        module-state boundary — ``end_module`` evicts it so one module group
        does not observe the previous group's module-level state — and a
        surviving ``sys.modules`` entry would hand that state to anything
        importing the module by name, which is the boundary the eviction
        exists to draw.

        Only an entry that still refers to *this* module object is removed. If
        something else has since claimed the name, it owns it now.
        """
        for key in [k for k in self._modules if k[0] == module_path]:
            _unregister_dotted(self._modules.pop(key))


_SYNTHETIC_PREFIX = "_oxitest_"

# Marks a module object this loader built, whatever key it is registered under.
#
# The prefix above was always a proxy for "oxitest made this module", and it
# stops being a reliable one once a module is also registered under its real
# dotted name (#1680). The marker states the same fact directly, so the skip in
# ``already_imported`` no longer depends on how the module happens to be spelled
# in ``sys.modules``.
_OXITEST_LOADED = "__oxitest_loaded__"


def _unregister_dotted(module: Any) -> None:
    """Remove *module*'s dotted ``sys.modules`` entry, if it owns one.

    Three guards, each declining rather than deleting:

    * a module with no dotted name has nothing to remove — its ``__name__`` is
      the synthetic key, which its own caller owns;
    * a module this loader did not build is not ours to unregister;
    * an entry that no longer refers to this object belongs to whoever
      replaced it.
    """
    name = getattr(module, "__name__", "")
    if not name or name.startswith(_SYNTHETIC_PREFIX):
        return
    if not getattr(module, _OXITEST_LOADED, False):
        return
    if sys.modules.get(name) is module:
        del sys.modules[name]


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
        if getattr(module, _OXITEST_LOADED, False):
            # A module this loader built, reachable under its real dotted name
            # (#1680). Returning it would hand a caller the AST-rewritten copy
            # as though it were the canonical import, which is what the prefix
            # skip above exists to prevent — the dotted registration simply
            # gives the same module a second, unprefixed spelling.
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


def _load_module(
    module_path: str, unique_name: str, *, spec_name: str | None = None
) -> Any:
    """Load a Python file with AST assertion rewriting applied.

    Returns the loaded module.
    Raises _LoadError if the file cannot be read, parsed, or executed.
    unique_name is used as the sys.modules key; caller is responsible for cleanup.

    *spec_name* is the module's own identity — its ``__name__``, and the
    ``__package__`` derived from it. ``None`` keeps the older behaviour, where
    the identity and the ``sys.modules`` key are one synthetic string.

    The two are separate because they answer different questions (#1680). The
    identity should be the real dotted name wherever one is truthful, because
    relative imports and caller-identity introspection both read it.

    When *spec_name* is given the module is registered under **both** keys, and
    ``already_imported`` skips it by the ``_OXITEST_LOADED`` marker rather than
    by the ``_oxitest_`` key prefix. #1962's duplicate-registration fix is
    preserved that way: the fact it needs is "oxitest built this module", which
    the marker states directly and the prefix only ever approximated.

    It is passed to ``spec_from_file_location`` rather than assigned to
    ``module.__package__`` afterwards: CPython compares ``__package__`` against
    ``__spec__.parent`` during a relative import and warns when they disagree —
    ``ImportWarning`` on 3.11, ``DeprecationWarning`` on 3.12 through 3.14.
    """
    path = Path(module_path)
    spec = importlib.util.spec_from_file_location(spec_name or unique_name, path)
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
    module.__dict__[_OXITEST_LOADED] = True
    sys.modules[unique_name] = module
    # What the dotted key held before this call, so a failed load can put it
    # back. The key is not necessarily free: a relative import from a sibling
    # registers a module under exactly this name through the ordinary import
    # system, so popping it on error would delete a module this call did not
    # create. Absence and a stored ``None`` are different states, hence the
    # membership test rather than a ``get`` with a default.
    # Held as a mapping rather than a value: an empty one means "the key was
    # free", which a `None` value cannot say — `sys.modules[name] = None` is a
    # legal import-blocking entry, so absence and a stored `None` are
    # different states. Restoring is then `update`, and it is a no-op when the
    # key was free.
    displaced = {
        key: sys.modules[key]
        for key in (spec_name,)
        if key is not None and key in sys.modules
    }
    if spec_name is not None:
        # The dotted name has to be a live sys.modules key, not only the
        # module's __name__. The standard library resolves a class's module by
        # looking its __module__ up here and dereferencing without a guard —
        # `dataclasses._is_type` does `sys.modules.get(cls.__module__).__dict__`
        # — and `typing.get_type_hints` reads the same mapping to evaluate
        # string annotations after the module body has finished. Registering
        # only the synthetic key took this repository's own suite to 46
        # collection errors, because 49 of its test files define a dataclass
        # and `from __future__ import annotations` makes the annotations
        # strings.
        #
        # Both keys point at one module object, so this is a second spelling
        # rather than a second import. `already_imported` skips it by the
        # marker above.
        sys.modules[spec_name] = module
    try:
        exec(code, module.__dict__)  # noqa: S102 — exec required for AST-rewritten module loading
    except Exception as exc:
        sys.modules.pop(unique_name, None)
        if spec_name is not None:
            sys.modules.pop(spec_name, None)
            sys.modules.update(displaced)
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
