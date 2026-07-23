"""End-to-end WARNING diagnostics for uncovered public subjects (#1602 M1).

Rust unit tests cover the coverage rule; these tests verify the full end-to-end
flow: pyproject parsing → subject enumeration → diagnostic emission → reporter
output. Each test scopes ``testpaths`` to the synthetic ``mypkg`` package so
only that module's subjects contribute to the diagnostic set — otherwise the
tmp-root ``test_*.py`` sanity file would also get flagged as an uncovered
public subject and drown out the assertions we care about.

``--warnings`` is passed to expand the diagnostic block so the reporter emits
the ``doctest.coverage`` context and inline messages verbatim; without it the
summary collapses to a bare count and we can't assert on the wording.
"""

from oxitest import TempDir, helpers


def test_missing_header_produces_warning(tmp: TempDir) -> None:
    """A public subject without `Examples:` in its docstring emits a WARNING."""
    helpers.integ.write_project(
        tmp,
        tests={},
        pyproject="""\
            [tool.oxitest]
            testpaths = ["mypkg"]
            [tool.oxitest.doctest]
            scope = "public"
            strictness = "warn"
        """,
        extra_files={
            "mypkg/__init__.py": (
                '"""Public package."""\n'
                '__all__ = ["foo"]\n'
                "def foo():\n"
                '    """No Examples section here."""\n'
                "    pass\n"
            ),
        },
    )
    out, err, _rc = helpers.common.run_oxitest(tmp, "--warnings")
    combined = out + err
    assert "doctest.coverage" in combined, (
        "the reporter must surface the coverage-rule context so users know "
        f"which rule fired; missing it hides the source of the warning: {combined!r}"
    )
    assert "mypkg.foo" in combined, (
        "the diagnostic must name the uncovered subject — otherwise the user "
        f"cannot map the warning back to a source location: {combined!r}"
    )
    assert "missing" in combined and "Examples:" in combined, (
        "the MissingHeader gap message must mention `missing` and `Examples:` "
        "so it is distinct from the HeaderNoExamples variant and points at "
        f"the fix: {combined!r}"
    )


def test_header_present_no_examples_produces_warning(tmp: TempDir) -> None:
    """Docstring with `Examples:` header but no `>>>` block emits a distinct WARNING."""
    helpers.integ.write_project(
        tmp,
        tests={},
        pyproject="""\
            [tool.oxitest]
            testpaths = ["mypkg"]
            [tool.oxitest.doctest]
            scope = "public"
            strictness = "warn"
        """,
        extra_files={
            "mypkg/__init__.py": (
                '"""Public package."""\n'
                '__all__ = ["foo"]\n'
                "def foo():\n"
                '    """Has header but no example.\n'
                "\n"
                "    Examples:\n"
                '    """\n'
                "    pass\n"
            ),
        },
    )
    out, err, _rc = helpers.common.run_oxitest(tmp, "--warnings")
    combined = out + err
    assert "doctest.coverage" in combined, (
        "the reporter must surface the coverage-rule context so users know "
        f"which rule fired: {combined!r}"
    )
    assert "mypkg.foo" in combined, (
        "the diagnostic must name the uncovered subject so users can locate "
        f"and fix it: {combined!r}"
    )
    assert ">>>" in combined, (
        "the HeaderNoExamples gap message must reference the missing `>>>` "
        "block — otherwise the fix is ambiguous with the MissingHeader "
        f"variant: {combined!r}"
    )


def test_off_mode_emits_no_coverage_diagnostics(tmp: TempDir) -> None:
    """No coverage diagnostics fire when ``strictness = "off"``.

    Explicit opt-out via ``strictness = "off"`` must suppress every
    ``doctest.coverage`` diagnostic — no matter what state the subjects
    are in.
    """
    helpers.integ.write_project(
        tmp,
        tests={},
        pyproject="""\
            [tool.oxitest]
            testpaths = ["mypkg"]
            [tool.oxitest.doctest]
            scope = "public"
            strictness = "off"
        """,
        extra_files={
            "mypkg/__init__.py": '__all__ = ["foo"]\ndef foo(): pass\n',
        },
    )
    out, err, _rc = helpers.common.run_oxitest(tmp, "--warnings")
    combined = out + err
    assert "doctest.coverage" not in combined, (
        "strictness=off must suppress all coverage-rule diagnostics — if any "
        "leak through, users who opted out will see spurious warnings and "
        f"lose trust in the config knob: {combined!r}"
    )


def test_covered_subject_produces_no_diagnostic(tmp: TempDir) -> None:
    """Fully covered subject (header + `>>>` block) emits no coverage diagnostic."""
    helpers.integ.write_project(
        tmp,
        tests={},
        pyproject="""\
            [tool.oxitest]
            testpaths = ["mypkg"]
            [tool.oxitest.doctest]
            scope = "public"
            strictness = "warn"
        """,
        extra_files={
            "mypkg/__init__.py": (
                '"""Public package."""\n'
                '__all__ = ["foo"]\n'
                "def foo():\n"
                '    """Fully covered.\n'
                "\n"
                "    Examples:\n"
                "        >>> 1 + 1\n"
                "        2\n"
                '    """\n'
                "    pass\n"
            ),
        },
    )
    out, err, _rc = helpers.common.run_oxitest(tmp, "--warnings")
    combined = out + err
    assert "doctest.coverage" not in combined, (
        "a subject with both an `Examples:` header and a `>>>` block is fully "
        "covered — emitting any coverage diagnostic here would mean the "
        f"scanner mis-parsed a well-formed docstring: {combined!r}"
    )
    assert "mypkg.foo" not in combined, (
        "no diagnostic should name the covered subject — the absence of "
        f"`mypkg.foo` is the observable proof of coverage: {combined!r}"
    )
