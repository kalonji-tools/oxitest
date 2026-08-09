"""Tests for the unresolved-thread gate in ``check_review_threads.py``.

Stage 8 posts every review finding as a GitHub review thread; stage 9 refuses to
merge while any of them is unresolved. Classification is pure — it takes a
decoded GraphQL payload and returns what the report needs — so every interesting
case is testable without touching the network (#2007).

The case that matters most is a thread whose code has since been fixed. GitHub
marks it ``isOutdated`` and sets ``originalLine`` to null, but leaves
``isResolved`` false. Fixing code must not dispose of a finding, and a filter
that dropped outdated threads would silently defeat the whole gate.

The second is a file-level thread. GitHub reports ``originalLine: 1`` for those
rather than null, so a ``line or "file"`` fallback is a branch that can never
fire — it renders every file-level thread as ``path:1``, which reads as a real
line number and is wrong. That defect was found by running the prototype against
a live pull request, not by reading it.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

# ── Script location ──────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_review_threads.py"


def _load_script_module() -> ModuleType:
    """Load ``scripts/check_review_threads.py`` as a module.

    The scripts directory is not a package, so this uses ``importlib.util``
    rather than a normal import. The ``sys.modules`` registration is
    load-bearing: the script defines a ``@dataclass``, and
    ``dataclasses._process_class`` resolves the defining module through
    ``sys.modules.get(cls.__module__).__dict__``. Executing the module without
    registering it first makes that ``None`` and the decorator dies with
    ``AttributeError: 'NoneType' object has no attribute '__dict__'``.
    """
    spec = importlib.util.spec_from_file_location(
        "check_review_threads_under_test", _SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ── Payload builders ─────────────────────────────────────────────────────────


def _payload(nodes: list[dict], *, has_next_page: bool = False) -> dict:
    """Wrap nodes in the shape the GraphQL query returns."""
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": has_next_page},
                        "nodes": nodes,
                    }
                }
            }
        }
    }


def _node(**overrides: object) -> dict:
    """One ``reviewThreads`` node, defaulting to an unresolved inline finding.

    Overrides use GitHub's own field names so a test reads as the payload it is
    standing in for.
    """
    node: dict = {
        "isResolved": False,
        "isOutdated": False,
        "path": "python/oxitest/_x.py",
        "originalLine": 17,
        "subjectType": "LINE",
        "comments": {"nodes": [{"body": "**improve #1** — a finding\n\ndetail"}]},
    }
    node.update(overrides)
    return node


# ── Classification ───────────────────────────────────────────────────────────


def test_a_resolved_thread_is_not_reported() -> None:
    """A disposition has been recorded, so the finding no longer blocks."""
    # Arrange
    module = _load_script_module()
    payload = _payload([_node(isResolved=True)])

    # Act
    unresolved, truncated = module.parse_threads(payload)

    # Assert
    assert unresolved == [], (
        "a resolved thread carries a disposition, so it must not block the merge — "
        "reporting it would make the gate unsatisfiable"
    )
    assert truncated is False, (
        "a single-page response is complete, so the gate can stand behind its count"
    )


def test_an_unresolved_thread_is_reported_with_its_location() -> None:
    """The report has to say which finding, and where in the code it sits."""
    # Arrange
    module = _load_script_module()
    payload = _payload([_node()])

    # Act
    unresolved, _ = module.parse_threads(payload)

    # Assert
    assert len(unresolved) == 1, (
        "an unresolved thread is an undispositioned finding and must reach the report"
    )
    assert unresolved[0].location() == "python/oxitest/_x.py:17", (
        "the report exists so a reader can go straight to the code; a location that "
        "does not name file and line defeats the reason for moving review onto GitHub"
    )
    assert unresolved[0].first_line == "**improve #1** — a finding", (
        "the marker line identifies which pass and which finding, which is how the "
        "maintainer knows what they are being asked to dispose of"
    )


def test_an_outdated_thread_still_counts_as_unresolved() -> None:
    """The load-bearing case: fixing the code must not dispose of the finding."""
    # Arrange
    module = _load_script_module()
    payload = _payload([_node(isOutdated=True, originalLine=None)])

    # Act
    unresolved, _ = module.parse_threads(payload)

    # Assert
    assert len(unresolved) == 1, (
        "GitHub marks a thread outdated when its line changes but leaves it "
        "unresolved; treating outdated as disposed would let every fixed-but-"
        "unanswered finding through and defeat the gate entirely"
    )
    assert unresolved[0].is_outdated is True, (
        "the report flags outdated threads so a reader knows the code moved under "
        "the finding and the disposition may need rechecking"
    )


def test_a_file_level_thread_reports_as_file_level_not_line_one() -> None:
    """``originalLine`` is 1 for file-level threads, so it cannot discriminate."""
    # Arrange
    module = _load_script_module()
    payload = _payload([_node(subjectType="FILE", originalLine=1)])

    # Act
    unresolved, _ = module.parse_threads(payload)

    # Assert
    assert unresolved[0].location() == "python/oxitest/_x.py (file-level)", (
        "GitHub returns originalLine=1 for file-level threads rather than null, so "
        "subjectType is the only reliable discriminator; keying off the line value "
        "renders every file-level finding as ':1', a line number that is not real"
    )


def test_a_truncated_page_is_reported_as_truncated() -> None:
    """Beyond one page the gate cannot see every thread, so it must refuse."""
    # Arrange
    module = _load_script_module()
    payload = _payload([], has_next_page=True)

    # Act
    _, truncated = module.parse_threads(payload)

    # Assert
    assert truncated is True, (
        "beyond one page the gate cannot see every thread; it must refuse rather "
        "than report a count it cannot stand behind"
    )


# ── Reporting ────────────────────────────────────────────────────────────────


def test_the_clean_report_states_the_total_it_checked() -> None:
    """A pass must be distinguishable from an empty query result."""
    # Arrange
    module = _load_script_module()

    # Act
    report = module.format_report([], total=3)

    # Assert
    assert "0 unresolved" in report, (
        "a passing gate states what it checked, so a reader can tell 'all resolved' "
        "apart from 'no threads found because the query was wrong'"
    )
    assert "3 thread(s)" in report, (
        "the total is what distinguishes a real pass from an empty query result"
    )


def test_the_report_names_every_unresolved_thread() -> None:
    """A blocked merge names everything blocking it, not just the first."""
    # Arrange
    module = _load_script_module()
    payload = _payload([_node(), _node(path="a.py", originalLine=4)])

    # Act
    unresolved, _ = module.parse_threads(payload)
    report = module.format_report(unresolved, total=5)

    # Assert
    assert "2 of 5" in report, (
        "the ratio tells the reader how much of the review is outstanding"
    )
    assert "python/oxitest/_x.py:17" in report, (
        "a blocked merge must name what is blocking it or the maintainer has to go "
        "hunting through the PR to find out"
    )
    assert "a.py:4" in report, (
        "every unresolved thread is named, not just the first — a report that stops "
        "early looks identical to one with fewer findings"
    )


# ── Exit status ──────────────────────────────────────────────────────────────


def test_a_clean_pull_request_exits_zero() -> None:
    """Nothing outstanding, so the merge sequence continues."""
    # Arrange
    module = _load_script_module()

    # Act
    status = module.exit_code([], truncated=False)

    # Assert
    assert status == module.EXIT_OK, (
        "every finding carries a disposition, so the gate must let the merge "
        "proceed — a gate that cannot pass would just be routed around"
    )


def test_an_unresolved_thread_exits_one() -> None:
    """An undispositioned finding blocks the merge."""
    # Arrange
    module = _load_script_module()
    unresolved, _ = module.parse_threads(_payload([_node()]))

    # Act
    status = module.exit_code(unresolved, truncated=False)

    # Assert
    assert status == module.EXIT_UNRESOLVED, (
        "blocking on undispositioned findings is the entire purpose of the gate; "
        "any other exit lets the merge sequence continue past them"
    )


def test_truncation_outranks_a_clean_page() -> None:
    """The case that would otherwise read as a pass.

    Beyond one page the gate cannot see every thread. If the threads it *can*
    see are all resolved, a naive implementation reports success — a false green
    that is strictly worse than having no gate, because it carries authority.
    """
    # Arrange
    module = _load_script_module()

    # Act
    status = module.exit_code([], truncated=True)

    # Assert
    assert status == module.EXIT_CANNOT_ANSWER, (
        "a truncated query with no visible unresolved threads must refuse, not "
        "pass: the gate has no basis for a verdict and reporting one anyway is "
        "how a merge proceeds past findings nobody ever saw"
    )


# ── Whole-script behaviour ───────────────────────────────────────────────────


def run_script(
    *args: str, ambient: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the script and decode its output as UTF-8, whatever the locale says.

    ``PYTHONIOENCODING`` is forced rather than inherited, and that is the whole
    point of this helper. The script's docstring contains U+2014. A child
    process on Windows encodes it to cp1252 byte ``0x97``; the parent then fails
    to decode that as UTF-8, the reader thread dies, and ``result.stdout``
    becomes ``None`` — so every assertion downstream fails with
    ``AttributeError: 'NoneType' object has no attribute 'lower'``, far from the
    cause.

    Measured on the Windows job of PR #2008 and reproduced on Linux by setting
    ``PYTHONIOENCODING=cp1252``. Forcing the child's encoding is the same lever
    the #2004 work uses; these scripts are not shipped entry points, so they do
    not reconfigure their own streams.
    """
    env = {
        **(ambient if ambient is not None else os.environ),
        "PYTHONIOENCODING": "utf-8",
    }
    return subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        env=env,
    )


def test_the_help_output_decodes_under_a_hostile_ambient_encoding() -> None:
    """Reproduces the Windows CI failure on any platform.

    An ambient ``PYTHONIOENCODING=cp1252`` is what the Windows runner supplies
    in effect. Without the forced override, this call raises
    ``UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97``.
    """
    # Arrange
    hostile = {**os.environ, "PYTHONIOENCODING": "cp1252"}

    # Act
    result = run_script("--help", ambient=hostile)

    # Assert
    assert result.stdout is not None, (
        "a failed decode kills the reader thread and leaves stdout as None; every "
        "later assertion then fails with AttributeError far from the real cause"
    )
    assert "—" in result.stdout, (
        "the docstring's em dash must survive the round trip; if it does not, the "
        "child wrote a codec the parent does not read and the test is measuring "
        "the harness rather than the script"
    )


def test_the_script_is_executable_and_documents_itself() -> None:
    """Subprocess run of the real script — proves it parses and its args work."""
    # Arrange / Act
    result = run_script("--help")

    # Assert
    assert result.returncode == 0, (
        f"the script must be runnable as a standalone command because the justfile "
        f"invokes it that way; --help exited {result.returncode} with "
        f"{result.stderr!r}"
    )
    assert "unresolved" in result.stdout.lower(), (
        "the help text names what the gate checks, which is the only documentation "
        "someone running it from the merge sequence will see"
    )
