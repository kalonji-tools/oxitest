"""End-to-end coverage ratchet tests (wayfinder #1604, #1606)."""

from __future__ import annotations

from oxitest import TempDir, helpers

_PKG_MISSING = (
    '"""pkg."""\n\n__all__ = ["foo"]\n\ndef foo():\n    """No examples."""\n    pass\n'
)

_PKG_COVERED = (
    '"""pkg."""\n\n'
    '__all__ = ["foo"]\n\n'
    "def foo():\n"
    '    """Foo.\n\n'
    "    Examples:\n"
    "        >>> 1 + 1\n"
    "        2\n"
    '    """\n'
    "    pass\n"
)


def _pyproject(*, strict: str, waivers: str) -> str:
    return f"""
    [tool.oxitest]
    testpaths = ["mypkg"]
    strict = "{strict}"

    [tool.oxitest.doctest]
    scope = "public"
    waivers = "{waivers}"
    """


def test_waived_missing_subject_downgrades_to_notice(tmp: TempDir) -> None:
    """When the waivers file covers a missing subject, enforce mode exits 0.

    Waived subjects surface as Notice, not blocking.
    """
    helpers.integ.write_project(
        tmp,
        tests={},
        pyproject=_pyproject(strict="enforce", waivers=".oxi-doctest-waivers"),
        extra_files={
            ".oxi-doctest-waivers": "mypkg.foo\n",
            "mypkg/__init__.py": _PKG_MISSING,
        },
    )
    stdout, stderr, rc = helpers.common.run_oxitest(tmp)
    assert rc == 0, (
        "enforce mode with the only missing subject waived must not fail — "
        f"waived subjects downgrade to Notice; got rc={rc}\nstdout:\n{stdout}\n"
        f"stderr:\n{stderr}"
    )


def test_missing_subject_not_waived_under_abort_hard_fails(
    tmp: TempDir,
) -> None:
    """strict=abort + missing subject not in waivers file => non-zero exit."""
    helpers.integ.write_project(
        tmp,
        tests={},
        pyproject=_pyproject(strict="abort", waivers=".oxi-doctest-waivers"),
        extra_files={
            ".oxi-doctest-waivers": "",
            "mypkg/__init__.py": _PKG_MISSING,
        },
    )
    stdout, stderr, rc = helpers.common.run_oxitest(tmp)
    assert rc != 0, (
        "strict=abort must hard-fail when a non-waived subject lacks "
        f"coverage; got rc={rc}\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    combined = stdout + stderr
    assert "mypkg.foo" in combined, (
        "failure output must name the offending subject so the user knows "
        f"what to waive or fix; stdout:\n{stdout}\nstderr:\n{stderr}"
    )


def test_stale_waivers_entry_hard_fails_regardless_of_strict(
    tmp: TempDir,
) -> None:
    """Shrink-only enforcement: stale waivers entries Error under any strict mode.

    A name in the waivers file that is not currently a missing subject must Error
    even under strict=enforce. Run via cwd (not explicit path) so that the
    stale-entry check is active — subset runs skip stale checking to avoid
    false positives.
    """
    helpers.integ.write_project(
        tmp,
        tests={},
        pyproject=_pyproject(strict="enforce", waivers=".oxi-doctest-waivers"),
        extra_files={
            ".oxi-doctest-waivers": "mypkg.foo\n",
            "mypkg/__init__.py": _PKG_COVERED,
        },
    )
    stdout, stderr, rc = helpers.common.run_oxitest(None, cwd=str(tmp))
    assert rc != 0, (
        "stale waivers entries must always Error, even under enforce — "
        f"this is the shrink-only ratchet; got rc={rc}\nstdout:\n{stdout}\n"
        f"stderr:\n{stderr}"
    )
    combined = stdout + stderr
    assert "mypkg.foo" in combined, (
        "stale-entry message must name the stale subject so the user can "
        f"remove the entry; stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "shrink-only" in combined, (
        "stale-entry message must explain the invariant so the user "
        f"understands why it failed; stdout:\n{stdout}\nstderr:\n{stderr}"
    )


def test_missing_waivers_file_treated_as_empty_set(tmp: TempDir) -> None:
    """Terminal burn-down: no waivers file + all subjects covered => exit 0."""
    helpers.integ.write_project(
        tmp,
        tests={},
        pyproject=_pyproject(strict="abort", waivers=".oxi-doctest-waivers"),
        extra_files={
            "mypkg/__init__.py": _PKG_COVERED,
        },
    )
    stdout, stderr, rc = helpers.common.run_oxitest(tmp)
    assert rc == 0, (
        "missing waivers file + all subjects covered is the happy path; "
        f"got rc={rc}\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )


def test_stale_waivers_entry_suppressed_when_explicit_path_given(tmp: TempDir) -> None:
    """Subset runs skip the stale-entry check to avoid false positives.

    When oxitest is invoked with an explicit path arg, a waivers entry for a
    subject outside the scanned subset would look stale — but that's a scanning
    artefact, not real staleness.
    """
    helpers.integ.write_project(
        tmp,
        tests={},
        pyproject=_pyproject(strict="abort", waivers=".oxi-doctest-waivers"),
        extra_files={
            ".oxi-doctest-waivers": "mypkg.foo\n",
            "mypkg/__init__.py": _PKG_COVERED,
        },
    )
    # Explicit path arg → filter.has_explicit_paths = true → stale check skipped.
    stdout, stderr, rc = helpers.common.run_oxitest(tmp)
    assert rc == 0, (
        "subset run with a stale waivers entry must NOT fire the stale-entry check — "
        f"the entry could legitimately refer to a subject outside the scanned subset; "
        f"got rc={rc}\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
