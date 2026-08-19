"""Module identity for both load routes (#1962 §2, #2014).

The test route AST-rewrites asserts and injects globals; the doctest route
executes source as written. They must never serve each other's module.

Both routes reuse an already-imported module rather than executing a second
copy. The collection route scopes that reuse to this package, because reuse
skips the rewrite the test route depends on.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import oxitest as oxi
import oxitest._bridge._loader as _loader_module
from oxitest import Patcher, TempDir, TestContext, parametrize
from oxitest._bridge._builtins import _capture, _tempdir
from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._doctest_runner import _doctest_module_name, _import_doctest_module
from oxitest._bridge._loader import ModuleCache, already_imported
from oxitest._bridge.importer import _import_test_module, collect_module
from oxitest._bridge.result import ErrorResult
from tests import helpers


def test_module_cache_keeps_the_two_load_routes_apart() -> None:
    """A path cached for one route must not be served to the other."""
    cache = ModuleCache()
    test_module = object()
    doctest_module = object()
    other_module = object()

    cache.set("/p/m.py", test_module, kind="test")
    cache.set("/p/m.py", doctest_module, kind="doctest")
    cache.set("/p/other.py", other_module, kind="test")

    assert cache.get("/p/m.py", kind="test") is test_module, (
        "the test route must get the AST-rewritten module — serving it the "
        "doctest route's copy silently stops bare asserts being rewritten"
    )
    assert cache.get("/p/m.py", kind="doctest") is doctest_module, (
        "the doctest route must get the unrewritten module — doctest examples "
        "should execute the source as written"
    )
    assert cache.get("/p/other.py", kind="test") is other_module, (
        "a cache keyed by kind alone, ignoring path, would pass the two "
        "assertions above while conflating every module that shares a kind"
    )


def test_module_cache_evict_clears_both_kinds() -> None:
    """end_module calls evict(path) once; both kinds must go."""
    cache = ModuleCache()
    other_module = object()
    cache.set("/p/m.py", object(), kind="test")
    cache.set("/p/m.py", object(), kind="doctest")
    cache.set("/p/other.py", other_module, kind="test")

    cache.evict("/p/m.py")

    assert cache.get("/p/m.py", kind="test") is None, (
        "a surviving test-route entry outlives its module group, which is the "
        "isolation ModuleCache exists to provide"
    )
    assert cache.get("/p/m.py", kind="doctest") is None, (
        "a surviving doctest entry would outlive its group too — evict() is "
        "the only call end_module makes, so it must clear everything"
    )
    assert cache.get("/p/other.py", kind="test") is other_module, (
        "evict is scoped to one path — dropping another module's entry re-runs "
        "its body mid-group, losing the module state the cache exists to keep"
    )


def test_doctest_module_name_is_stable_for_equal_paths() -> None:
    """Equal paths must produce one name, whatever string objects they are.

    The defect was ``id(module_path)``: two equal paths built at runtime are
    different objects with different ids, so every doctest item re-executed
    its module under a fresh name (#1962).
    """
    one = "/project/pkg/mod.py"
    other = "".join(  # noqa: FLY002 — a literal here is the fold ruff suggests,
        # which collapses the two objects this test depends on being distinct
        ["/project/pkg/", "mod.py"]
    )

    assert one is not other, (
        "the arrange step is void if the interpreter folded these into one "
        "object — the whole point is two distinct objects with equal values"
    )
    assert _doctest_module_name(one) == _doctest_module_name(other), (
        "a name derived from object identity rather than path content makes "
        "every doctest item a fresh module, re-registering every class in it"
    )


def test_import_doctest_module_reuses_the_cached_module() -> None:
    """Two doctest items from one module share one module object."""
    cache = ModuleCache()
    path = str(Path(__file__).parent / "test_doctest_module_identity.py")

    def _cleanup() -> None:
        sys.modules.pop(_doctest_module_name(path), None)

    # This call is exactly the "stray module identity" pattern #1962 removes
    # elsewhere — clean up the entry it leaves in sys.modules so this test
    # doesn't itself become a nobody-evicts-it residue for the worker's life.
    TestContext.current().on_teardown(_cleanup)

    first = _import_doctest_module(path, cache)
    second = _import_doctest_module(path, cache)

    assert first is second, (
        "re-executing the module body re-runs every class definition in it, "
        "so BuiltinFixture.__init_subclass__ fires again and the registry "
        "gains a duplicate that is never evicted"
    )


def test_import_doctest_module_reuses_an_already_imported_module() -> None:
    """A path already in sys.modules under its real dotted name is not re-executed.

    Caching (the test above) only stops *re*-execution within one module
    group — it cannot stop the *first* execution. A module that oxitest's own
    normal package import already loaded (real dotted name, not one of the
    synthetic per-route names) must be reused instead of executed a second
    time: for a ``_builtins/*`` module, a second execution re-fires
    ``BuiltinFixture.__init_subclass__`` and the resulting duplicate is never
    evicted (#1962). Using a real, already-imported oxitest module — rather
    than a bespoke fixture module — exercises the actual condition: the
    module this test imports is the same one the whole process already
    shares, which is what the assertion below checks by identity.
    """
    # Arrange — this module is already in sys.modules under its real dotted
    # name because the top of this file imports it normally.
    cache = ModuleCache()
    path = _loader_module.__file__
    assert path is not None, (
        "the arrange step needs a real file path to hand to "
        "_import_doctest_module — a module with no __file__ can't exercise "
        "this condition at all"
    )

    # Act
    result = _import_doctest_module(path, cache)

    # Assert
    assert result is _loader_module, (
        "a distinct object here means the doctest route built its own copy "
        "of every class this file defines instead of reusing the one every "
        "other import in the process shares — ModuleCache and LoadKind would "
        "silently fork into two incompatible definitions"
    )


@dataclass(frozen=True)
class SyntheticRouteCase:
    """One of oxitest's own sys.modules write-site names, for the test below."""

    synthetic_name: str


# Each case pins one synthetic sys.modules name. The guard under test is a
# single prefix comparison, so every case exercises one branch; the cases are
# distinct names rather than distinct code paths. Four name live write sites
# (executor._exec_unique_name, importer.collect_module, _doctest_module_name,
# importer's fixture-module route). The conftest one names a route #1720
# retired, and is kept: the guard must still refuse the prefix.
# Installing under only one route — _oxitest_exec_, say — would pass against
# the earlier three-item enumeration too, since that route was one of the
# three it already listed; the point of this test is the two routes that
# enumeration missed, so every route needs its own case (#1962). Dataclass
# mode, not dict mode: this repo's own tests run under strict = "abort",
# which rejects dict-parametrize as a violation.
@parametrize(
    exec_route=SyntheticRouteCase(synthetic_name="_oxitest_exec_deadbeefcafe"),
    collect_route=SyntheticRouteCase(synthetic_name="_oxitest_collect_deadbeefcafe"),
    doctest_route=SyntheticRouteCase(synthetic_name="_oxitest_doctest_deadbeefcafe"),
    conftest_route=SyntheticRouteCase(synthetic_name="_oxitest_conftest_deadbeefcafe"),
    fixture_module_route=SyntheticRouteCase(
        synthetic_name="_oxitest_fixture_module_deadbeefcafe"
    ),
)
def test_import_doctest_module_ignores_a_synthetic_sys_modules_entry(
    tmp: TempDir, synthetic_name: str
) -> None:
    """A module installed under any of oxitest's own synthetic names is not canonical.

    Without the ``_SYNTHETIC_PREFIX`` guard, ``already_imported`` would treat
    any ``sys.modules`` entry as a real import as long as its ``__file__``
    resolves to the target path — including another oxitest route's own
    private copy. A probe against this exact defect got back
    ``sys.modules["conftest"]`` for a doctest inside a conftest.py, on a tree
    where oxitest still loaded one; a hand-enumerated prefix list here (an
    earlier revision of this file) still missed two of the write sites that
    existed on the day it was written (#1962).
    """
    # Arrange — a real file on disk, so the resolved-path comparison in
    # already_imported can succeed, with a fake module installed under a
    # synthetic name pointing at it. Nothing else in the process has any
    # reason to have imported this fresh file under a real dotted name, so
    # the fake is the *only* sys.modules entry that can match by path.
    target_path = helpers.write_test_module(
        tmp, "value = 'real execution happened'\n", name="probe_module.py"
    )
    fake = helpers.make_plugin_module("not_the_real_thing", __file__=target_path)
    helpers.install_module(synthetic_name, fake)
    cache = ModuleCache()

    def _cleanup() -> None:
        # _import_doctest_module reaches the fresh-execution path by design
        # here — it must, since the fake is correctly ignored — which leaves
        # exactly the stray sys.modules entry the neighbouring cache test
        # was fixed to clean up. Mirror that fix here too.
        sys.modules.pop(_doctest_module_name(target_path), None)

    TestContext.current().on_teardown(_cleanup)

    # Act
    result = _import_doctest_module(target_path, cache)

    # Assert
    assert result is not fake, (
        f"a module found under the {synthetic_name!r} sys.modules key is "
        "another route's private copy, not a real import — returning it "
        "means the doctest route can be handed another route's own private "
        "module, defeating the isolation Task 1 built"
    )
    assert not isinstance(result, ErrorResult), (
        f"expected a genuine fresh execution of {target_path}, got an error "
        f"instead: {result!r}"
    )
    assert getattr(result, "value", None) == "real execution happened", (
        "the returned module should be a real fresh execution of the target "
        "file's own body, not merely some object that isn't `fake`"
    )


def test_import_doctest_module_cleans_up_sys_modules_on_exec_failure(
    tmp: TempDir,
) -> None:
    """A module whose body raises must not leave a half-executed entry behind.

    ``_load_module`` (the test route) already pops on failure; the doctest
    route's own failure path must match it, or a later lookup by synthetic
    name could find a poisoned, half-executed module left over from this one
    rather than a clean absence (#1962).

    The absence check alone cannot tell "popped after registering" from
    "never registered at all" — dropping the pre-registration line entirely
    would also leave the name absent afterwards and pass just as well. The
    marker below records, from *inside* the failing module's own body, that
    it really was in sys.modules under its own name before it raised, so the
    final absence check is provably a cleanup rather than a no-op.
    """
    # Arrange — the broken module records its own registration state onto
    # `sys` (a module that outlives it) before raising, since nothing can
    # read the failing module's own namespace back out afterward.
    marker_attr = "_oxitest_test_1962_was_registered"
    path = helpers.write_test_module(
        tmp,
        "import sys\n"
        f"setattr(sys, {marker_attr!r}, __name__ in sys.modules)\n"
        "raise RuntimeError('boom during import')\n",
        name="broken_module.py",
    )
    cache = ModuleCache()
    unique_name = _doctest_module_name(path)

    def _cleanup() -> None:
        if hasattr(sys, marker_attr):
            delattr(sys, marker_attr)

    TestContext.current().on_teardown(_cleanup)

    # Act
    result = _import_doctest_module(path, cache)

    # Assert
    assert getattr(sys, marker_attr, None) is True, (
        "the module never observed itself as registered before raising — "
        "the assertion below would prove nothing about a transition, only "
        "that a module which was never registered is, unsurprisingly, absent"
    )
    assert isinstance(result, ErrorResult), (
        f"a module whose body raises must surface as an ErrorResult, got "
        f"{result!r} instead"
    )
    assert unique_name not in sys.modules, (
        "a half-executed module left behind under its synthetic name can be "
        "found by a later lookup that assumes a successful import, surfacing "
        "a confusing failure far from the real cause"
    )


def test_loading_a_builtin_module_for_doctests_adds_no_registry_duplicates() -> None:
    """The doctest route must not re-register a built-in module's classes.

    Loading a ``_builtins/*`` module under a second identity re-runs every
    class body in it, re-firing ``BuiltinFixture.__init_subclass__`` into a
    registry that is never evicted. This drives that load directly rather
    than waiting to observe it: doctest items are always scheduled after
    regular tests, so an observational assertion here can never see the
    condition it exists to catch (#1962).
    """
    before = sorted(t.__name__ for t in BuiltinFixture.registered_types())

    result = _import_doctest_module(str(Path(_tempdir.__file__)), ModuleCache())

    assert not isinstance(result, ErrorResult), (
        "the arrange step is void if the module failed to load — the "
        "assertion below would then pass without exercising anything"
    )
    after = sorted(t.__name__ for t in BuiltinFixture.registered_types())
    assert after == before, (
        "loading a built-in module through the doctest route registered its "
        "classes again; BuiltinFixture.for_type() resolves by class identity, "
        "so which object a caller gets would depend on which route loaded it"
    )


# ── The collection route (#2014) ──────────────────────────────────────────────


def test_collecting_a_builtin_module_adds_no_registry_duplicates() -> None:
    """The collection route must not re-register a built-in module's classes.

    Doctest coverage makes a source module collectable, so ``collect_module``
    reaches ``_builtins/*`` and used to execute it a second time under an
    ``_oxitest_collect_*`` name (#2014). This drives that route directly:
    observing it after the fact cannot work, because whether a worker collects
    a built-in before the shape-rule test is scheduling-dependent.
    """
    # Arrange
    before = sorted(cls.__name__ for cls in BuiltinFixture.registered_types())

    # Act
    collect_module(str(Path(_tempdir.__file__)))

    # Assert
    after = sorted(cls.__name__ for cls in BuiltinFixture.registered_types())
    assert after == before, (
        "collecting a built-in module registered its classes a second time; "
        "BuiltinFixture.for_type() resolves by class identity, so which object "
        "a caller gets would then depend on which route loaded the module"
    )


def test_collecting_a_module_outside_the_package_still_rewrites_asserts() -> None:
    """Reuse is scoped to this package, so a test file keeps its rewrite.

    ``_load_module`` injects ``_OxitestAssertionError`` and rewrites asserts so
    a failure carries operand detail. Reusing an already-imported module skips
    that. Test infrastructure is canonically importable — ``tests.helpers`` is —
    so an unscoped reuse rule would silently strip that detail (#2014).
    """
    # Arrange — helpers is imported at module scope above, so it is already in
    # sys.modules under a canonical, non-oxitest name. That is the condition.
    assert not helpers.__name__.startswith("oxitest."), (
        "this test is void unless the arranged module sits outside the oxitest "
        "package — inside it, reuse is correct and the assertion below would "
        "pass for the wrong reason"
    )

    # Act
    module = _import_test_module(str(Path(helpers.__file__)), "_probe_2014", None)

    # Assert
    assert "_OxitestAssertionError" in module.__dict__, (
        "a module outside the oxitest package was reused instead of loaded, so "
        "its asserts were never rewritten; every assertion in it would report "
        "without operand detail and nothing would announce the loss"
    )


@dataclass(frozen=True)
class _FakeStat:
    """The two fields ``os.path.samestat`` reads, and nothing else.

    A real ``os.stat_result`` cannot be built with an arbitrary inode without
    reconstructing all ten fields, and only these two decide identity.
    """

    st_ino: int
    st_dev: int


def _stat_with_zero_inode(
    real_stat: Callable[..., Any], target_name: str
) -> Callable[..., Any]:
    """Return an ``os.stat`` that reports a zero inode for *target_name* only.

    Delegating for every other path keeps the patch from reaching the test
    runner's own stat calls, which happen while this patch is installed.
    """

    def fake(path: Any, *args: Any, **kwargs: Any) -> Any:
        info = real_stat(path, *args, **kwargs)
        if Path(str(path)).name == target_name:
            return _FakeStat(st_ino=0, st_dev=info.st_dev)
        return info

    return fake


def test_an_unusable_inode_declines_the_match(patch: Patcher) -> None:
    """A zero inode must decline, not compare equal to every sibling.

    ``os.path.samestat`` is ``st_ino == st_ino and st_dev == st_dev``, so two
    zero-inode stats compare **equal**. The basename pre-filter does not save
    us: ``sys.modules`` holds many entries sharing a basename. Without this
    guard a filesystem that reports no inode would make ``already_imported``
    return the first same-named module it walks past, and the collection route
    would reuse the wrong module and skip its AST rewrite (#2018).
    """
    # Arrange
    target = str(Path(_tempdir.__file__))
    patch.setattr(os, "stat", _stat_with_zero_inode(os.stat, Path(target).name))

    # Act
    found = already_imported(target)

    # Assert
    assert found is None, (
        "a zero inode was treated as a usable identity, so any module sharing "
        "this basename could be returned in place of the right one — the "
        "silent wrong-module reuse this guard exists to prevent"
    )


def test_two_files_sharing_a_basename_do_not_match(tmp: TempDir) -> None:
    """Identity must discriminate, not accept anything the pre-filter allowed.

    The pre-filter passes every ``sys.modules`` entry whose basename matches,
    so the comparison after it is the only thing separating one file from a
    same-named other. Nothing else in this file asserts a **negative** match,
    which is what lets a mutant replacing the comparison with ``True`` survive
    (#2018).
    """
    # Arrange — a real, different file with the same basename as an imported one
    imported = Path(_tempdir.__file__)
    impostor = tmp.path / imported.name
    impostor.write_text("# not the module oxitest imported\n", encoding="utf-8")

    # Act
    found = already_imported(str(impostor))

    # Assert
    assert found is None, (
        f"a different file named {imported.name!r} was accepted as the "
        f"imported module; reusing it would run the wrong source and skip the "
        f"AST rewrite the test route depends on"
    )


# ── The Windows spelling (#2018) ──────────────────────────────────────────────
#
# Rust hands Python the output of std::fs::canonicalize, which on Windows is an
# extended-length path. Nothing else in this file exercises that spelling: every
# other test feeds a module's own __file__, so both sides of the comparison
# always agree and the tests pass on Windows while the defect is live.
#
# The three tests below construct the spelling rather than capture it. If
# #1767 makes normalize_path stop emitting the prefix, this reconstruction goes
# stale and these tests keep passing — they assert the comparison tolerates the
# spelling, not that the collector still produces it.

_IS_WINDOWS = sys.platform == "win32"
_NOT_WINDOWS_REASON = (
    "the extended-length path prefix exists only on Windows; on POSIX "
    "resolve() reconciles every spelling, so there is no divergence to "
    "reproduce (#2018)"
)


def _as_rust_spells_it(module_file: str) -> str:
    """Return *module_file* spelled the way std::fs::canonicalize returns it."""
    return "\\\\?\\" + str(Path(module_file).resolve())


@oxi.mark.skip(when=not _IS_WINDOWS, reason=_NOT_WINDOWS_REASON)
def test_already_imported_matches_a_verbatim_spelled_path() -> None:
    r"""The collector's own spelling must find the module the process imported.

    ``ntpath.realpath`` keeps the ``\\?\`` prefix when the input carries it and
    strips it otherwise, so comparing two resolved strings can never match
    here. Comparing file identity does (#2018).
    """
    # Arrange
    target = _as_rust_spells_it(str(_capture.__file__))

    # Act
    found = already_imported(target)

    # Assert
    assert found is _capture, (
        "the collector's own path spelling did not find the module oxitest "
        "already imported, so both load routes will execute a second copy and "
        "every BuiltinFixture in it registers twice"
    )


@oxi.mark.skip(when=not _IS_WINDOWS, reason=_NOT_WINDOWS_REASON)
def test_collecting_a_builtin_via_a_verbatim_path_adds_no_duplicates() -> None:
    """The collection route, driven the way the collector drives it.

    The sibling test above this section drives ``collect_module`` with the
    module's own ``__file__``, which cannot reproduce the divergence. This one
    supplies the spelling Rust actually sends (#2018).
    """
    # Arrange
    before = sorted(cls.__name__ for cls in BuiltinFixture.registered_types())

    # Act
    collect_module(_as_rust_spells_it(str(_capture.__file__)))

    # Assert
    after = sorted(cls.__name__ for cls in BuiltinFixture.registered_types())
    assert after == before, (
        "collecting a built-in through the collector's own path spelling "
        "registered its classes a second time; BuiltinFixture.for_type() "
        "resolves by class identity, so which object a caller gets would "
        "depend on which route loaded the module"
    )


@oxi.mark.skip(when=not _IS_WINDOWS, reason=_NOT_WINDOWS_REASON)
def test_the_doctest_route_reuses_a_builtin_given_a_verbatim_path() -> None:
    """The doctest route's reuse branch, exercised on Windows for the first time.

    That branch reuses whatever ``already_imported`` returns, with no
    package-scope rule — deliberately, because it never rewrites. Since
    ``already_imported`` has always returned ``None`` on Windows, the branch
    has never executed there. A green Windows run cannot tell *correct* from
    *not exercised*, so this asserts the outcome instead (#2018).
    """
    # Arrange
    cache = ModuleCache()

    # Act
    result = _import_doctest_module(_as_rust_spells_it(str(_capture.__file__)), cache)

    # Assert
    assert result is _capture, (
        "the doctest route built its own copy of the module instead of "
        "reusing the imported one, so every BuiltinFixture class in it exists "
        "twice and BuiltinFixture.for_type() resolves to whichever won"
    )


def test_a_path_that_does_not_exist_declines_rather_than_raising(
    tmp: TempDir,
) -> None:
    """A missing file must return None, not raise out of the loader.

    ``Path.resolve()`` with ``strict=False`` never raised, so this branch was
    unreachable in practice before identity replaced it. ``os.stat`` raises for
    every path that is not there, which is an ordinary input — a source file
    deleted between collection and load. An ``OSError`` escaping here would
    surface as a collection failure rather than a cache miss (#2018).
    """
    # Arrange
    missing = tmp.path / "never_written.py"

    # Act
    found = already_imported(str(missing))

    # Assert
    assert found is None, (
        "a path with no file behind it must decline quietly; the caller "
        "treats None as 'not already imported' and loads the module itself"
    )
