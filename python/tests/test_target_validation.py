"""Target validation acceptance (#1797).

A **Target** is a path, a directory or a node ID given as a command-line
argument. A Target that names something absent is a usage error, not an empty
run.

These run oxitest as a subprocess because the behaviour under test is an exit
code, and only a real process has one. The refusal is written by Rust with
``eprint!`` rather than emitted as a ``Diagnostic``, so ``--warnings`` is not
needed to make the text assertions live.
"""

from __future__ import annotations

from dataclasses import dataclass

import oxitest as oxi
from oxitest import TempDir
from tests import helpers

#: ``ExitCode::UsageError`` (``src/types/exit.rs``). Pinned rather than
#: asserting ``rc != 0`` because 1 and 3 are also non-zero and mean different
#: things: 1 is a genuine test failure and 3 is a collection error. A regression
#: that turned an absent Target into either would change what CI reads about the
#: run without failing any assertion here.
_EXIT_USAGE_ERROR = 4

#: ``ExitCode::Success``.
_EXIT_SUCCESS = 0


def _project(tmp: TempDir) -> TempDir:
    """A project with a rootdir anchor and three tests across two files."""
    (tmp / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "0"\n')
    (tmp / "test_one.py").write_text(
        "def test_a():\n"
        "    assert True, 'the control must pass'\n"
        "\n\n"
        "def test_b():\n"
        "    assert True, 'the control must pass'\n"
    )
    (tmp / "test_two.py").write_text(
        "def test_c():\n    assert True, 'the control must pass'\n"
    )
    return tmp


def test_control_run_passes(tmp: TempDir) -> None:
    """A valid Target still exits 0.

    Without this the refusal assertions below could all be passing for the
    wrong reason.
    """
    project = _project(tmp)

    _stdout, _stderr, rc = helpers.run_oxitest(None, "test_one.py", cwd=str(project))

    assert rc == _EXIT_SUCCESS, (
        "a valid Target must still exit 0 — if the control fails, every refusal "
        "assertion in this file could be passing for an unrelated reason"
    )


def test_absent_path_target_refuses(tmp: TempDir) -> None:
    """AC1: an absent file Target exits 4 and runs nothing."""
    project = _project(tmp)

    stdout, stderr, rc = helpers.run_oxitest(None, "missing.py", cwd=str(project))

    assert rc == _EXIT_USAGE_ERROR, (
        "an absent Target must exit 4 — exiting 0 is #1797, where a typo in a CI "
        "invocation is indistinguishable from a successful run"
    )
    assert "missing.py" in stdout + stderr, (
        "the refusal must name the Target the user typed, or they cannot tell "
        "which of several arguments was wrong"
    )
    assert "passed" not in stdout, (
        "no test may run when a Target is refused — a partial run is the failure "
        "mode this issue exists to remove"
    )


def test_absent_directory_target_refuses(tmp: TempDir) -> None:
    """AC1: an absent directory Target exits 4."""
    project = _project(tmp)

    _stdout, _stderr, rc = helpers.run_oxitest(None, "no/such/dir/", cwd=str(project))

    assert rc == _EXIT_USAGE_ERROR, (
        "an absent directory must be refused like an absent file — the directory "
        "walker produces an error for it and the pre-#1797 code discarded that "
        "error with `filter_map(|e| e.ok())`"
    )


def test_one_absent_target_refuses_the_whole_run(tmp: TempDir) -> None:
    """AC4: a bad Target among valid ones refuses everything.

    This project has a rootdir anchor, so the assertion does not depend on the
    anchor-less variant that AC5 covers.
    """
    project = _project(tmp)

    stdout, _stderr, rc = helpers.run_oxitest(
        None, "test_one.py", "missing.py", cwd=str(project)
    )

    assert rc == _EXIT_USAGE_ERROR, (
        "one absent Target must refuse the whole run — running the valid subset "
        "and exiting 0 means CI passes having tested less than it was told to"
    )
    assert "2 passed" not in stdout, (
        "the valid Target's tests must not run — before #1797 this printed "
        "'2 passed' and exited 0"
    )


def test_absent_target_first_does_not_silently_discard_the_rest(tmp: TempDir) -> None:
    """AC5: regression for the anchor-less variant.

    With no rootdir anchor, an absent Target in first position relocated the
    rootdir, so every other Target collected nothing and the run exited 0 after
    running no tests. Refusing before ``find_rootdir`` makes that unreachable.
    """
    (tmp / "test_one.py").write_text(
        "def test_a():\n    assert True, 'must not be silently skipped'\n"
    )
    (tmp / "test_two.py").write_text(
        "def test_c():\n    assert True, 'must not be silently skipped'\n"
    )
    # Deliberately no pyproject.toml, setup.cfg or tox.ini: the defect needs an
    # unanchored rootdir, and creating one here would hide it.

    stdout, _stderr, rc = helpers.run_oxitest(
        None, "missing.py", "test_one.py", "test_two.py", cwd=str(tmp)
    )

    assert rc == _EXIT_USAGE_ERROR, (
        "an absent Target in first position must be refused — it previously "
        "exited 0 after running none of the three valid tests, which is the worst "
        "shape of #1797 because CI reads it as a pass"
    )
    assert "no tests ran" not in stdout, (
        "'no tests ran' with exit 0 is exactly the silent discard being fixed"
    )


def test_two_absent_targets_are_both_reported(tmp: TempDir) -> None:
    """AC6: every bad Target is reported, not only the first."""
    project = _project(tmp)

    stdout, stderr, rc = helpers.run_oxitest(
        None, "missing_a.py", "missing_b.py", cwd=str(project)
    )
    blob = stdout + stderr

    assert rc == _EXIT_USAGE_ERROR, (
        "two absent Targets must still exit 4 — the count of mistakes does not "
        "change their class"
    )
    assert "missing_a.py" in blob and "missing_b.py" in blob, (
        "both absent Targets must be named — reporting only the first makes the "
        "user re-run once per typo"
    )


@dataclass(frozen=True)
class Subcommand:
    """One subcommand that accepts path Targets, as a parametrize case."""

    name: str


@oxi.parametrize(
    run=Subcommand(name=""),
    debug=Subcommand(name="debug"),
    query=Subcommand(name="query"),
)
def test_every_subcommand_refuses_an_absent_target(
    case: Subcommand, tmp: TempDir
) -> None:
    """AC8: validation lives where paths resolve, so every subcommand inherits it."""
    project = _project(tmp)

    args = ["missing.py"] if not case.name else [case.name, "missing.py"]
    _stdout, _stderr, rc = helpers.run_oxitest(None, *args, cwd=str(project))

    label = case.name or "run"
    assert rc == _EXIT_USAGE_ERROR, (
        f"'{label}' must refuse an absent Target — before #1797 both "
        "'query missing.py' and 'debug missing.py' exited 0, so a mistyped path "
        "made a query look empty rather than wrong"
    )


@dataclass(frozen=True)
class ValidEmptyTarget:
    """A Target that exists and legitimately holds no tests."""

    label: str
    args: tuple[str, ...]


@oxi.parametrize(
    non_test_file=ValidEmptyTarget(label="a non-test file", args=("README.md",)),
    empty_dir=ValidEmptyTarget(label="an empty directory", args=("empty_dir",)),
    all_deselected=ValidEmptyTarget(
        label="everything deselected", args=("test_one.py", "-E", "name(zzz)")
    ),
)
def test_valid_target_holding_no_tests_still_exits_zero(
    case: ValidEmptyTarget, tmp: TempDir
) -> None:
    """AC9: an explicit non-goal, pinned so a later change cannot fold it in."""
    project = _project(tmp)
    (project / "README.md").write_text("not a test file\n")
    (project / "empty_dir").mkdir()

    _stdout, _stderr, rc = helpers.run_oxitest(None, *case.args, cwd=str(project))

    assert rc == _EXIT_SUCCESS, (
        f"{case.label} must stay at exit 0 — the Target is valid, and exit 0 is "
        "documented as covering a run that collected nothing. Refusing it would "
        "need a decision between 'holds no tests today' and 'can never hold "
        "tests', which depends on the configurable python_files glob"
    )


def test_valid_literal_node_id_target_passes(tmp: TempDir) -> None:
    """Control for the node-ID half. Without it AC2 could pass for any reason."""
    project = _project(tmp)

    _stdout, _stderr, rc = helpers.run_oxitest(
        None, "test_one.py::test_a", cwd=str(project)
    )

    assert rc == _EXIT_SUCCESS, (
        "a literal node ID that names a real test must still exit 0 — a false "
        "refusal here is worse than the defect being fixed"
    )


def test_absent_literal_node_id_refuses(tmp: TempDir) -> None:
    """AC2: a literal node ID matching no test exits 4 and runs nothing."""
    project = _project(tmp)

    stdout, stderr, rc = helpers.run_oxitest(
        None, "test_one.py::test_zzz", cwd=str(project)
    )
    blob = stdout + stderr

    assert rc == _EXIT_USAGE_ERROR, (
        "a literal node ID that matches nothing must exit 4 — exiting 0 means a "
        "renamed test still passes CI while running nothing"
    )
    assert "test_zzz" in blob, "the refusal must name the Target the user typed"


def test_absent_node_id_refusal_is_spelled_relative_to_rootdir(tmp: TempDir) -> None:
    """The two halves of Target validation must spell a Target the same way.

    Node IDs are absolutised upstream, so the refusal has to relativise them
    again or it shows a path the user never typed while the path half echoes the
    argument verbatim.
    """
    project = _project(tmp)

    stdout, stderr, rc = helpers.run_oxitest(
        None, "test_one.py::test_zzz", cwd=str(project)
    )
    blob = stdout + stderr

    assert rc == _EXIT_USAGE_ERROR, "the absent node ID must still be refused"
    assert "test_one.py::test_zzz" in blob, (
        "the refusal must spell the Target the way the user typed it — this is "
        "the positive half, and without it the negative assertions below can "
        "pass on a message that names no Target at all"
    )
    assert "\\\\?\\" not in blob, (
        "the refusal must not contain a Windows extended-length prefix — "
        "canonicalize_node_ids runs std::fs::canonicalize, which returns the "
        "\\\\?\\ form on Windows, and leaking it shows a spelling no user typed. "
        "Asserted on the prefix rather than on the project path because a "
        "Windows temp directory can be an 8.3 short name, which would make a "
        "path-substring assertion pass without the message being correct"
    )
    assert str(project) not in blob, (
        "the refusal must not contain the absolute project path — node IDs are "
        "absolutised internally, and echoing that spelling back disagrees with "
        "the path half of this same feature"
    )


def test_zero_match_glob_node_id_still_exits_zero(tmp: TempDir) -> None:
    """AC3: a glob asks to match what is present, so zero matches is legitimate."""
    project = _project(tmp)

    _stdout, _stderr, rc = helpers.run_oxitest(
        None, "test_one.py::test_z*", cwd=str(project)
    )

    assert rc == _EXIT_SUCCESS, (
        "a glob that matches nothing must stay at exit 0 — refusing it would "
        "break every script that globs defensively, and only a literal Target "
        "asserts existence"
    )


def test_last_failed_with_no_prior_failures_still_exits_zero(tmp: TempDir) -> None:
    """AC10: a filter that legitimately narrows to nothing is not a bad Target."""
    project = _project(tmp)

    _stdout, _stderr, rc = helpers.run_oxitest(
        None, "test_one.py", "--lf", cwd=str(project)
    )

    assert rc == _EXIT_SUCCESS, (
        "--lf narrows a list that collection already produced and never supplies "
        "a Target, so Target validation must not reach it"
    )
