"""#2067: a test function returns ``None``.

A test function containing ``yield`` is a generator function. Calling it
returns a generator and runs none of its body, so before this change the test
reported as passed having executed nothing.

These are e2e rather than unit tests because the defect is only observable
across the collection/execution seam — every half behaves correctly alone.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from tests import helpers

_DATA_ROOT = Path(__file__).parent / "data"
_GEN_SYNC = _DATA_ROOT / "gen_test_sync"
_GEN_ASYNC = _DATA_ROOT / "gen_test_async"
_GEN_CLASS = _DATA_ROOT / "gen_test_class"
_GEN_SKIPPED = _DATA_ROOT / "gen_test_skipped"
_GEN_WRAPPED = _DATA_ROOT / "gen_test_wrapped"
_RETURN_STRICT = _DATA_ROOT / "return_value_strict"
_RETURN_LAX = _DATA_ROOT / "return_value_lax"
_ASYNC_RETURN_GEN = _DATA_ROOT / "async_return_gen"


def _run_cold(project: Path) -> tuple[str, str, int]:
    """Run *project* with no item cache, and return ``(stdout, stderr, rc)``.

    The item cache serves a file's collected items **without importing it**, so
    a cache left by any earlier run hides a collection guard completely. That
    is not hypothetical here: a mutation run that made one of these projects
    collect left a cache entry which then survived the revert, and the test
    that had just killed the mutant went on failing against correct code.

    ``.oxitest_cache/`` is gitignored, so removing it has no tracked effect.
    """
    shutil.rmtree(project / ".oxitest_cache", ignore_errors=True)
    return helpers.run_oxitest(project, "--warnings")


def test_sync_generator_is_refused_at_collection() -> None:
    """The headline defect: passed, with the body never entered."""
    # Act
    stdout, stderr, rc = _run_cold(_GEN_SYNC)
    output = stdout + stderr

    # Assert
    assert rc == 3, (
        f"a generator test must be refused at collection; rc 0 means it was "
        f"reported as passed without executing, which is the defect\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "test_generator" in output, (
        f"the refusal must name the offending function, or the user cannot "
        f"find it\n{output}"
    )
    assert "yield" in output, (
        f"the refusal must name the cause; a message that only says 'invalid' "
        f"leaves the user to guess\n{output}"
    )
    # The count line, not the bare word: the refusal message itself contains
    # "reported as passed", so `"passed" not in output` fires on the message it
    # is meant to accept.
    assert "collected 0 items" in output, (
        f"collection was refused, so nothing ran; any item count here means "
        f"the guard fired after collection completed\n{output}"
    )
    assert "2 passed" not in output, (
        f"the sibling test must not have run either — a refusal stops the "
        f"module, it does not skip one function\n{output}"
    )


def test_async_generator_is_refused_identically() -> None:
    """`iscoroutinefunction` is False for these, so they run as sync tests."""
    # Act
    stdout, stderr, rc = _run_cold(_GEN_ASYNC)
    output = stdout + stderr

    # Assert
    assert rc == 3, (
        f"the async form must be refused too — it is the same defect reached "
        f"through a predicate that answers False\nstdout:\n{stdout}\n"
        f"stderr:\n{stderr}"
    )
    assert "test_async_generator" in output, (
        f"the refusal must name the async offender\n{output}"
    )


def test_generator_method_in_a_test_class_is_refused() -> None:
    """One loop serves both walks — assert it rather than assume it."""
    # Act
    stdout, stderr, rc = _run_cold(_GEN_CLASS)
    output = stdout + stderr

    # Assert
    assert rc == 3, (
        f"a generator method must be refused; `_collect_items` is shared by "
        f"`_module_members` and `_class_members`, and this pins that it is\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "test_method_generator" in output, (
        f"the refusal must name the method\n{output}"
    )


def test_a_skipped_generator_test_is_still_refused() -> None:
    """A skip does not make a malformed declaration well-formed."""
    # Act
    stdout, stderr, rc = _run_cold(_GEN_SKIPPED)
    output = stdout + stderr

    # Assert
    assert rc == 3, (
        f"@oxi.mark.skip must not suppress the refusal — the B1 static gate "
        f"refuses a skipped test for the same reason\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    # The count line, not the bare word: the project directory is named
    # `gen_test_skipped`, so `"skipped" not in output` matches the path oxitest
    # prints and fails on a correct refusal.
    assert "1 skipped" not in output, (
        f"a skip count means the guard ran after mark evaluation, so a skipped "
        f"generator test would ship unrefused\n{output}"
    )


def test_a_wrapped_generator_test_is_caught_at_runtime() -> None:
    """The case that ruled out a collection-only design.

    ``functools.wraps`` leaves ``isgeneratorfunction`` False on the wrapper —
    measured on Python 3.12.13 — so collection cannot see it. Calling it still
    returns a generator, so the value can.
    """
    # Act
    stdout, stderr, rc = _run_cold(_GEN_WRAPPED)
    output = stdout + stderr

    # Assert
    assert rc == 4, (
        f"the wrapped generator must be caught while running; rc 0 means both "
        f"guards missed it and it passed with an empty body\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "test_wrapped_generator" in output, (
        f"the error must name the wrapped offender, not the wrapper\n{output}"
    )
    assert "1 passed" in output, (
        f"the run must not be stopped — the sibling test still reports, which "
        f"is what makes this a per-test error rather than a refusal\n{output}"
    )


def test_returning_a_value_is_a_strict_violation() -> None:
    """The static third: the body ran, so this is a smell, not a silent defect."""
    # Act
    stdout, stderr, rc = _run_cold(_RETURN_STRICT)
    output = stdout + stderr

    # Assert
    assert rc == 3, (
        f'a strict violation under `strict = "abort"` exits 3\n'
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "test-returns-value" in output, (
        f"the violation must be named, or the user cannot look it up in "
        f"errors.md\n{output}"
    )


def test_returning_a_value_is_silent_when_strict_is_off() -> None:
    """Unlike the other two points, this one is opt-in.

    There is no `--strict` CLI flag — the setting is config-only — so the two
    modes are two projects rather than two invocations of one.
    """
    # Act
    stdout, stderr, rc = _run_cold(_RETURN_LAX)
    output = stdout + stderr

    # Assert
    assert rc == 0, (
        f"the check must not fire outside strict mode — the body ran, so "
        f"nothing is silently unverified\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "test-returns-value" not in output, (
        f"a violation reported outside strict mode makes the strict dial a lie"
        f"\n{output}"
    )


def test_an_async_test_returning_a_generator_is_caught_at_runtime() -> None:
    """The async arm of the runtime guard, which no other test reaches.

    The two wrapped-generator cases run on the *sync* path: `is_async` comes
    from `iscoroutinefunction`, which answers False for a wrapper. This one is
    an ordinary coroutine, so it is awaited by `run_base_async`, and the value
    it returns is what the guard classifies.

    Added at stage 8: a mutation deleting the async call left the whole suite
    green, so the line shipped untested.
    """
    # Act
    stdout, stderr, rc = _run_cold(_ASYNC_RETURN_GEN)
    output = stdout + stderr

    # Assert
    assert rc == 4, (
        f"an async test whose awaited value is a generator must be refused; "
        f"rc 0 means the async arm of the guard never ran\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "test_returns_a_generator" in output, (
        f"the error must name the offending coroutine\n{output}"
    )
    assert "wrapper" not in output, (
        "no wrapper is involved here, so the message must not name one — it "
        "would send the reader hunting for a decorator that does not exist"
    )
    assert "1 passed" in output, (
        f"the sibling async test must still run and report; this is a per-test "
        f"error, not a refusal of the module\n{output}"
    )
