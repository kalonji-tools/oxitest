"""#1859: ADR-0009 enforcement no longer depends on prescan's spelling coverage.

Every project here declares fixtures through ``import oxitest as ox`` — a binding
``is_fixture_call`` does not recognize but the runtime registers normally, because
registration is marker-attribute based (``__oxitest_fixture__``). Any binding
declares a real fixture, so a static scan is not an authority on what exists.

Each test names the enforcement site it covers. They are e2e rather than unit
tests because the defect is only observable across the prescan/registration
seam: every half behaves correctly in isolation.
"""

from __future__ import annotations

from pathlib import Path

from tests import helpers

_DATA_ROOT = Path(__file__).parent / "data"
_ALIAS_FIXTURES_HOME = _DATA_ROOT / "alias_fixtures_home"
_ALIAS_INIT_HOME = _DATA_ROOT / "alias_init_home"
_ALIAS_DECORATED_NON_FIXTURE = _DATA_ROOT / "alias_decorated_non_fixture"
_ALIAS_PROCESS_OUTSIDE_ROOT = _DATA_ROOT / "alias_process_outside_root"
_ALIAS_PACKAGE_COLOCATES = _DATA_ROOT / "alias_package_colocates"
_ALIAS_INLINE_OVER_CAP = _DATA_ROOT / "alias_inline_over_cap"


def test_aliased_fixture_in_fixtures_home_resolves() -> None:
    """An unrecognized spelling must not gate the import away."""
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_ALIAS_FIXTURES_HOME)

    # Assert
    assert rc == 0, (
        f"the aliased declaration must register; a non-zero rc means prescan "
        f"skipped the import and the fixture never existed\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "1 passed" in stdout, (
        f"the test must run — a collection error would leave the resolution "
        f"assertion unexamined while still failing for a different reason; "
        f"got:\n{stdout}"
    )


def test_aliased_fixture_in_init_home_resolves() -> None:
    """`__init__.py` is the fully-silent row — no error fires there today."""
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_ALIAS_INIT_HOME)

    # Assert
    assert rc == 0, (
        f"an aliased declaration in __init__.py must register; reserved=false "
        f"suppresses even the mistyped-alias hint, so this row failed with no "
        f"diagnostic at all\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "1 passed" in stdout, (
        f"the test must run — otherwise the resolution assertion never "
        f"executes; got:\n{stdout}"
    )


def test_decorator_only_fixtures_home_is_not_an_error() -> None:
    """The old guard never inspected the decorator — only that one existed."""
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_ALIAS_DECORATED_NON_FIXTURE)
    output = stdout + stderr

    # Assert
    assert rc == 0, (
        f"a __fixtures__.py holding only @functools.cache declares no fixtures "
        f"and must not fail the run; the pre-#1859 guard fired on decorator "
        f"shape alone\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "import alias" not in output, (
        f"the mistyped-alias hint must be gone — it accused a file that never "
        f"mentioned oxitest\n{output}"
    )


def test_aliased_process_outside_rootdir_is_rejected() -> None:
    """ADR-0009 Rule 4 must apply to spellings prescan cannot name."""
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_ALIAS_PROCESS_OUTSIDE_ROOT, "--warnings")
    output = stdout + stderr

    # Assert
    assert rc != 0, (
        f'an aliased lifetime="process" below the rootdir package must be '
        f"rejected; before #1859 the AST-based check could not see this "
        f"spelling and the rule silently did not apply\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    for expected in ("engine", "process", "rootdir package"):
        assert expected in output, (
            f"the diagnostic must name the fixture, the tier, and the rule, or "
            f"the user cannot act on it; {expected!r} missing from:\n{output}"
        )


def test_aliased_package_declaration_reaches_the_scheduler() -> None:
    """The co-location warning is the scheduler consumer's observable effect."""
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_ALIAS_PACKAGE_COLOCATES, "--warnings")
    output = stdout + stderr

    # Assert
    assert rc == 0, f"the project must pass\nstdout:\n{stdout}\nstderr:\n{stderr}"
    assert "co-locates" in output and "engine" in output, (
        f'an aliased lifetime="package" must still co-locate its subtree; '
        f"before #1859 the AST could not see it, so the exactly-once guarantee "
        f"quietly did not hold and no warning was emitted\n{output}"
    )


def test_aliased_inline_over_cap_reports_every_offender() -> None:
    """The home-kind cap applies to any spelling, and one run names all of them."""
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_ALIAS_INLINE_OVER_CAP, "--warnings")
    output = stdout + stderr

    # Assert
    assert rc != 0, (
        f"an inline lifetime wider than module must fail whatever the import "
        f"spelling; before #1859 the AST-based cap could not see `ox` and both "
        f"declarations were accepted\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    for expected in ("first_bad", "second_bad"):
        assert expected in output, (
            f"violations accumulate per module — a fail-fast check would name "
            f"only the first, and someone whose aliased declarations were "
            f"silently ignored likely has several; {expected!r} missing "
            f"from:\n{output}"
        )
    assert "__fixtures__.py" in output, (
        f"the hint must name the sibling __fixtures__.py as the migration "
        f"target (#1711's review lesson): 'move it elsewhere' is unactionable "
        f"because the user cannot derive the destination\n{output}"
    )
