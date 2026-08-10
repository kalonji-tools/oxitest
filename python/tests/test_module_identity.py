"""Unit tests for the dotted-name helper (#1680).

A test module loads under a synthetic name, so it gets ``__package__ == ''``
and a ``__name__`` that names nothing. ``dotted_name_for`` computes the name
the file would have if imported normally, and returns it only when that name
resolves back to the same file.

``sys.path`` is replaced wholesale through ``Patcher`` rather than appended to,
so a failing test cannot leak an entry into the rest of the suite.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import warnings
from pathlib import Path
from types import ModuleType

from oxitest import Patcher, TempDir
from oxitest._bridge import _module_identity
from oxitest._bridge._loader import _load_module, _LoadError, already_imported
from oxitest._bridge._module_identity import dotted_name_for
from oxitest._bridge._session_factory import create_session
from oxitest._bridge.executor import _exec_unique_name, _load_and_resolve, _ResolvedTest
from oxitest._bridge.importer import collect_module
from tests import helpers


def _tree(root: Path) -> Path:
    """Build ``proj/proj1680/test_mod.py`` and return the project root.

    The package is **not** called ``tests``. This repository is installed
    editable, which puts ``<repo>/python`` on ``sys.path``, and that directory
    provides a top-level ``tests``. A fixture package by that name is shadowed
    by oxitest's own suite, so ``dotted_name_for`` correctly declines it and
    the test measures the wrong thing.
    """
    proj = root / "proj"
    (proj / "proj1680").mkdir(parents=True)
    (proj / "proj1680" / "__init__.py").write_text("", encoding="utf-8")
    (proj / "proj1680" / "test_mod.py").write_text("X = 1\n", encoding="utf-8")
    return proj


def _shadow(root: Path) -> Path:
    """Build a second provider of the top-level name ``proj1680``.

    Its ``__init__.py`` records that it ran, so a check that imports the parent
    package is detectable rather than merely suspected.
    """
    shadow = root / "shadow"
    (shadow / "proj1680").mkdir(parents=True)
    (shadow / "proj1680" / "__init__.py").write_text(
        'import os\nos.environ["OXITEST_1680_SHADOW_INIT_RAN"] = "yes"\n',
        encoding="utf-8",
    )
    (shadow / "proj1680" / "test_mod.py").write_text("X = 2\n", encoding="utf-8")
    return shadow


def test_adopts_a_name_that_round_trips(tmp: TempDir, patch: Patcher) -> None:
    """A name that resolves back to the same file is adopted."""
    # Arrange
    proj = _tree(tmp / "case")
    target = proj / "proj1680" / "test_mod.py"
    patch.setattr(sys, "path", [*sys.path, str(proj)])

    # Act
    dotted = dotted_name_for(str(target), str(proj))

    # Assert
    assert dotted == "proj1680.test_mod", (
        "the rootdir-relative name resolves back to this file, so adopting it "
        "is truthful — declining here would leave relative imports broken"
    )


def test_declines_when_another_sys_path_entry_owns_the_name(
    tmp: TempDir, patch: Patcher
) -> None:
    """A name owned by an earlier sys.path entry is refused."""
    # Arrange — #1780 appends rootdir, so an installed distribution wins
    root = tmp / "case"
    proj = _tree(root)
    shadow = _shadow(root)
    target = proj / "proj1680" / "test_mod.py"
    patch.setattr(sys, "path", [str(shadow), *sys.path, str(proj)])

    # Act
    dotted = dotted_name_for(str(target), str(proj))

    # Assert
    assert dotted is None, (
        "'proj1680.test_mod' names the shadow copy on this sys.path — adopting it "
        "would give the module a name that resolves to a different file"
    )


def test_the_check_imports_nothing(tmp: TempDir, patch: Patcher) -> None:
    """Asking the question imports no module and runs no adopter code."""
    # Arrange — importlib.util.find_spec answers this question correctly but
    # imports the parent package to do it, executing adopter code
    root = tmp / "case"
    proj = _tree(root)
    shadow = _shadow(root)
    target = proj / "proj1680" / "test_mod.py"
    patch.setattr(sys, "path", [str(shadow), *sys.path, str(proj)])
    before_modules = set(sys.modules)
    before_marker = os.environ.get("OXITEST_1680_SHADOW_INIT_RAN")

    # Act
    dotted_name_for(str(target), str(proj))

    # Assert
    assert set(sys.modules) == before_modules, (
        f"the round-trip check must not import anything; it added "
        f"{sorted(set(sys.modules) - before_modules)}"
    )
    assert os.environ.get("OXITEST_1680_SHADOW_INIT_RAN") == before_marker, (
        "asking the question must not execute an adopter's tests/__init__.py — "
        "measured happening with find_spec, and against the wrong project"
    )


def test_declines_when_the_rootdir_is_not_importable(tmp: TempDir) -> None:
    """A rootdir absent from sys.path yields no dotted name."""
    # Arrange — rootdir deliberately absent from sys.path
    proj = _tree(tmp / "case")
    target = proj / "proj1680" / "test_mod.py"

    # Act
    dotted = dotted_name_for(str(target), str(proj))

    # Assert
    assert dotted is None, (
        "no sys.path entry provides 'proj1680', so the dotted name would name "
        "nothing at all"
    )


def test_declines_outside_the_rootdir(tmp: TempDir) -> None:
    """A file outside the rootdir yields no dotted name."""
    # Arrange
    root = tmp / "case"
    proj = _tree(root)
    outside = root / "elsewhere.py"
    outside.write_text("X = 3\n", encoding="utf-8")

    # Act
    dotted = dotted_name_for(str(outside), str(proj))

    # Assert
    assert dotted is None, (
        "a file outside the rootdir has no rootdir-relative name to compute"
    )


def test_declines_without_a_rootdir(tmp: TempDir) -> None:
    """A session with no rootdir yields no dotted name."""
    # Arrange
    proj = _tree(tmp / "case")
    target = proj / "proj1680" / "test_mod.py"

    # Act
    dotted = dotted_name_for(str(target), None)

    # Assert
    assert dotted is None, (
        "create_session accepts rootdir=None during oxitest's own bootstrap, "
        "so the helper must tolerate it rather than raise"
    )


def test_declines_a_path_segment_that_is_not_an_identifier(
    tmp: TempDir, patch: Patcher
) -> None:
    """A directory that cannot appear in a dotted name is refused."""
    # Arrange
    proj = tmp / "case" / "proj"
    (proj / "my-tests").mkdir(parents=True)
    target = proj / "my-tests" / "test_mod.py"
    target.write_text("X = 4\n", encoding="utf-8")
    patch.setattr(sys, "path", [*sys.path, str(proj)])

    # Act
    dotted = dotted_name_for(str(target), str(proj))

    # Assert
    assert dotted is None, (
        "'my-tests' cannot appear in a dotted name, so no truthful name exists"
    )


def test_declines_an_init_file(tmp: TempDir, patch: Patcher) -> None:
    """An __init__.py is refused, so the package is not duplicated."""
    # Arrange
    proj = _tree(tmp / "case")
    target = proj / "proj1680" / "__init__.py"
    patch.setattr(sys, "path", [*sys.path, str(proj)])

    # Act
    dotted = dotted_name_for(str(target), str(proj))

    # Assert
    assert dotted is None, (
        "naming it 'proj1680.__init__' would build a second copy of the "
        "package 'proj1680' — declining keeps today's synthetic name, which "
        "is safe"
    )


def test_the_owner_lookup_is_memoized_per_sys_path(
    tmp: TempDir, patch: Patcher
) -> None:
    """A repeat lookup on an unchanged sys.path reuses the recorded answer."""
    # Arrange
    proj = _tree(tmp / "case")
    target = proj / "proj1680" / "test_mod.py"
    patch.setattr(sys, "path", [*sys.path, str(proj)])
    patch.setattr(_module_identity, "_OWNER_CACHE", {})

    # Act
    dotted_name_for(str(target), str(proj))
    after_first = len(_module_identity._OWNER_CACHE)  # noqa: SLF001 — a memo has no public surface; its size is the behaviour under test
    dotted_name_for(str(target), str(proj))
    after_second = len(_module_identity._OWNER_CACHE)  # noqa: SLF001 — see above

    # Assert
    assert after_first == 1, (
        "the first lookup must record its answer, or every collected module "
        "pays the full sys.path scan"
    )
    assert after_second == 1, (
        "a repeat lookup on an unchanged sys.path must reuse the entry rather "
        "than adding a second one"
    )


def test_the_memo_does_not_outlive_a_sys_path_change(
    tmp: TempDir, patch: Patcher
) -> None:
    """Appending rootdir to sys.path changes the verdict, memo notwithstanding."""
    # Arrange — a worker calls ensure_rootdir_importable per task, so sys.path
    # grows mid-process and a name-only memo would answer with a stale one
    proj = _tree(tmp / "case")
    target = proj / "proj1680" / "test_mod.py"
    patch.setattr(_module_identity, "_OWNER_CACHE", {})

    # Act
    before = dotted_name_for(str(target), str(proj))
    patch.setattr(sys, "path", [*sys.path, str(proj)])
    after = dotted_name_for(str(target), str(proj))

    # Assert
    assert before is None, (
        "with rootdir off sys.path there is no owner, so no truthful name"
    )
    assert after == "proj1680.test_mod", (
        "appending rootdir makes the name resolvable — a memo keyed on the "
        "name alone would keep answering None for the whole run"
    )


def _rel_module(proj: Path) -> Path:
    """Add a module whose body performs a relative import, and return it."""
    rel = proj / "proj1680" / "rel.py"
    rel.write_text("from . import test_mod\n\nY = test_mod.X\n", encoding="utf-8")
    return rel


def test_a_spec_name_becomes_the_module_identity(tmp: TempDir) -> None:
    """The dotted name lands on the module; the sys.modules key stays synthetic."""
    # Arrange
    proj = _tree(tmp / "case")
    target = proj / "proj1680" / "test_mod.py"
    key = "_oxitest_collect_deadbeef1234"

    # Act
    module = _load_module(str(target), key, spec_name="proj1680.test_mod")

    # Assert
    try:
        assert module.__name__ == "proj1680.test_mod", (
            "frame introspection reads __name__ from the module globals, so "
            "this is what a caller-identity check such as loguru's "
            "logger.disable sees"
        )
        assert module.__package__ == "proj1680", (
            "relative imports resolve through __package__ — an empty one is "
            "what 'attempted relative import with no known parent package' "
            "reports"
        )
        assert sys.modules[key] is module, (
            "the synthetic key stays, because the executor pops it per test "
            "and the module cache is keyed on the path, not on either name"
        )
        assert sys.modules["proj1680.test_mod"] is module, (
            "the dotted name must be a live key, not only the module's "
            "__name__: dataclasses._is_type dereferences "
            "sys.modules.get(cls.__module__) with no guard, and "
            "typing.get_type_hints reads the same mapping to evaluate string "
            "annotations after the body has run"
        )
        assert sys.modules[key] is sys.modules["proj1680.test_mod"], (
            "two spellings of one module, not two imports — a second module "
            "object is the duplicate-registration defect #1962 fixed"
        )
    finally:
        sys.modules.pop(key, None)
        sys.modules.pop("proj1680.test_mod", None)


def test_a_dotted_registration_is_not_reused_as_canonical(tmp: TempDir) -> None:
    """already_imported skips a module this loader built, whatever its key.

    The skip used to rest on the ``_oxitest_`` key prefix. A module registered
    under its real dotted name has no such prefix, so without the origin marker
    it would be offered back as the canonical import — handing a caller the
    AST-rewritten copy and reversing #1962 and #2014.
    """
    # Arrange
    proj = _tree(tmp / "case")
    target = proj / "proj1680" / "test_mod.py"
    key = "_oxitest_collect_ab1e00000000"

    # Act
    _load_module(str(target), key, spec_name="proj1680.test_mod")

    # Assert
    try:
        assert "proj1680.test_mod" in sys.modules, (
            "the arrange step must actually register the unprefixed key, or "
            "this test passes without exercising the marker at all"
        )
        assert already_imported(str(target)) is None, (
            "a module oxitest built must never be returned as the canonical "
            "import for its own file"
        )
    finally:
        sys.modules.pop(key, None)
        sys.modules.pop("proj1680.test_mod", None)


def test_no_spec_name_keeps_todays_behaviour(tmp: TempDir) -> None:
    """Declining a dotted name leaves the module exactly as it was."""
    # Arrange
    proj = _tree(tmp / "case")
    target = proj / "proj1680" / "test_mod.py"
    key = "_oxitest_collect_f00d00000000"

    # Act
    module = _load_module(str(target), key)

    # Assert
    try:
        assert module.__name__ == key, (
            "declining must leave the module unchanged, not partially renamed"
        )
    finally:
        sys.modules.pop(key, None)


def test_a_spec_built_name_emits_no_mismatch_warning(
    tmp: TempDir, patch: Patcher
) -> None:
    """The spec-built name is silent where the attribute assignment warns.

    CPython compares ``__package__`` with ``__spec__.parent`` on every version
    oxitest ships wheels for — ``ImportWarning`` on 3.11, ``DeprecationWarning``
    on 3.12 through 3.14. The control half is mandatory: without it, a filtered
    or unreachable warning would make the treatment's silence prove nothing.
    """
    # Arrange
    proj = _tree(tmp / "case")
    rel = _rel_module(proj)
    patch.setattr(sys, "path", [*sys.path, str(proj)])
    control_key = "_oxitest_collect_c04180000000"
    treatment_key = "_oxitest_collect_c04180000001"

    # Act — control: assign the attribute against a synthetic spec
    with warnings.catch_warnings(record=True) as control:
        warnings.simplefilter("always")
        spec = importlib.util.spec_from_file_location(control_key, rel)
        assert spec is not None, "the scratch module must produce a spec"
        control_module = importlib.util.module_from_spec(spec)
        control_module.__package__ = "proj1680"
        sys.modules[control_key] = control_module
        try:
            exec(  # noqa: S102 — reproducing _load_module's construction exactly
                compile(rel.read_text(encoding="utf-8"), str(rel), "exec"),
                control_module.__dict__,
            )
        finally:
            sys.modules.pop(control_key, None)

    # Act — treatment: let the spec carry the name
    with warnings.catch_warnings(record=True) as treatment:
        warnings.simplefilter("always")
        try:
            _load_module(str(rel), treatment_key, spec_name="proj1680.rel")
        finally:
            sys.modules.pop(treatment_key, None)

    # Assert
    def mismatches(caught: list[warnings.WarningMessage]) -> list[str]:
        return [str(w.message) for w in caught if "__spec__.parent" in str(w.message)]

    assert mismatches(control), (
        f"the control must warn, or this test cannot fail and proves nothing "
        f"about the treatment; caught {[str(w.message) for w in control]}"
    )
    assert not mismatches(treatment), (
        f"a __package__/__spec__.parent mismatch would fire once per test "
        f"module on every supported Python; got {mismatches(treatment)}"
    )


def test_the_session_retains_the_rootdir(tmp: TempDir) -> None:
    """create_session keeps the rootdir instead of only consuming it."""
    # Arrange
    proj = _tree(tmp / "case")

    # Act
    session = create_session(rootdir=str(proj))

    # Assert
    assert session.rootdir == str(proj), (
        "both load routes derive the dotted name from the rootdir, and the "
        "session is the only object both of them hold"
    )


def test_a_session_without_a_rootdir_reports_none() -> None:
    """The bootstrap path passes no rootdir, and the session tolerates it."""
    # Arrange — oxitest's own runner bootstraps with create_session()

    # Act
    session = create_session()

    # Assert
    assert session.rootdir is None, (
        "the bootstrap path passes no rootdir, so the session must report "
        "None rather than raising"
    )


def _collectable(proj: Path) -> Path:
    """Turn the fixture module into one holding a test, and return it."""
    target = proj / "proj1680" / "test_mod.py"
    target.write_text(
        "def test_ok() -> None:\n    assert True, 'trivially true'\n",
        encoding="utf-8",
    )
    return target


def test_the_collect_route_gives_the_module_a_dotted_name(
    tmp: TempDir, patch: Patcher
) -> None:
    """Collection loads the module under its real dotted name."""
    # Arrange
    proj = _tree(tmp / "case")
    target = _collectable(proj)
    patch.setattr(sys, "path", [*sys.path, str(proj)])
    session = create_session(rootdir=str(proj))

    # Act
    collect_module(str(target), session)
    module = session.module_cache.get(str(target), kind="test")

    # Assert
    assert module is not None, "collect_module caches the module it loaded"
    assert module.__name__ == "proj1680.test_mod", (
        "collection is where a module-level relative import runs, so the "
        "identity has to be right before the body executes, not at run time"
    )


def test_both_load_routes_agree_on_the_identity(tmp: TempDir, patch: Patcher) -> None:
    """Collection and execution derive the same dotted name for one file."""
    # Arrange — the two routes build the same file under two synthetic keys
    proj = _tree(tmp / "case")
    target = _collectable(proj)
    patch.setattr(sys, "path", [*sys.path, str(proj)])
    session = create_session(rootdir=str(proj))
    exec_key = _exec_unique_name(str(target))

    # Act
    collect_module(str(target), session)
    collected = session.module_cache.get(str(target), kind="test")
    session.module_cache.evict(str(target))
    meta = helpers.make_meta(module_path=str(target), fn_name="test_ok")
    resolved = _load_and_resolve(meta, session, exec_key)

    # Assert
    try:
        expected = dotted_name_for(str(target), str(proj))
        assert collected is not None, "collection caches the module it loaded"
        assert isinstance(resolved, _ResolvedTest), (
            f"the module must load at execution time; got {resolved!r}"
        )
        assert collected.__name__ == expected, (
            "the collect route must use the shared helper, not a rule of its own"
        )
        assert resolved.module.__name__ == expected, (
            "a module reloaded at execution time must carry the identity it "
            "had at collection, or a relative import resolves differently "
            "between the two phases"
        )
    finally:
        sys.modules.pop(exec_key, None)


def test_evicting_the_cache_removes_the_dotted_registration(tmp: TempDir) -> None:
    """The module-state boundary covers the dotted name too (F1)."""
    # Arrange
    proj = _tree(tmp / "case")
    target = _collectable(proj)
    session = create_session(rootdir=str(proj))
    cache = session.module_cache

    # Act
    module = _load_module(
        str(target), "_oxitest_collect_ev1c700000", spec_name="proj1680.test_mod"
    )
    cache.set(str(target), module, kind="test")
    registered_before = sys.modules.get("proj1680.test_mod")
    cache.evict(str(target))

    # Assert
    try:
        assert registered_before is module, (
            "the arrange step must actually register the dotted name, or the "
            "eviction below has nothing to remove and this test is vacuous"
        )
        assert "proj1680.test_mod" not in sys.modules, (
            "end_module evicts the cache to stop one module group observing "
            "the previous group's module state; a surviving sys.modules entry "
            "hands that state to anything importing the module by name"
        )
    finally:
        sys.modules.pop("_oxitest_collect_ev1c700000", None)
        sys.modules.pop("proj1680.test_mod", None)


def test_eviction_leaves_a_name_another_module_has_claimed(tmp: TempDir) -> None:
    """Eviction removes only an entry still referring to this module (F1)."""
    # Arrange
    proj = _tree(tmp / "case")
    target = _collectable(proj)
    session = create_session(rootdir=str(proj))
    cache = session.module_cache
    module = _load_module(
        str(target), "_oxitest_collect_ev1c700001", spec_name="proj1680.test_mod"
    )
    cache.set(str(target), module, kind="test")
    usurper = ModuleType("proj1680.test_mod")
    sys.modules["proj1680.test_mod"] = usurper

    # Act
    cache.evict(str(target))

    # Assert
    try:
        assert sys.modules.get("proj1680.test_mod") is usurper, (
            "whoever replaced the entry owns it; deleting it would remove a "
            "module this cache never registered"
        )
    finally:
        sys.modules.pop("_oxitest_collect_ev1c700001", None)
        sys.modules.pop("proj1680.test_mod", None)


def test_a_failed_load_restores_a_displaced_module(tmp: TempDir) -> None:
    """A failing load must not delete a module the import system built (F2)."""
    # Arrange — a relative import registers a sibling under exactly this name
    proj = _tree(tmp / "case")
    broken = proj / "proj1680" / "broken.py"
    broken.write_text("raise RuntimeError('module body fails')\n", encoding="utf-8")
    incumbent = ModuleType("proj1680.broken")
    sys.modules["proj1680.broken"] = incumbent

    # Act
    raised = False
    try:
        _load_module(
            str(broken), "_oxitest_collect_b0000000000", spec_name="proj1680.broken"
        )
    except _LoadError:
        raised = True

    # Assert
    try:
        assert raised, (
            "the module body raises, so the load must fail — otherwise the "
            "restore path below never runs and this test proves nothing"
        )
        assert sys.modules.get("proj1680.broken") is incumbent, (
            "the failed load displaced a module it did not create, so it must "
            "put it back rather than pop the key"
        )
    finally:
        sys.modules.pop("_oxitest_collect_b0000000000", None)
        sys.modules.pop("proj1680.broken", None)


def test_a_failed_load_leaves_no_entry_when_it_displaced_nothing(
    tmp: TempDir,
) -> None:
    """A failing load still cleans up a key it introduced itself (F2)."""
    # Arrange
    proj = _tree(tmp / "case")
    broken = proj / "proj1680" / "broken2.py"
    broken.write_text("raise RuntimeError('module body fails')\n", encoding="utf-8")

    # Act
    raised = False
    try:
        _load_module(
            str(broken), "_oxitest_collect_b0000000001", spec_name="proj1680.broken2"
        )
    except _LoadError:
        raised = True

    # Assert
    try:
        assert raised, "the module body raises, so the load must fail"
        assert "proj1680.broken2" not in sys.modules, (
            "a half-executed module must not stay visible under any of its "
            "names, which is why the synthetic key is popped as well"
        )
    finally:
        sys.modules.pop("_oxitest_collect_b0000000001", None)
        sys.modules.pop("proj1680.broken2", None)
