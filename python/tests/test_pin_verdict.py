"""Tests for the CI-verdict pin in ``pin_verdict.py``.

The merge sequence named ``gh pr view --json headRefOid`` as the way to resolve
the commit a CI verdict is about. Measured on #2139, that instrument returns the
*previous* head immediately after a force-push, and so does the pulls API. Local
git is correct (#2137).

Classification is pure: it takes a required-context list and a check-runs
payload and returns a state per context, so every case is testable without
touching the network.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import oxitest as oxi

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "pin_verdict.py"


def _load_script_module() -> ModuleType:
    """Load ``scripts/pin_verdict.py`` as a module.

    The scripts directory is not a package, so this uses ``importlib.util``
    rather than a normal import. Registering in ``sys.modules`` before
    ``exec_module`` is load-bearing: the script defines a ``@dataclass`` and
    ``dataclasses._process_class`` resolves the defining module through
    ``sys.modules.get(cls.__module__).__dict__``.
    """
    scripts_dir = str(_SCRIPT_PATH.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    spec = importlib.util.spec_from_file_location(
        "pin_verdict_under_test", _SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_script = _load_script_module()

_REQUIRED = ["Quality (required)", "Tests (required)", "Docs (required)"]


def _run(name: str, status: str, conclusion: str | None = None) -> dict:
    return {"name": name, "status": status, "conclusion": conclusion}


# ── One context, one state ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StateCase:
    """One check run, and the state it must classify to."""

    runs: tuple[dict, ...]
    expected: str


@oxi.parametrize(
    absent_when_no_run_carries_the_name=StateCase(runs=(), expected="absent"),
    pending_when_queued=StateCase(
        runs=(_run("Quality (required)", "queued"),), expected="pending"
    ),
    pending_when_in_progress=StateCase(
        runs=(_run("Quality (required)", "in_progress"),), expected="pending"
    ),
    passing_on_success=StateCase(
        runs=(_run("Quality (required)", "completed", "success"),), expected="passing"
    ),
    passing_on_skipped=StateCase(
        runs=(_run("Quality (required)", "completed", "skipped"),), expected="passing"
    ),
    failing_on_failure=StateCase(
        runs=(_run("Quality (required)", "completed", "failure"),), expected="failing"
    ),
    failing_on_timed_out=StateCase(
        runs=(_run("Quality (required)", "completed", "timed_out"),), expected="failing"
    ),
    failing_on_cancelled=StateCase(
        runs=(_run("Quality (required)", "completed", "cancelled"),), expected="failing"
    ),
)
def test_a_required_context_resolves_to_one_state(case: StateCase) -> None:
    """Every check-run shape maps to exactly one of the four states."""
    verdicts = _script.classify(["Quality (required)"], list(case.runs))

    assert len(verdicts) == 1, f"expected one verdict, got {len(verdicts)}"
    assert verdicts[0].state == case.expected, (
        f"{case.runs} classified as {verdicts[0].state}, expected {case.expected}"
    )


def test_a_rerun_leaves_two_entries_and_the_last_one_wins() -> None:
    """A re-run appends rather than replacing, and the platform reports the last.

    E60 recorded a conclusion changing from ``failure`` to ``success`` under one
    identifier after a job re-run. Reading the first entry would report a verdict
    the platform no longer holds.
    """
    runs = [
        _run("Quality (required)", "completed", "failure"),
        _run("Quality (required)", "completed", "success"),
    ]

    verdicts = _script.classify(["Quality (required)"], runs)

    assert verdicts[0].state == "passing", (
        f"the later entry must win, got {verdicts[0].state}"
    )


# ── The failure mode the clause exists for ───────────────────────────────────


def test_an_absent_rollup_cannot_exit_zero_while_the_rest_are_green() -> None:
    """`Tests (required)` does not exist until the matrix finishes.

    This is the case that reads as success: every context an agent can see is
    green, and the one that has not appeared is the one that matters. Exiting
    ``0`` here is the defect.
    """
    runs = [
        _run("Quality (required)", "completed", "success"),
        _run("Docs (required)", "completed", "success"),
    ]

    verdicts = _script.classify(_REQUIRED, runs)
    status = _script.exit_code(verdicts)

    states = {verdict.context: verdict.state for verdict in verdicts}
    assert states["Tests (required)"] == "absent", (
        f"the missing rollup must be absent, got {states['Tests (required)']}"
    )
    assert status == _script.EXIT_CANNOT_ANSWER, (
        f"an absent required context must not exit 0, got {status}"
    )


# ── An absent branch is refused, never worked around ─────────────────────────


def test_an_absent_remote_branch_refuses_rather_than_falling_back() -> None:
    """After a merge with ``--delete-branch`` the ref is gone.

    Answering from a local ref or from the pulls API would reintroduce the
    staleness this script removes, and would do it silently. The plan's
    ``Not reached by`` row decided to refuse, so this pins the refusal.
    """

    def fake_gh(_args: list[str]) -> dict[str, object]:
        return {"headRefName": "feat/already-deleted"}

    def fake_run(_args: list[str]) -> tuple[int, str]:
        return 0, ""  # ls-remote succeeds and reports nothing

    sha, branch = _script.resolve_sha("2139", gh_json=fake_gh, run=fake_run)

    assert sha is None, f"an absent branch must not resolve to a SHA, got {sha}"
    assert branch == "feat/already-deleted", (
        f"the branch name must still be reported, got {branch!r}"
    )


def test_an_unknown_pull_request_refuses() -> None:
    """No branch is knowable, so git is never consulted."""

    def fake_gh(_args: list[str]) -> dict[str, object] | None:
        return None

    def fake_run(_args: list[str]) -> tuple[int, str]:
        msg = "git must not run when the pull request is unknown"
        raise AssertionError(msg)

    sha, branch = _script.resolve_sha("999999", gh_json=fake_gh, run=fake_run)

    assert sha is None, f"an unknown pull request must not resolve, got {sha}"
    assert branch == "", f"no branch name is knowable, got {branch!r}"


# ── Pagination: `--paginate` alone emits one document per page ───────────────


def test_check_runs_concatenates_every_page() -> None:
    """`--slurp` turns N pages into one JSON array, and all of it counts.

    Without it, `gh api --paginate` emits one JSON document per page and
    `json.loads` raises on the second — measured with ``per_page=5`` against 17
    runs. The failure direction is safe, because an unparsable payload reports
    every context ``absent``, but a required context on page two would be
    reported missing while it was green.
    """

    def fake_gh(args: list[str]) -> object:
        assert "--slurp" in args, "the payload must be slurped into one array"
        return [
            {"check_runs": [{"name": "Quality (required)", "status": "completed"}]},
            {"check_runs": [{"name": "Tests (required)", "status": "completed"}]},
        ]

    runs = _script.check_runs("deadbeef", gh_json=fake_gh)

    assert len(runs) == 2, f"both pages must contribute, got {len(runs)}"
    assert {run["name"] for run in runs} == {
        "Quality (required)",
        "Tests (required)",
    }, f"a context on page two must survive, got {runs}"


def test_check_runs_reports_nothing_when_the_payload_is_not_a_list() -> None:
    """An unparsable payload yields no runs, so every context reads absent."""

    def fake_gh(_args: list[str]) -> object:
        return None

    assert _script.check_runs("deadbeef", gh_json=fake_gh) == [], (
        "a failed read must not invent check runs"
    )


@dataclass(frozen=True, slots=True)
class ExitCase:
    """A set of states, and the status they must produce together."""

    states: tuple[str, ...]
    expected: int


@oxi.parametrize(
    all_passing_exits_zero=ExitCase(
        states=("passing", "passing", "passing"), expected=0
    ),
    any_failing_exits_one=ExitCase(
        states=("passing", "failing", "passing"), expected=1
    ),
    any_absent_exits_two=ExitCase(states=("passing", "absent", "passing"), expected=2),
    any_pending_exits_two=ExitCase(
        states=("passing", "pending", "passing"), expected=2
    ),
    failing_outranks_absent=ExitCase(states=("absent", "failing"), expected=1),
)
def test_an_incomplete_answer_never_exits_zero(case: ExitCase) -> None:
    """Failure outranks a gap, and a gap outranks success."""
    verdicts = [
        _script.Verdict(f"context {index}", state)
        for index, state in enumerate(case.states)
    ]

    status = _script.exit_code(verdicts)

    assert status == case.expected, (
        f"{case.states} produced {status}, expected {case.expected}"
    )
