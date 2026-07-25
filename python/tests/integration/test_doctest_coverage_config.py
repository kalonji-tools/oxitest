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
    """An unknown ``scope`` value hard-exits with the deserializer error.

    Per ADR-0008, any deserialization error inside ``[tool.oxitest]``
    (including unknown enum variants like ``scope = "bogus"``) causes
    oxitest to exit ``UsageError`` (4) with the offending field named in
    stderr. This replaces the earlier soft-fallback contract that let typos
    silently drop config.
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
    out, err, rc = helpers.common.run_oxitest(tmp)
    assert rc != 0, (
        "an unknown scope value must hard-exit — silent fallback to defaults "
        f"would let typos ship undetected: {out}{err}"
    )
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


def test_unknown_oxitest_key_hard_exits(tmp: TempDir) -> None:
    """An unknown top-level ``[tool.oxitest]`` key hard-exits per ADR-0008.

    ``deny_unknown_fields`` on ``OxitestConfig`` catches typos like
    ``waivres`` at deserialization time. Without the fail-closed contract
    this would silently drop the field; with it, oxitest exits
    ``UsageError`` and names the offender so users can grep their
    pyproject for the typo.
    """
    helpers.integ.write_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            waivres = "typo"
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
        "unknown top-level [tool.oxitest] keys must hard-exit — silent drop "
        f"would let typos evade the fail-closed contract: {out}{err}"
    )
    combined = out + err
    assert "waivres" in combined, (
        "the deserializer error must name the offending field so users can "
        f"grep pyproject.toml for the typo: {combined!r}"
    )


def test_whole_file_syntax_error_does_not_block_oxitest(tmp: TempDir) -> None:
    """A syntax error outside ``[tool.oxitest]`` must not fail oxitest.

    Narrow scope per ADR-0008: oxitest is a test runner, not a TOML linter.
    A broken ``[tool.ruff]`` or ``[build-system]`` should not prevent
    oxitest from running under defaults — other tools reading pyproject
    will complain on their own turf.
    """
    helpers.integ.write_project(
        tmp,
        pyproject="""\
            [tool.ruff
            line-length = 100

            [tool.oxitest]
            timeout = 60
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
        "whole-file TOML syntax errors outside [tool.oxitest] must not fail "
        f"oxitest — narrow scope per ADR-0008: {out}{err}"
    )


def test_scope_member_form_covered_method_produces_no_diagnostic(
    tmp: TempDir,
) -> None:
    """Member-form scope entry: method with Examples block produces no diagnostic."""
    helpers.integ.write_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            strict = "enforce"

            [tool.oxitest.doctest]
            scope = ["src/mod.py::Cls::method"]
        """,
        tests={
            "test_pass.py": ('def test_x():\n    assert True, "sanity"\n'),
        },
        extra_files={
            "src/mod.py": (
                "class Cls:\n"
                "    def method(self):\n"
                '        """Do the thing.\n'
                "\n"
                "        Examples:\n"
                "\n"
                "        >>> Cls().method()\n"
                '        """\n'
            ),
        },
    )
    out, err, _rc = helpers.common.run_oxitest(tmp, "--warnings")
    combined = out + err
    assert "Cls.method" not in combined, (
        "a member-form scope entry pointing at a method with an Examples: "
        "block must produce zero coverage diagnostics — the docstring already "
        f"satisfies the coverage contract: {combined!r}"
    )


def test_scope_member_form_missing_examples_header_is_diagnosed(
    tmp: TempDir,
) -> None:
    """Member-form: method missing Examples header → diagnostic naming Cls.method."""
    helpers.integ.write_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            strict = "enforce"

            [tool.oxitest.doctest]
            scope = ["src/mod.py::Cls::method"]
        """,
        tests={
            "test_pass.py": ('def test_x():\n    assert True, "sanity"\n'),
        },
        extra_files={
            "src/mod.py": (
                'class Cls:\n    def method(self):\n        """Do the thing."""\n'
            ),
        },
    )
    out, err, _rc = helpers.common.run_oxitest(tmp, "--warnings")
    combined = out + err
    assert "Cls.method" in combined, (
        "the diagnostic must name the method via its dotted public_id "
        "(mod.Cls.method) so users can jump to the exact source location "
        f"without inferring the class from the file path: {combined!r}"
    )
    assert "Examples" in combined, (
        "the coverage gap message must mention Examples: so users know "
        f"which contract fired — not just 'coverage failed': {combined!r}"
    )


def test_scope_member_form_missing_method_hard_fails_as_stale(
    tmp: TempDir,
) -> None:
    """Member-form scope entry with non-existent method: stale hard-fail under abort."""
    helpers.integ.write_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            strict = "abort"

            [tool.oxitest.doctest]
            scope = ["src/mod.py::Cls::nope"]
        """,
        tests={
            "test_pass.py": ('def test_x():\n    assert True, "sanity"\n'),
        },
        extra_files={
            "src/mod.py": (
                "class Cls:\n"
                "    def other(self):\n"
                '        """Examples:\n'
                "\n"
                "        >>> 1\n"
                "        1\n"
                '        """\n'
            ),
        },
    )
    out, err, rc = helpers.common.run_oxitest(tmp)
    combined = out + err
    assert rc != 0, (
        "a member-form scope entry naming a non-existent method must hard-fail "
        "under strict = abort — otherwise typos in the class/method name "
        f"silently disable coverage for that method: {combined!r}"
    )
    assert "stale" in combined.lower() or "matched no" in combined.lower(), (
        "the stale-entry diagnostic must fire and name the missing entry so "
        f"users can find the typo in pyproject.toml: {combined!r}"
    )


def test_skip_member_form_excludes_only_that_method(tmp: TempDir) -> None:
    """Member-form skip: excludes only the named method; sibling still checked."""
    helpers.integ.write_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            strict = "enforce"

            [tool.oxitest.doctest]
            scope = ["src/mod.py::Cls::method", "src/mod.py::Cls::keep"]
            skip = ["src/mod.py::Cls::method"]
        """,
        tests={
            "test_pass.py": ('def test_x():\n    assert True, "sanity"\n'),
        },
        extra_files={
            "src/mod.py": (
                "class Cls:\n"
                "    def method(self):\n"
                "        pass\n"
                "    def keep(self):\n"
                '        """Do it."""\n'
            ),
        },
    )
    out, err, _rc = helpers.common.run_oxitest(tmp, "--warnings")
    combined = out + err
    assert "Cls.method" not in combined, (
        "the skip entry must remove the named method from coverage checks — "
        "otherwise skip and scope form a redundant grammar rather than "
        f"scope + subtractive-skip: {combined!r}"
    )
    assert "Cls.keep" in combined, (
        "the sibling method still in scope but missing Examples: must still "
        "diagnose — skip precision is per-method, not per-class: "
        f"{combined!r}"
    )


def test_scope_member_pass_body_method_diagnoses_at_def_line(tmp: TempDir) -> None:
    """Pass-body method (not an ellipsis stub) still diagnoses missing Examples."""
    helpers.integ.write_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            strict = "enforce"

            [tool.oxitest.doctest]
            scope = ["src/mod.py::Cls::method"]
        """,
        tests={
            "test_pass.py": ('def test_x():\n    assert True, "sanity"\n'),
        },
        extra_files={
            "src/mod.py": ("class Cls:\n    def method(self):\n        pass\n"),
        },
    )
    out, err, _rc = helpers.common.run_oxitest(tmp, "--warnings")
    combined = out + err
    assert "Cls.method" in combined, (
        "pass-body is NOT an ellipsis stub — the method is a real subject that "
        "must diagnose against the Examples: contract when named explicitly in "
        f"scope: {combined!r}"
    )
    assert "Examples" in combined, (
        "the diagnostic must mention Examples: so the user knows which contract "
        f"fired, not just 'coverage failed': {combined!r}"
    )


def test_scope_member_ellipsis_stub_method_surfaces_as_stale(tmp: TempDir) -> None:
    """Ellipsis-body abstract method: filtered at lookup → stale-entry."""
    helpers.integ.write_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            strict = "abort"

            [tool.oxitest.doctest]
            scope = ["src/mod.py::Cls::abstract_method"]
        """,
        tests={
            "test_pass.py": ('def test_x():\n    assert True, "sanity"\n'),
        },
        extra_files={
            "src/mod.py": ("class Cls:\n    def abstract_method(self) -> int: ...\n"),
        },
    )
    out, err, rc = helpers.common.run_oxitest(tmp)
    combined = out + err
    assert rc != 0, (
        "an ellipsis-body abstract method under a Member scope entry must "
        "surface as a stale-entry hard-fail — better UX than false-positive "
        "MissingHeader against a method that has no meaningful body to grade: "
        f"{combined!r}"
    )
    assert "stale" in combined.lower() or "matched no" in combined.lower(), (
        "the diagnostic must name the entry as stale so the user updates "
        f"pyproject.toml rather than adding docstrings to abstract stubs: {combined!r}"
    )
