"""ADR-0012 gate: every built-in declares whether it has a block-scoped form.

ADR-0012 says an object with a lifetime boundary exposes any narrower,
block-scoped form as a method or classmethod on itself — never as a second
concept. Two built-ins already do (``StdCapture.disabled``,
``LogCapture.at_level``) and one is missing its form (``Patcher``, #1696).

**This file is not testing behaviour. It is making a design decision
unskippable.** Nothing here would catch a badly-written ``disabled()``; what it
catches is a *new* built-in whose author never considered whether it needed a
block-scoped form at all. That question was answered ad hoc three times before
anyone wrote it down, and ``Patcher`` is what the ad hoc answer costs — a
shipped fixture that lost 41-to-0 to a helper an adopter wrote themselves.

So the partition below is asserted in **both** directions. A built-in missing
from every bucket fails, which is the case this file exists for. A bucket entry
naming a type that no longer registers also fails, because a stale exemption is
how a partition quietly stops covering the thing it claims to cover.

The registry is populated by importing ``oxitest`` — *not* by
``BuiltinFixture.ensure_registered()``, whose docstring promises to "import all
builtin modules" but whose body only sets a flag. Calling it here would look
like the arrange step and do nothing.
"""

from __future__ import annotations

import inspect
from typing import Any

from oxitest import (
    FdCapture,
    LogCapture,
    Patcher,
    StdCapture,
    TempDir,
    TempDirFactory,
    TestContext,
    WarnCapture,
)
from oxitest._bridge._builtins._base import BuiltinFixture

# ── The partition ─────────────────────────────────────────────────────────────
# Every type in BuiltinFixture.registered_types() belongs to exactly one bucket.
# Adding a built-in without classifying it here fails test_every_builtin_is_
# classified — deliberately, per ADR-0012 Rule 5.

#: Built-in -> the member that narrows it to a block. ADR-0012 Rule 1.
HAS_BLOCK_SCOPED_FORM: dict[type, str] = {
    StdCapture: "disabled",
    FdCapture: "disabled",
    LogCapture: "at_level",
}

#: Built-ins for which a block-scoped form is meaningless. ADR-0012 Rule 3.
#: TempDir/TempDirFactory are value carriers — there is no narrower window over
#: "a directory exists". TestContext is metadata plus finalizers; narrowing it
#: would mean narrowing the test. WarnCapture's block-scoped case is a
#: different question, answered by oxi.warns().
NEEDS_NONE: frozenset[type] = frozenset(
    {TempDir, TempDirFactory, WarnCapture, TestContext}
)

#: Built-ins that need a block-scoped form and do not have one yet.
#: Each entry names the issue that closes it. Empty is the goal, not the norm.
#: Patcher is here because it satisfies none of ADR-0012 Rule 4's four
#: conditions, so the block-scoped form is its primary shape and the injected
#: fixture is the vestige — kept only because the class is semver-protected.
KNOWN_GAP: dict[type, str] = {Patcher: "#1696"}


def _is_context_manager_factory(member: Any) -> bool:
    """Is ``member`` a ``@contextmanager``-decorated function?

    ``contextlib.contextmanager`` sets ``__wrapped__`` to the underlying
    generator function. Checking that it *is* a generator function — rather
    than merely present — keeps an unrelated ``functools.wraps`` decorator from
    satisfying this. Every block-scoped form in the tree uses this spelling; no
    built-in implements ``__enter__`` directly.
    """
    return inspect.isgeneratorfunction(getattr(member, "__wrapped__", None))


def test_every_builtin_is_classified() -> None:
    """Each registered built-in sits in exactly one ADR-0012 bucket."""
    # Arrange
    registered = set(BuiltinFixture.registered_types())
    classified = set(HAS_BLOCK_SCOPED_FORM) | NEEDS_NONE | set(KNOWN_GAP)

    # Act
    unclassified = registered - classified
    stale = classified - registered

    # Assert
    assert registered, (
        "no built-ins registered at all — every assertion below would hold "
        "vacuously, so this file would pass while guarding nothing"
    )
    assert not unclassified, (
        f"{sorted(cls.__name__ for cls in unclassified)} registered as "
        f"built-ins without declaring whether they need a block-scoped form. "
        f"Decide it now rather than later: add each to HAS_BLOCK_SCOPED_FORM "
        f"with the member name, to NEEDS_NONE, or to KNOWN_GAP with an issue. "
        f"'Needs none' is a fine answer — not answering is what ADR-0012 "
        f"Rule 5 exists to prevent"
    )
    assert not stale, (
        f"{sorted(cls.__name__ for cls in stale)} are classified here but no "
        f"longer registered as built-ins. A stale exemption makes this "
        f"partition look like it covers something it does not — remove them"
    )


def test_declared_block_scoped_forms_exist() -> None:
    """Every member named in HAS_BLOCK_SCOPED_FORM resolves and is a context manager."""
    # Arrange
    missing: list[str] = []
    not_a_context_manager: list[str] = []

    # Act
    for cls, member_name in HAS_BLOCK_SCOPED_FORM.items():
        # hasattr walks the MRO, which matters: `disabled` is defined on
        # _CaptureBase, so a cls.__dict__ lookup would report both capture
        # built-ins as missing their form.
        if not hasattr(cls, member_name):
            missing.append(f"{cls.__name__}.{member_name}")
            continue
        if not _is_context_manager_factory(getattr(cls, member_name)):
            not_a_context_manager.append(f"{cls.__name__}.{member_name}")

    # Assert
    assert not missing, (
        f"{missing} are declared as block-scoped forms but do not resolve on "
        f"the class or any of its bases. Either the member was renamed — in "
        f"which case update this map — or the form was removed, which is a "
        f"decision, not a bugfix: see ADR-0012 before restoring it"
    )
    assert not not_a_context_manager, (
        f"{not_a_context_manager} resolve but are not @contextmanager "
        f"functions. ADR-0012 Rule 1 spells a block-scoped form as a context "
        f"manager on the object; a plain method that mutates and returns None "
        f"(LogCapture.set_level) narrows nothing and does not belong here"
    )


def test_needs_none_builtins_have_not_grown_a_form() -> None:
    """A built-in exempted from the rule has not quietly acquired a block-scoped form.

    Set membership alone cannot catch this: a built-in that grows a context
    manager while still listed under NEEDS_NONE keeps the partition valid and
    the exemption false. That is the same silent drift the rule exists to stop,
    arriving from the other direction.
    """
    # Arrange
    grown: list[str] = []

    # Act
    for cls in sorted(NEEDS_NONE, key=lambda exempt: exempt.__name__):
        grown.extend(
            f"{cls.__name__}.{name}"
            for name in dir(cls)
            if not name.startswith("_")
            and _is_context_manager_factory(getattr(cls, name, None))
        )

    # Assert
    assert not grown, (
        f"{grown} are block-scoped forms on built-ins listed in NEEDS_NONE. "
        f"Either the exemption is now wrong — move the built-in to "
        f"HAS_BLOCK_SCOPED_FORM — or the new member is not a block-scoped "
        f"form and should not be a context manager. ADR-0012 wants that "
        f"decided, not defaulted"
    )
