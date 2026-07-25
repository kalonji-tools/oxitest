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


def test_scope_off_hard_errors_with_migration_hint(tmp: TempDir) -> None:
    """Removed ``scope = "off"`` value surfaces a migration hint to the user.

    The value was dropped in 2.2.0. Users hitting the old syntax must get a
    clear migration hint pointing at the correct opt-out — drop the whole
    ``[tool.oxitest.doctest]`` table.

    Rust unit test ``doctest_scope_rejects_off_with_migration_hint`` proves
    the deserializer error itself. This E2E test verifies the error reaches
    the user through Config::load's soft-fallback path — the diagnostic
    must surface even when the whole pyproject silently falls back to
    defaults.
    """
    helpers.integ.write_project(
        tmp,
        pyproject="""\
            [tool.oxitest.doctest]
            scope = "off"
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
    assert "off" in combined, (
        "the deserializer error must name the removed value 'off' so users "
        f"can grep for it in their config: {combined!r}"
    )
    assert "[tool.oxitest.doctest]" in combined, (
        "the error must point at the correct migration path — dropping the "
        f"whole doctest table — not just say 'off is invalid': {combined!r}"
    )


def test_list_form_scope_covers_only_named_symbol(tmp: TempDir) -> None:
    """List-form scope entry covers only the named symbol.

    ``scope = ["path/to/mod.py::sym"]`` covers only the named symbol — other
    subjects in the same file are outside scope and produce no diagnostic
    even under ``strict = "enforce"``.
    """
    helpers.integ.write_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            strict = "enforce"

            [tool.oxitest.doctest]
            scope = ["src/mod.py::public"]
        """,
        tests={
            "test_pass.py": ('def test_x():\n    assert True, "sanity"\n'),
        },
        extra_files={
            "src/mod.py": (
                "def public():\n"
                '    """Examples:\n'
                "\n"
                "    >>> 1\n"
                "    1\n"
                '    """\n'
                "\n"
                "def also_public(): pass\n"
            ),
        },
    )
    out, err, _rc = helpers.common.run_oxitest(tmp, "--warnings")
    combined = out + err
    assert "also_public" not in combined, (
        "an out-of-scope subject must not produce a coverage diagnostic — "
        f"list-form scope is meant as a curated whitelist: {combined!r}"
    )


def test_skip_symbol_removes_only_that_symbol(tmp: TempDir) -> None:
    """Symbol-level skip entry excludes exactly that subject.

    ``skip = ["file.py::sym"]`` excludes exactly that subject — other subjects
    in the same file are still covered by ``scope = "public"``.
    """
    helpers.integ.write_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            strict = "enforce"

            [tool.oxitest.doctest]
            scope = "public"
            skip = ["src/mod.py::skip_me"]
        """,
        tests={
            "test_pass.py": ('def test_x():\n    assert True, "sanity"\n'),
        },
        extra_files={
            "src/mod.py": ("def keep_me(): pass\ndef skip_me(): pass\n"),
        },
    )
    out, err, _rc = helpers.common.run_oxitest(tmp, "--warnings")
    combined = out + err
    assert "keep_me" in combined, (
        "an in-scope subject must still be diagnosed for missing Examples — "
        f"skip should only remove the named symbol, not the whole file: {combined!r}"
    )
    assert "skip_me" not in combined, (
        "the skip entry must remove the named symbol from coverage checks — "
        f"subject-level skip precision is the whole point of the grammar: {combined!r}"
    )


def test_stale_scope_entry_hard_fails_under_abort(tmp: TempDir) -> None:
    """Stale scope entry hard-fails the run under ``strict = "abort"``.

    A stale scope entry (matches nothing) must promote to a hard-fail so the
    config error surfaces at collection time.

    Rust unit test ``stale_entries_promote_to_collect_error_under_abort``
    proves the promotion. This E2E test verifies the whole path: config
    load → subject filter → match tracking → stale diagnostic emission
    → hard-fail promotion → non-zero exit.
    """
    helpers.integ.write_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            strict = "abort"

            [tool.oxitest.doctest]
            scope = ["src/nonexistent.py"]
        """,
        tests={
            "test_pass.py": ('def test_x():\n    assert True, "sanity"\n'),
        },
        extra_files={
            "src/mod.py": (
                'def real():\n    """Examples:\n\n    >>> 1\n    1\n    """\n'
            ),
        },
    )
    out, err, rc = helpers.common.run_oxitest(tmp)
    combined = out + err
    assert rc != 0, (
        "a stale scope entry under strict = abort must hard-fail — otherwise "
        f"typos silently bypass coverage without the user noticing: {combined!r}"
    )
    assert "stale" in combined.lower() or "matched no" in combined.lower(), (
        "the error output must explain WHY the run failed — the message needs "
        f"to name the stale entry so users can locate the typo: {combined!r}"
    )
