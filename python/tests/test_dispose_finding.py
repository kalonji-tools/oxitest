"""Tests for the disposition recorder in ``dispose_finding.py``.

Resolution is split: the agent may resolve only ``Fixed``, because only
``Fixed`` is verifiable from the diff. Every other verb is a judgement call and
the Resolve button stays the maintainer's (#2007).

The load-bearing test is ``test_only_fixed_is_agent_resolvable``. The guard it
covers is what turns split resolution from a rule an agent is asked to follow
into one the tooling enforces. A mutant that removes it must fail loudly —
otherwise the rule is decorative, and an agent can close its own judgement calls
while every gate still reports green.
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
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "dispose_finding.py"


def _load_script_module() -> ModuleType:
    """Load ``scripts/dispose_finding.py`` as a module."""
    spec = importlib.util.spec_from_file_location(
        "dispose_finding_under_test", _SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _thread(thread_id: str, body: str) -> dict:
    """One reviewThreads node carrying a marker in its first comment."""
    return {
        "id": thread_id,
        "isResolved": False,
        "comments": {"nodes": [{"databaseId": 1, "body": body}]},
    }


# ── The split-resolution guard ───────────────────────────────────────────────


def test_only_fixed_is_agent_resolvable() -> None:
    """Split resolution, enforced by the tool rather than by discipline."""
    # Arrange
    module = _load_script_module()

    # Act / Assert
    assert module.agent_may_resolve("Fixed") is True, (
        "a fix is verifiable from the diff, so the agent closing it costs the "
        "maintainer nothing and keeps the queue to judgement calls only"
    )
    for verb in ("Refuted", "Superseded", "Accepted", "Deferred", "No change"):
        assert module.agent_may_resolve(verb) is False, (
            f"{verb!r} is a judgement about whether a finding should be acted on; "
            f"letting the agent resolve it means the same actor raises, answers "
            f"and closes the finding, which is the review this change replaces"
        )


def test_the_reply_leads_with_the_verb() -> None:
    """The verb is greppable, because the resolve bit cannot carry it."""
    # Arrange
    module = _load_script_module()

    # Act
    reply = module.reply_body("Refuted", "mirrors the existing rebind in this method")

    # Assert
    assert reply.startswith("**Refuted** — "), (
        "GitHub's resolve bit is binary, so the verb only exists in this text; "
        "leading with it is what lets a reader tell a refusal from a fix without "
        "reading the whole reason"
    )
    assert "mirrors the existing rebind in this method" in reply, (
        "the reason is the part a future reader needs — a disposition without one "
        "records that someone clicked, not why"
    )


# ── Thread lookup ────────────────────────────────────────────────────────────


def test_the_thread_is_found_by_its_namespaced_marker() -> None:
    """Two passes each emit a ``#1``; the slug is what tells them apart."""
    # Arrange
    module = _load_script_module()
    nodes = [
        _thread("T1", "**ponytail #1** — a shrink"),
        _thread("T2", "**improve #1** — a correctness finding"),
    ]

    # Act / Assert
    assert module.find_thread(nodes, "improve", 1)["id"] == "T2", (
        "matching on the number alone would return ponytail's thread, so the "
        "disposition would land on a different finding than the one intended"
    )
    assert module.find_thread(nodes, "ponytail", 1)["id"] == "T1", (
        "the same collision in the other direction — both must resolve to their "
        "own pass's thread or the split is meaningless"
    )


def test_a_missing_marker_returns_nothing() -> None:
    """A typo must fail loudly rather than disposing of some other finding."""
    # Arrange
    module = _load_script_module()
    nodes = [_thread("T1", "**ponytail #1** — a shrink")]

    # Act / Assert
    assert module.find_thread(nodes, "improve", 9) is None, (
        "returning None lets the caller exit non-zero and say which marker missed; "
        "falling back to any thread would silently close an unrelated finding"
    )


def test_a_thread_with_no_comments_is_skipped_not_crashed_on() -> None:
    """Defensive: an empty comment list must not raise mid-disposition."""
    # Arrange
    module = _load_script_module()
    nodes = [{"id": "T0", "isResolved": False, "comments": {"nodes": []}}]

    # Act / Assert
    assert module.find_thread(nodes, "improve", 1) is None, (
        "a thread with no comments carries no marker; indexing into an empty list "
        "would abort the run after earlier dispositions had already posted"
    )


# ── Whole-script behaviour ───────────────────────────────────────────────────


def test_the_script_rejects_an_unknown_verb_at_the_command_line() -> None:
    """An unrecognised verb is refused before any network call is made."""
    # Arrange / Act
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "improve", "1", "Done", "a reason"],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    # Assert
    assert result.returncode != 0, (
        "an unrecognised verb must stop the run before it posts anything; "
        "accepting it would write a reply nothing downstream can interpret"
    )
    assert "Done" in result.stderr or "choice" in result.stderr.lower(), (
        f"the error has to name what was wrong so the author can pick a real verb, "
        f"got {result.stderr!r}"
    )


def test_the_script_is_executable_and_documents_itself() -> None:
    """Subprocess run of the real script — proves it parses and its args work."""
    # Arrange / Act
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    # Assert
    assert result.returncode == 0, (
        f"the justfile invokes this as a standalone command; --help exited "
        f"{result.returncode} with {result.stderr!r}"
    )
    assert "fixed" in result.stdout.lower(), (
        "the help text names the verbs, which is the only place someone running "
        "this from stage 8 will learn which ones are agent-resolvable"
    )
