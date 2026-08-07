"""Module identity for the doctest load route (#1962 §2).

The test route AST-rewrites asserts and injects globals; the doctest route
executes source as written. They must never serve each other's module.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import oxitest._bridge._loader as _loader_module
from oxitest import TempDir, TestContext, parametrize
from oxitest._bridge._doctest_runner import _doctest_module_name, _import_doctest_module
from oxitest._bridge._loader import ModuleCache
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


# Each case pins one of oxitest's five sys.modules write sites by name
# (executor._exec_unique_name, importer.collect_module, _doctest_module_name,
# conftest_loader._load_conftest_module, importer's fixture-module route).
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

    Without the ``_SYNTHETIC_PREFIX`` guard, ``_already_imported`` would treat
    any ``sys.modules`` entry as a real import as long as its ``__file__``
    resolves to the target path — including another oxitest route's own
    private copy. A probe against this exact defect got back
    ``sys.modules["conftest"]`` for a doctest inside a conftest.py; a hand-
    enumerated prefix list here (an earlier revision of this file) still
    missed two of the five write sites on the day it was written (#1962).
    """
    # Arrange — a real file on disk, so the resolved-path comparison in
    # _already_imported can succeed, with a fake module installed under a
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
