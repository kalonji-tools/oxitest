"""The freeze boundary the frozen-fixture hint's advice depends on (#2036).

`suggest_fix` tells a user who mutated a frozen fixture value to declare the
fixture with `lifetime="function"` for a mutable per-test copy. Nothing asserted
that the advice works: `test_proxy.py` exercises `FrozenProxy` directly and never
asks which lifetime produces one, so the tier that the advice names was untested
at the point the advice started naming it.

These are **characterisation** tests. The behaviour already works, so they have
no red phase — their proof of teeth is a mutant that wraps the per-test cache in
`FrozenProxy`, not an initial failure.

End-to-end rather than unit, because the claim is about which tier the
instantiator freezes, and that decision is only reachable through a real run.
"""

from __future__ import annotations

from pathlib import Path

from tests import helpers

_PROJECT = Path(__file__).parent / "data" / "fixture_freeze_boundary"
_HINT_PROJECT = Path(__file__).parent / "data" / "fixture_freeze_hint"


def test_the_freeze_boundary_falls_between_function_and_module() -> None:
    """A function-lifetime value is mutable; a module-lifetime one refuses."""
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_PROJECT, "--warnings")

    # Assert
    assert rc == 0, (
        f"both halves of the hint's advice must hold; a non-zero rc means the "
        f"tier the hint names does not behave as the hint promises\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "2 passed" in stdout, (
        f"both tests must run — a collection error would leave the mutability "
        f"and the refusal assertions unexamined while still exiting 0 on a "
        f"'no tests ran' path; got:\n{stdout}"
    )


def test_a_refused_write_carries_the_fix_suggestion() -> None:
    """The hint reaches the user, not just `suggest_fix`.

    `suggest_shared_mutation` feeds a synthetic outcome, so it proves the
    function returns a string and nothing more. The hint reaches the reporter
    only if the rendered message still carries the error class name, which is
    the single condition `suggest_fix` now matches on — #2036 removed the
    second, redundant clause. Nothing else asserts that condition end to end.
    """
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_HINT_PROJECT)

    # Assert
    assert rc != 0, (
        f"the run must fail — a passing run emits no hint, so a green exit "
        f"would leave the assertion below unable to fire\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert 'lifetime="function"' in stdout, (
        f"the fix suggestion must reach the user; if this fails the hint is "
        f"still produced by suggest_fix but no longer rendered, which every "
        f"unit test would miss\nstdout:\n{stdout}"
    )
