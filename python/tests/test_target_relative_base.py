"""A relative Target resolves against the invocation directory (#2026).

Every test here runs oxitest with ``cwd`` set to a directory **below** the
rootdir and names its Target relatively. That combination had no coverage:
every other suite either runs from the project root, or passes an absolute
path. When the working directory is the rootdir the two candidate bases give
the same answer, which is why the defect stayed invisible.

Before the fix, from ``sub/``:

* ``test_self_contained.py`` produced an **empty** rootdir, so the project
  package was not importable;
* ``./test_self_contained.py`` resolved against the rootdir, collected
  nothing, and **exited 0**;
* ``./test_self_contained.py::test_self_contained`` was refused with
  ``no such test`` for a test that exists;
* two Targets ran everything or nothing depending on their **order**.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import oxitest as oxi
from oxitest import TempDir
from tests import helpers

_DATA_ROOT = Path(__file__).parent / "data"
_PROJECT = _DATA_ROOT / "target_relative_base"
_SUB = _PROJECT / "sub"

#: `sub/` holds two modules directly and one in `nested/`.
_TESTS_UNDER_SUB = 3


def _run_from_sub(*args: str) -> tuple[str, str, int]:
    """Run oxitest from `sub/`, which is below the rootdir."""
    return helpers.run_oxitest(None, *args, cwd=str(_SUB))


@dataclass(frozen=True)
class Spelling:
    """One spelling of a Target that names exactly one passing test."""

    argument: str


@oxi.parametrize(
    bare=Spelling("test_self_contained.py"),
    dot_slash=Spelling("./test_self_contained.py"),
    nested=Spelling("nested/test_nested.py"),
    dot_slash_nested=Spelling("./nested/test_nested.py"),
    parent_round_trip=Spelling("../sub/test_self_contained.py"),
    literal_node_id=Spelling("test_self_contained.py::test_self_contained"),
    dot_slash_node_id=Spelling("./test_self_contained.py::test_self_contained"),
    glob_node_id=Spelling("test_self_*.py::test_self_contained"),
)
def test_every_relative_spelling_runs_the_test_it_names(case: Spelling) -> None:
    """Every spelling of one file names one test, whatever the base."""
    # Act
    stdout, stderr, rc = _run_from_sub(case.argument)

    # Assert
    assert rc == 0, (
        f"{case.argument!r} names a test that exists and passes, so the run "
        f"must succeed; exit 4 means the Target resolved against the rootdir "
        f"and was then reported absent, and exit 3 means the rootdir never "
        f"reached sys.path — got {rc}\n{stdout}\n{stderr}"
    )
    assert "1 passed" in stdout, (
        f"{case.argument!r} names exactly one test, so exactly one must run; "
        f"'no tests ran' here is the false green #2026 removes, and the exit "
        f"code cannot show it\n{stdout}"
    )


def test_a_relative_target_reaches_the_project_package() -> None:
    """The rootdir on `sys.path` must be the project root, not the seed's parent."""
    # Act
    stdout, stderr, rc = _run_from_sub("test_below_root.py")

    # Assert
    assert rc == 0, (
        "the module imports `mypkg` from the project root, so a rootdir of "
        f"`sub/` or of the empty path makes this a collection error rather "
        f"than a pass — got {rc}\n{stdout}\n{stderr}"
    )
    assert "ModuleNotFoundError" not in stdout + stderr, (
        "a ModuleNotFoundError here means the rootdir placed on sys.path was "
        f"not the project root, which is the import half of #2026\n{stdout}\n{stderr}"
    )
    assert "1 passed" in stdout, (
        "the import assertion above cannot fire unless the test actually ran: "
        "a Target that collects nothing exits 0 and prints no traceback, which "
        f"satisfies both checks above while proving nothing\n{stdout}"
    )


def test_argument_order_does_not_decide_whether_tests_run() -> None:
    """`first_path` seeds the rootdir, so two Targets must not disagree by order."""
    # Act
    forward_out, _, forward_rc = _run_from_sub(
        "nested/test_nested.py", "test_self_contained.py"
    )
    reverse_out, _, reverse_rc = _run_from_sub(
        "test_self_contained.py", "nested/test_nested.py"
    )

    # Assert
    assert forward_rc == 0 and reverse_rc == 0, (
        "both orders name the same two existing tests, so neither may fail — "
        f"got {forward_rc} and {reverse_rc}"
    )
    assert "2 passed" in forward_out and "2 passed" in reverse_out, (
        "the rootdir is seeded from the first Target only, so before #2026 the "
        "subdirectory-first order collected nothing while the reverse order ran "
        f"both, and both exited 0 — forward:\n{forward_out}\nreverse:\n{reverse_out}"
    )


def test_no_argument_collects_the_whole_subtree_from_below_the_root() -> None:
    """The control: with no Target, the rootdir walk was already correct."""
    # Act
    stdout, _, rc = _run_from_sub()

    # Assert
    assert rc == 0, f"the project passes when run whole — got {rc}\n{stdout}"
    assert f"{_TESTS_UNDER_SUB} passed" in stdout, (
        "a bare run from `sub/` walks up to the rootdir and collects the whole "
        "project, so this asserts the fix did not narrow collection to the "
        f"invocation directory\n{stdout}"
    )


def test_an_absent_relative_target_is_still_refused() -> None:
    """#1797's refusal must survive the change of base."""
    # Act
    _, stderr, rc = _run_from_sub("./test_does_not_exist.py")

    # Assert
    assert rc == 4, (
        "resolving a Target against the invocation directory must not make an "
        "absent Target reachable; a Target that does not exist is a usage "
        f"error, not an empty run — got {rc}\n{stderr}"
    )
    assert "no such path" in stderr, (
        f"the refusal must still name the failure it found\n{stderr}"
    )


def test_a_relative_target_resolves_through_a_symlinked_working_directory(
    tmp: TempDir,
) -> None:
    """`current_dir` returns the physical path where the shell shows the logical one.

    The plan ledger's unvaried dimension. `std::env::current_dir` calls
    `getcwd`, which resolves symlinks, so a user standing in a symlinked
    directory has a different idea of "here" than oxitest does. The Target must
    still reach its test.
    """
    # Arrange
    link = Path(tmp / "link_to_sub")
    try:
        link.symlink_to(_SUB, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform guard
        oxi.skip(f"this platform cannot create a directory symlink: {exc}")

    # Act
    stdout, stderr, rc = helpers.run_oxitest(
        None, "./test_self_contained.py", cwd=str(link)
    )

    # Assert
    assert rc == 0, (
        "the invocation directory is a symlink, so `getcwd` reports the "
        "physical path while the argument was typed against the logical one; "
        f"the Target must resolve either way — got {rc}\n{stdout}\n{stderr}"
    )
    assert "1 passed" in stdout, (
        "the symlinked spelling must run the same single test as the direct "
        f"one\n{stdout}"
    )
