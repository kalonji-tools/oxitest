"""End-to-end config parsing for [tool.oxitest.doctest] (#1602 M1).

Rust unit tests cover the deserializer in isolation; these tests verify the
whole load path through the CLI, including the error rendering for the legacy
``doctest_modules`` key and unknown enum values.
"""

from oxitest import TempDir, helpers


def test_doctest_section_parses(tmp: TempDir) -> None:
    """A valid ``[tool.oxitest.doctest]`` sub-table loads and the run succeeds."""
    helpers.integ.write_project(
        tmp,
        pyproject="""\
            [tool.oxitest.doctest]
            scope = "public"
        """,
        tests={
            "test_pass.py": """\
                def test_x():
                    assert True, "sanity"
            """,
        },
    )
    out, err, rc = helpers.common.run_oxitest(tmp)
    assert rc == 0, (
        f"a valid [tool.oxitest.doctest] section must not block the run: {out}{err}"
    )


def test_legacy_doctest_modules_hard_errors(tmp: TempDir) -> None:
    """Legacy ``doctest_modules`` key hard-errors with a migration hint."""
    helpers.integ.write_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            doctest_modules = true
        """,
        tests={
            "test_pass.py": """\
                def test_x():
                    assert True, "sanity"
            """,
        },
    )
    out, err, rc = helpers.common.run_oxitest(tmp)
    assert rc != 0, (
        "the removed doctest_modules key must fail at config load — silent "
        f"acceptance would mean stale config is being ignored: {out}{err}"
    )
    combined = out + err
    assert "doctest_modules" in combined, (
        "the error must name the offending key so users can grep for it in "
        f"their config, got: {combined!r}"
    )
    assert "[tool.oxitest.doctest]" in combined, (
        "the error must point at the new sub-table so users know where to "
        f"move their settings, got: {combined!r}"
    )


def test_invalid_scope_surfaces_deserializer_error(tmp: TempDir) -> None:
    """An unknown ``scope`` value surfaces the deserializer error to the user.

    The Rust unit test ``invalid_scope_enum_hard_fails_at_parse`` proves
    ``serde`` rejects unknown enum variants. E2E, ``Config::load`` currently
    catches the ``toml::de::Error`` and logs a WARN before falling back to
    defaults — see ``src/config/mod.rs`` around the ``pyproject.toml parse
    failed`` site. That soft-fallback is broader than doctest and pre-dates
    #1602; tightening it would change how every ``[tool.oxitest]`` typo is
    surfaced. Until that decision is made, verify the invariant that
    actually holds: the user sees a diagnostic message naming the bad
    variant (``bogus``) so they can fix it — silent acceptance would be the
    real regression.
    """
    helpers.integ.write_project(
        tmp,
        pyproject="""\
            [tool.oxitest.doctest]
            scope = "bogus"
        """,
        tests={
            "test_pass.py": """\
                def test_x():
                    assert True, "sanity"
            """,
        },
    )
    out, err, _rc = helpers.common.run_oxitest(tmp)
    combined = out + err
    assert "bogus" in combined, (
        "the deserializer error naming the bad variant must reach the user — "
        "otherwise a typo silently picks the default scope and coverage "
        f"stops matching the intended configuration: {combined!r}"
    )
    assert "scope" in combined, (
        "the diagnostic must identify which field rejected the value so "
        f"users know where to look in pyproject.toml: {combined!r}"
    )
