"""Unit tests for format_path — the display base for diagnostic paths."""

from __future__ import annotations

import os
from pathlib import Path

import oxitest
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._paths import format_path
from oxitest._bridge.fixture_lister import tree_fixtures_from_registry
from tests import helpers

#: A rootdir spelled by the platform, so every case below is portable.
ROOT = str(Path(os.sep, "proj"))


def test_path_under_rootdir_is_relative() -> None:
    """A path inside the project shows only the segments that differ."""
    absolute = str(Path(ROOT, "pkg", "api", "v1", "__fixtures__.py"))

    shown = format_path(absolute, ROOT)

    assert shown == str(Path("pkg", "api", "v1", "__fixtures__.py")), (
        "the rootdir prefix carries no information — leaving it in is what "
        f"buries the meaningful segments, got {shown!r}"
    )


def test_two_paths_under_rootdir_stay_parallel() -> None:
    """Both halves of a shadow notice keep the same shape, so they compare."""
    shower = str(Path(ROOT, "pkg", "api", "v1", "__fixtures__.py"))
    shadowed = str(Path(ROOT, "pkg", "admin", "v1", "__fixtures__.py"))

    left = format_path(shower, ROOT)
    right = format_path(shadowed, ROOT)

    assert left.count(os.sep) == right.count(os.sep), (
        "a reader compares the two halves of the notice against each other, "
        f"and different depths make that impossible, got {left!r} and {right!r}"
    )


def test_sentinel_is_returned_unchanged() -> None:
    """A sentinel names an origin with no file, so it is not relativised."""
    shown = format_path("<plugin:suite>", ROOT)

    assert shown == "<plugin:suite>", (
        "relativising a sentinel would turn it into a climb out of the "
        f"rootdir, and the notice would name no origin at all, got {shown!r}"
    )


def test_builtin_sentinel_is_returned_unchanged() -> None:
    """The builtin sentinel takes the same arm as the plugin one."""
    shown = format_path("<builtin>", ROOT)

    assert shown == "<builtin>", (
        f"'<builtin>' is an origin label and never a file, got {shown!r}"
    )


def test_no_rootdir_returns_the_path_unchanged() -> None:
    """A session with no project still prints something true."""
    absolute = str(Path(ROOT, "pkg", "api.py"))

    shown = format_path(absolute, None)

    assert shown == absolute, (
        "create_session accepts rootdir=None during oxitest's own bootstrap, "
        f"and a diagnostic raised there must still name a real file, got {shown!r}"
    )


def test_path_outside_rootdir_stays_absolute() -> None:
    """A climb out of the project is longer than what it replaces."""
    absolute = str(Path(os.sep, "venv", "site-packages", "suite", "fixtures.py"))

    shown = format_path(absolute, str(Path(ROOT, "pkg")))

    assert shown == absolute, (
        "an installed plugin measured 9 '..' levels from the rootdir, which is "
        f"longer than the absolute path and harder to read, got {shown!r}"
    )


def test_cross_drive_failure_returns_the_path_unchanged(
    patcher: oxitest.Patcher,
) -> None:
    """A Windows cross-drive pair raises, and the path survives it."""
    message = "path is on mount 'D:', start on mount 'C:'"

    def _raise_cross_drive(_path: str, _start: str) -> str:
        raise ValueError(message)

    absolute = str(Path(ROOT, "pkg", "api.py"))
    patcher.setattr(os.path, "relpath", _raise_cross_drive)

    shown = format_path(absolute, ROOT)

    assert shown == absolute, (
        "os.path.relpath raises across Windows drives, and this arm cannot be "
        "reached on a POSIX filesystem because one root gives every pair a "
        f"common ancestor — without it the diagnostic never renders, got {shown!r}"
    )


def _shadow_project(root: Path) -> None:
    """Write a project whose deeper declaration shadows a shallower one."""
    for part in ("pkg", "pkg/api", "pkg/api/v1"):
        (root / part).mkdir(parents=True, exist_ok=True)
        (root / part / "__init__.py").write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[tool.oxitest]\ntestpaths = ["pkg"]\n', encoding="utf-8"
    )
    declaration = (
        "import oxitest as oxi\n\n\n"
        '@oxi.fixture(lifetime="function")\n'
        "def thing() -> str:\n"
        '    return "{value}"\n'
    )
    (root / "pkg" / "api" / "__fixtures__.py").write_text(
        declaration.format(value="outer"), encoding="utf-8"
    )
    (root / "pkg" / "api" / "v1" / "__fixtures__.py").write_text(
        declaration.format(value="inner"), encoding="utf-8"
    )
    (root / "pkg" / "api" / "v1" / "test_thing.py").write_text(
        "from oxitest import Fixture\n\n\n"
        "def test_thing(thing: Fixture[str]) -> None:\n"
        '    assert thing == "inner", "the deeper declaration wins"\n',
        encoding="utf-8",
    )


def test_the_shadow_notice_names_no_absolute_path(tmp: oxitest.TempDir) -> None:
    """Every path in the notice is shown against the project rootdir."""
    root = Path(tmp)
    _shadow_project(root)

    stdout, _stderr, _code = helpers.run_oxitest(root, "--warnings")

    notice = next(line for line in stdout.splitlines() if "shadows definition" in line)
    assert str(root) not in notice, (
        "the notice names three paths and they share the rootdir prefix, so "
        f"printing it three times is what buries the segments that differ: {notice!r}"
    )


def test_the_notice_does_not_change_with_the_working_directory(
    tmp: oxitest.TempDir,
) -> None:
    """The rootdir is the base, so where the run starts cannot reshape it.

    This is the property that chose the rootdir over a base captured at start:
    both are stable across a test that moves the directory, and only the
    rootdir keeps the two halves of the notice the same shape when the run is
    invoked from inside a sub-package.
    """
    root = Path(tmp)
    _shadow_project(root)

    from_root, _stderr, _code = helpers.run_oxitest(root, "--warnings")
    from_deep, _stderr2, _code2 = helpers.run_oxitest(
        root, "--warnings", cwd=str(root / "pkg" / "api" / "v1")
    )

    def _notice(output: str) -> str:
        return next(
            line.strip() for line in output.splitlines() if "shadows definition" in line
        )

    assert _notice(from_root) == _notice(from_deep), (
        "a base that moves with the reader collapses one half of the notice to "
        "a bare filename and grows a '../..' climb on the other, which is the "
        "defect this issue reports, reintroduced by its own fix"
    )


def test_a_boundary_error_path_does_not_follow_a_chdir(
    tmp: oxitest.TempDir, patcher: oxitest.Patcher
) -> None:
    """A test that moves the working directory cannot reshape a diagnostic.

    ``os.chdir`` is process-global and ``_cwd_guard`` repairs only a *deleted*
    directory, never a moved one, so a base that reads the working directory
    is a base a test owns. This is the failing case that made the issue a bug.
    """
    root = Path(tmp)
    anchor = root / "pkg" / "api"
    anchor.mkdir(parents=True)
    module = root / "pkg" / "admin" / "test_admin.py"

    before = str(
        oxitest.BoundaryError(
            "conn", "api", str(anchor), str(module), rootdir=str(root)
        )
    )
    patcher.chdir(anchor)
    after = str(
        oxitest.BoundaryError(
            "conn", "api", str(anchor), str(module), rootdir=str(root)
        )
    )

    assert str(root) not in before, (
        "the anchor and the test share the rootdir prefix, so printing it "
        f"twice is what buries the segments that differ: {before!r}"
    )
    assert before == after, (
        "a test moved the working directory between the two calls, and nothing "
        "moved it back — a diagnostic that reads it reports a different file "
        f"for the same fixture: {before!r} then {after!r}"
    )


def test_the_run_announces_its_rootdir(tmp: oxitest.TempDir) -> None:
    """Every diagnostic path is shown against the root, so the root is named.

    Without this line a relative path in a diagnostic cannot be resolved by
    the reader, which is the one cost the rootdir base carries over a base
    that follows the working directory.
    """
    root = Path(tmp)
    _shadow_project(root)

    stdout, _stderr, _code = helpers.run_oxitest(root)

    # Compared by identity, not by spelling. Windows canonicalises to the
    # extended-length form, so the run announces
    # `\\?\C:\Users\...\test_x` where TempDir hands back `C:\Users\...\test_x`.
    # That prefix is how oxitest spells every path on Windows — its own failure
    # lines carry it too — so an exact-match assertion tested the spelling
    # rather than the claim, and it failed on a header that was correct.
    announced = next(
        (
            line.removeprefix("rootdir:").strip()
            for line in stdout.splitlines()
            if line.startswith("rootdir:")
        ),
        None,
    )
    assert announced is not None, (
        "a diagnostic prints paths relative to the rootdir, so a run that "
        "never names the rootdir gives the reader no way to resolve one; "
        f"got {stdout.splitlines()[:2]!r}"
    )
    assert Path(announced).samefile(root), (
        "the announced rootdir must be the directory the diagnostics are "
        f"relative to, or the reader resolves them against the wrong tree; "
        f"announced {announced!r}, ran against {str(root)!r}"
    )


def test_a_sentinel_survives_a_working_directory_inside_the_rootdir(
    tmp: oxitest.TempDir, patcher: oxitest.Patcher
) -> None:
    """The sentinel rule, in the one case the '..' rule cannot mask.

    ``os.path.relpath`` resolves a relative input against the working
    directory, so ``<plugin:suite>`` becomes ``<cwd>/<plugin:suite>``. When the
    working directory is *outside* the rootdir that lands on a ``..`` climb and
    the last rule returns the input unchanged — which is the right answer for
    the wrong reason, and it hides the sentinel rule completely. A mutant that
    deleted that rule survived the two sentinel tests above for exactly this
    reason.

    Inside the rootdir there is no climb, so ``<plugin:suite>`` would render as
    ``sub/<plugin:suite>``: a fixture origin turned into a path that names no
    file.
    """
    root = Path(tmp)
    inside = root / "sub"
    inside.mkdir()
    patcher.chdir(inside)

    shown = format_path("<plugin:suite>", str(root))

    assert shown == "<plugin:suite>", (
        "a sentinel names where a fixture came from when there is no file to "
        "name, and relativising it invents a path that does not exist; "
        f"got {shown!r}"
    )


def test_the_fixture_tree_shows_an_origin_against_the_rootdir() -> None:
    """The listing is a printer too, and it reads the base off the registry.

    Covers the wiring as well as the rendering: the renderer takes its base
    from ``registry.rootdir``, so a test that called ``_origin_header``
    directly would leave that one line unproven.
    """
    root = str(Path(os.sep, "proj"))
    declared_at = str(Path(root, "pkg", "api", "__fixtures__.py"))
    registry = FixtureRegistry(rootdir=root)
    registry.register(
        helpers.make_fixture_def("thing", lambda: "v", declaration_path=declared_at)
    )

    rendered = tree_fixtures_from_registry(registry, verbosity=2)

    assert str(Path("pkg", "api", "__fixtures__.py")) in rendered, (
        "the listing names where to go and edit a fixture, and the rootdir "
        f"prefix is identical on every row, so it carries nothing: {rendered!r}"
    )
    assert root not in rendered, (
        f"no row should still carry the absolute rootdir prefix: {rendered!r}"
    )
