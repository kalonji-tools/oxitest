"""End-to-end diagnostics for files the coverage scan cannot parse (#1800).

Before #1800 a file in the doctest coverage scan set that failed to parse was
silently dropped: no diagnostic named the file, and a ``scope``/``skip`` entry
naming a symbol inside it abstained silently. These tests pin the fix across
invocation shapes — full run, narrowed run — and across the ``strict`` dial
(off silent, enforce warning, abort hard-fail), plus the exclusion pin: a file
pruned before the parse (``norecursedirs``) must produce nothing.

``--warnings`` expands the diagnostic block so the reporter emits the
``doctest.coverage.parse-error`` context and inline messages verbatim.
"""

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ

BROKEN_SOURCE = "def broken(:\n    pass\n"


def _write_broken_project(tmp: TempDir, *, pyproject: str) -> None:
    """Scaffold a package with one parseable public module and one syntax error."""
    integ.write_project(
        tmp,
        tests={
            "test_sanity.py": ('def test_sanity():\n    assert True, "sanity"\n'),
        },
        pyproject=pyproject,
        extra_files={
            "mypkg/__init__.py": '"""Public package."""\n',
            "mypkg/broken.py": BROKEN_SOURCE,
        },
    )


def test_full_run_reports_unparsable_file_under_enforce(tmp: TempDir) -> None:
    """A syntax-error file in the scan set produces a diagnostic naming it."""
    _write_broken_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            testpaths = ["mypkg"]
            strict = "enforce"
            [tool.oxitest.doctest]
            scope = "public"
        """,
    )

    out, err, rc = helpers.run_oxitest(tmp, "--warnings")

    combined = out + err
    assert "doctest.coverage.parse-error" in combined, (
        "an unparsable file in the scan set must surface under its own "
        "context — before #1800 it was dropped with a bare `continue` and the "
        f"module silently vanished from coverage auditing: {combined!r}"
    )
    assert "broken.py" in combined, (
        "the diagnostic must name the file that failed to parse, otherwise "
        f"the user cannot find what to fix: {combined!r}"
    )
    assert rc == 0, (
        "strict = enforce reports parse failures as warnings — the run itself "
        f"must still pass, only abort may turn it into a hard failure: {combined!r}"
    )


def test_narrowed_run_still_reports_unparsable_file(tmp: TempDir) -> None:
    """A ``-E``-narrowed run reports the same parse failure as a full run.

    ADR-0010's invariant: the coverage walk is driven by ``testpaths``, not by
    how the item set was narrowed, so the verdict must not change shape here.
    """
    _write_broken_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            testpaths = ["mypkg"]
            strict = "enforce"
            [tool.oxitest.doctest]
            scope = "public"
        """,
    )

    out, err, _rc = helpers.run_oxitest(tmp, "--warnings", "-E", "name(test_sanity)")

    combined = out + err
    assert "doctest.coverage.parse-error" in combined, (
        "narrowing the item set with -E must not silence the parse-failure "
        "diagnostic — the coverage walk is testpaths-driven, and a verdict "
        f"that depends on run shape is the #1796 bug all over again: {combined!r}"
    )


def test_strict_off_silences_parse_error(tmp: TempDir) -> None:
    """``strict = "off"`` suppresses the parse-error diagnostic entirely."""
    _write_broken_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            testpaths = ["mypkg"]
            strict = "off"
            [tool.oxitest.doctest]
            scope = "public"
        """,
    )

    out, err, _rc = helpers.run_oxitest(tmp, "--warnings")

    combined = out + err
    assert "doctest.coverage.parse-error" not in combined, (
        "strict = off silences every coverage diagnostic, and the parse-error "
        "one rides the same dial — leaking it would break the documented "
        f"contract that off means silent: {combined!r}"
    )


def test_strict_abort_hard_fails_on_parse_error(tmp: TempDir) -> None:
    """``strict = "abort"`` promotes the parse failure to a collection error."""
    _write_broken_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            testpaths = ["mypkg"]
            strict = "abort"
            [tool.oxitest.doctest]
            scope = "public"
        """,
    )

    out, err, rc = helpers.run_oxitest(tmp)

    combined = out + err
    assert rc == 3, (
        "under strict = abort an unparsable file in the scan set is a "
        "coverage failure the run cannot vouch for — it must hard-fail "
        "collection (exit 3) exactly like the other coverage Error contexts: "
        f"got rc={rc}: {combined!r}"
    )
    assert "broken.py" in combined, (
        "the hard-fail message must carry the file name through the "
        f"CollectError promotion so the failure is actionable: {combined!r}"
    )


def test_skip_entry_in_unparsable_file_reports_parse_failure(tmp: TempDir) -> None:
    """A ``skip`` entry naming a symbol in an unparsable file reports, not abstains."""
    _write_broken_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            testpaths = ["mypkg"]
            strict = "enforce"
            [tool.oxitest.doctest]
            scope = "public"
            skip = ["mypkg/broken.py::helper"]
        """,
    )

    out, err, _rc = helpers.run_oxitest(tmp, "--warnings")

    combined = out + err
    assert "mypkg/broken.py::helper" in combined, (
        "the skip entry names a symbol the scan holds no evidence about "
        "because the parse failed — before #1800 it abstained silently, and "
        "the user never learned their skip entry was dead config: "
        f"{combined!r}"
    )
    assert "could not be parsed" in combined, (
        "the entry-level diagnostic must report the parse failure as the "
        f"reason, not a generic staleness verdict: {combined!r}"
    )
    assert "matched no coverage subjects" not in combined, (
        "a parse failure is not a staleness verdict — reporting NoSubjects "
        "here would send the user hunting for a symbol typo that does not "
        f"exist (#1796's wrong-diagnosis shape): {combined!r}"
    )


def test_scope_entry_in_unparsable_file_reports_parse_failure(tmp: TempDir) -> None:
    """A list-form ``scope`` entry into an unparsable file reports the failure."""
    _write_broken_project(
        tmp,
        pyproject="""\
            [tool.oxitest]
            testpaths = ["mypkg"]
            strict = "enforce"
            [tool.oxitest.doctest]
            scope = ["mypkg/broken.py::helper"]
        """,
    )

    out, err, _rc = helpers.run_oxitest(tmp, "--warnings")

    combined = out + err
    assert "mypkg/broken.py::helper" in combined, (
        "a scope entry that opted the file in must be told the file could "
        "not be read — abstaining leaves the user believing the symbol is "
        f"being coverage-checked when nothing is: {combined!r}"
    )
    assert "could not be parsed" in combined, (
        "the entry-level diagnostic must name the parse failure so the fix "
        f"(repair the file) is unambiguous: {combined!r}"
    )


def test_unparsable_file_under_norecursedirs_is_silent(tmp: TempDir) -> None:
    """A syntax-error file inside a ``norecursedirs`` directory produces nothing.

    Files pruned before the parse were never asked for — only files the
    scanner genuinely tried to read may produce the diagnostic.
    """
    integ.write_project(
        tmp,
        tests={
            "test_sanity.py": ('def test_sanity():\n    assert True, "sanity"\n'),
        },
        pyproject="""\
            [tool.oxitest]
            testpaths = ["mypkg"]
            norecursedirs = ["fixtures"]
            strict = "abort"
            [tool.oxitest.doctest]
            scope = "public"
        """,
        extra_files={
            "mypkg/__init__.py": '"""Public package."""\n',
            "mypkg/fixtures/broken.py": BROKEN_SOURCE,
        },
    )

    out, err, rc = helpers.run_oxitest(tmp)

    combined = out + err
    assert "doctest.coverage.parse-error" not in combined, (
        "norecursedirs prunes the file before the parse loop, so the scanner "
        "never asked for it — diagnosing it would make deliberately-invalid "
        f"fixture files impossible to keep in a strict repo: {combined!r}"
    )
    assert rc == 0, (
        "the run must stay green under strict = abort — the excluded broken "
        "file is exactly the noise case that made option 1 controversial, "
        f"and the exclusion is what makes it safe: {combined!r}"
    )
