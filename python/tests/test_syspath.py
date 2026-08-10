"""Unit tests for the rootdir sys.path append (#1780).

The ``_restore_sys_path`` fixture snapshots and restores ``sys.path`` around
each test. The append is a process-global mutation with no teardown by
design, so a test that leaked one would change import resolution for
everything that ran after it in the same worker.

Assertions compare against ``tmp.path.resolve()`` rather than bare
``tmp.path``: ``TempDir`` does not resolve its own path (it hands out
whatever ``tempfile.mkdtemp`` returns), so on a platform where the temp root
is itself a symlink (e.g. macOS ``/var`` -> ``/private/var``) the raw and
resolved spellings differ, while ``ensure_rootdir_importable`` always stores
the resolved form.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import oxitest
from oxitest import Patcher, TempDir, Yields, fixture
from oxitest._bridge._session_factory import create_session
from oxitest._bridge._syspath import ensure_rootdir_importable


@fixture(lifetime="function")
def _restore_sys_path() -> Yields[None]:
    """Snapshot sys.path before a test and restore it after, pass or fail."""
    before = list(sys.path)
    yield
    sys.path[:] = before


@oxitest.arrange("_restore_sys_path")
def test_appends_at_the_end_not_the_front(tmp: TempDir) -> None:
    """The rootdir must be appended, never inserted at the front (spec D2)."""
    ensure_rootdir_importable(str(tmp.path))
    assert Path(sys.path[-1]) == tmp.path.resolve(), (
        "the entry must be appended, not inserted — prepending would let a "
        "local directory shadow an installed distribution of the same name, "
        "silently changing which copy of the code under test is imported "
        "(spec D2)"
    )


@oxitest.arrange("_restore_sys_path")
def test_second_call_is_a_no_op(tmp: TempDir) -> None:
    """A repeat call for the same rootdir must not grow sys.path further."""
    ensure_rootdir_importable(str(tmp.path))
    after_first = list(sys.path)
    ensure_rootdir_importable(str(tmp.path))
    assert sys.path == after_first, (
        "the append must be idempotent — workers call create_session once "
        "per task, so a non-idempotent append would grow sys.path without "
        "bound over a long run"
    )


@oxitest.arrange("_restore_sys_path")
def test_trailing_slash_is_the_same_directory(tmp: TempDir) -> None:
    """A trailing slash must not be treated as a distinct rootdir spelling."""
    ensure_rootdir_importable(str(tmp.path))
    ensure_rootdir_importable(str(tmp.path) + "/")
    matches = [entry for entry in sys.path if Path(entry) == tmp.path.resolve()]
    assert len(matches) == 1, (
        "'/a/b' and '/a/b/' name one directory and must not both be "
        f"appended, got {matches}"
    )


@oxitest.arrange("_restore_sys_path")
def test_missing_directory_is_appended_anyway(tmp: TempDir) -> None:
    """A non-existent rootdir is still appended rather than rejected."""
    missing = str(tmp.path / "does-not-exist")
    ensure_rootdir_importable(missing)
    resolved_missing = Path(missing).resolve()
    assert any(Path(entry) == resolved_missing for entry in sys.path), (
        "a non-existent rootdir is appended rather than rejected — Python "
        "ignores unusable sys.path entries, and refusing here would mean "
        "inventing a diagnostic for a case find_rootdir makes unreachable"
    )


@oxitest.arrange("_restore_sys_path")
def test_a_non_str_sys_path_entry_does_not_abort_the_append(
    tmp: TempDir,
) -> None:
    """A non-str entry on sys.path must not stop the rootdir being appended.

    sys.path entries are conventionally str, but nothing enforces it. The
    previous implementation called ``Path(entry)`` on every entry, which
    raises TypeError on a bytes entry and would abort session creation from
    inside conftest loading. Membership uses ``==``, and ``str.__eq__``
    returns NotImplemented for bytes rather than raising, so the comparison
    is total over whatever sys.path holds (#1786).

    ``sys.path`` is typed ``list[str]``, so the bytes entry is widened
    through ``Any`` — the same escape this file already uses to call a
    keyword-only parameter positionally. The point of the test is precisely
    that runtime admits what the stub does not.
    """
    non_str_entry: Any = b"/non-str-entry"
    sys.path.insert(0, non_str_entry)

    ensure_rootdir_importable(str(tmp.path))

    assert str(tmp.path.resolve()) in sys.path, (
        "a non-str sys.path entry must not prevent the append — the entry "
        "side is never converted to a Path, so no entry type can raise"
    )


@oxitest.arrange("_restore_sys_path")
def test_symlinked_entry_yields_one_duplicate_and_no_more(
    tmp: TempDir,
) -> None:
    """A foreign symlinked spelling is not recognized — by design (#1786).

    The entry side is no longer resolved, so a spelling something else put
    on sys.path (PYTHONPATH, an installed .pth file) is not matched, and
    the canonical form is appended alongside it. That costs one duplicate.

    It must cost exactly one. Workers call this once per task, so an append
    that repeated would grow sys.path without bound over a long run — the
    only way the dropped dedup could become more than cosmetic.
    """
    real_dir = tmp.path.resolve() / "real"
    real_dir.mkdir()
    link_dir = tmp.path.resolve() / "link"
    link_dir.symlink_to(real_dir, target_is_directory=True)
    sys.path.append(str(link_dir))

    for _ in range(3):
        ensure_rootdir_importable(str(real_dir))

    matches = [entry for entry in sys.path if Path(entry).resolve() == real_dir]
    assert matches == [str(link_dir), str(real_dir)], (
        "a foreign symlinked spelling is not recognized, so the canonical "
        "form is appended once beside it — and only once, or a worker "
        f"calling this per task would grow sys.path without bound; got {matches}"
    )


@oxitest.arrange("_restore_sys_path")
def test_symlinked_argument_is_appended_in_its_resolved_form(
    tmp: TempDir,
) -> None:
    """Appending a symlinked argument must store the resolved target.

    Appending the raw argument would defeat the recognition this module
    promises for the *next* caller: a later call with the real path would
    fail to recognize this call's symlinked spelling as the same directory,
    since nothing here canonicalises what is already on sys.path.
    """
    real_dir = tmp.path.resolve() / "real"
    real_dir.mkdir()
    link_dir = tmp.path.resolve() / "link"
    link_dir.symlink_to(real_dir, target_is_directory=True)

    ensure_rootdir_importable(str(link_dir))

    assert sys.path[-1] == str(real_dir), (
        f"the appended entry must be the resolved target {str(real_dir)!r}, "
        f"not the raw symlinked argument — got {sys.path[-1]!r}"
    )


@oxitest.arrange("_restore_sys_path")
def test_create_session_without_rootdir_leaves_sys_path_alone(
    tmp: TempDir, patch: Patcher
) -> None:
    """The existing create_session() call sites must stay side-effect free.

    ``patch.chdir`` moves into a fresh directory first so cwd is not already
    on ``sys.path`` (it is, via an existing entry, when this test runs from
    the repo root under the test runner) — otherwise a mutant that appends
    ``rootdir or "."`` unconditionally would short-circuit to a no-op and
    this test would pass for the wrong reason.
    """
    patch.chdir(tmp.path)
    before = list(sys.path)
    create_session()
    assert sys.path == before, (
        "a leaked entry here changes import resolution for everything that "
        f"runs next in the same process; sys.path changed by "
        f"{set(sys.path) - set(before)}"
    )


@oxitest.arrange("_restore_sys_path")
def test_create_session_with_rootdir_appends(tmp: TempDir) -> None:
    """create_session(rootdir=...) is the seam all three entry points share."""
    create_session(rootdir=str(tmp.path))
    assert str(tmp.path.resolve()) in sys.path, (
        "create_session(rootdir=...) is the seam the serial path, the "
        "workers and the inspect TUI all go through — if it does not "
        "append, none of the three entry points work"
    )


def test_create_session_rootdir_is_keyword_only() -> None:
    """The rootdir parameter must stay keyword-only — a settled decision (#1780).

    Keyword-only keeps the existing create_session() call sites
    source-compatible and matches the #1305 convention already applied
    across the plugin protocols; a positional call must be rejected.
    """
    create_session_any: Any = create_session
    with oxitest.raises(TypeError, match="positional"):
        create_session_any("some/rootdir")
