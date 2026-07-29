"""Slice-5 acceptance: inline @oxi.fixture in test_*.py (#1712).

Runs oxitest as a subprocess: inline fixtures are resolved during collection and
execution of the target project, so the assertions have to be about a real run
rather than about registry state.

The isolation pair in ``slice5_inline_fixtures`` is the load-bearing part. A
filter that blocked every ``ModuleSource`` would satisfy "a sibling cannot see
the inline fixture" exactly as well as a correct one, so the same project also
asserts that a package-level fixture stays visible from both files.
"""

from __future__ import annotations

from pathlib import Path

from oxitest import helpers

_DATA_ROOT = Path(__file__).parent / "data"
_PROJECT = _DATA_ROOT / "slice5_inline_fixtures"
_INLINE_PACKAGE = _DATA_ROOT / "slice5_inline_package"
_INLINE_SESSION_AT_ROOT = _DATA_ROOT / "slice5_inline_session_at_root"
_CROSS_FILE_INJECTION = _DATA_ROOT / "slice5_inline_cross_file_injection"

#: 3 tests in test_inline.py + 2 in test_sibling.py.
_TOTAL_TESTS = 5


def test_inline_fixtures_work_end_to_end() -> None:
    """Lifetimes, isolation, and package-level visibility, in one real run."""
    # Act
    stdout, stderr, rc = helpers.common.run_oxitest(_PROJECT)

    # Assert
    assert rc == 0, (
        f"the project must pass; rc={rc}\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert f"{_TOTAL_TESTS} passed" in stdout, (
        f"all {_TOTAL_TESTS} tests must run — a collection-level failure would "
        f"skip every isolation assertion and still leave rc==0 unexamined; "
        f"got:\n{stdout}"
    )


def test_inline_package_lifetime_is_rejected() -> None:
    """Inline `package` exceeds the module cap, wherever the file sits."""
    # Act
    stdout, stderr, rc = helpers.common.run_oxitest(_INLINE_PACKAGE)
    output = stdout + stderr

    # Assert
    assert rc != 0, (
        f'an inline lifetime="package" must fail the run; rc={rc}\n'
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    for expected in ("engine", "test_bad.py", "package", "module", "Hint"):
        assert expected in output, (
            f"the diagnostic must name {expected!r} so the user can act on it "
            f"without reading oxitest's source; got:\n{output}"
        )
    assert "__fixtures__.py" in output, (
        f"the hint must name the concrete file to move the declaration to — a "
        f"generic 'move it elsewhere' is unactionable (#1711 review); got:\n{output}"
    )


def test_inline_session_at_the_rootdir_package_is_rejected() -> None:
    """The case only the home-kind cap catches.

    The location rule from #1711 permits ``session`` when the anchor IS the
    rootdir package, and this declaration sits exactly there. If the two cap axes
    were collapsed into one check, this run would pass and an inline fixture
    would outlive its module.
    """
    # Act
    stdout, stderr, rc = helpers.common.run_oxitest(_INLINE_SESSION_AT_ROOT)
    output = stdout + stderr

    # Assert
    assert rc != 0, (
        f'an inline lifetime="session" must fail even at the rootdir package, '
        f"where the location rule alone would allow it; rc={rc}\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    for expected in ("cluster", "test_bad.py", "session", "module"):
        assert expected in output, (
            f"the diagnostic must name {expected!r}; got:\n{output}"
        )


def test_inline_fixture_is_not_injectable_across_files() -> None:
    """The Fixture[T] route honours the module restriction too.

    Proxy access resolves by (namespace, name); parameter injection resolves by
    bare name and never sees a namespace. They are two routes into the same
    registry, so filtering one leaves the other open — and this one was open:
    before the fix a sibling file received the fixture as ``FrozenProxy(2)``.
    """
    # Act
    stdout, stderr, rc = helpers.common.run_oxitest(_CROSS_FILE_INJECTION)
    output = stdout + stderr

    # Assert
    assert rc != 0, (
        f"injecting another file's inline fixture by type must fail the run; "
        f"rc={rc}\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "owned" in output, (
        f"the failure must name the fixture that could not be resolved; got:\n{output}"
    )
    assert "1 passed" in stdout, (
        f"test_owner.py's own test must still pass — the restriction is about "
        f"other files, not about the declaring one; got:\n{stdout}"
    )
