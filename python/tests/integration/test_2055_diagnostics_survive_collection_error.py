"""Pending diagnostics survive a collection error (#2055).

``ready.rs`` holds the only drain for the two diagnostic channels, and three
exits above it returned before either one ran. Any collection error therefore
discarded every diagnostic the run had accumulated — including the one that
explains the error being reported.

The worst case is the misattributed error, and it is pinned by
``test_misattributed_fixture_not_found_carries_its_cause``: a ``pkg/__init__.py``
that declares a fixture and then fails to parse registers nothing, so the run
reports ``fixture 'conn' not found`` for a fixture that is declared correctly.
That exit is ``collected.rs:50``, which is a different site from the one the
issue names — a fix at the collection-error exit alone leaves this case broken.

⚠️ **Every assertion here reads ``out``, never ``out + err``.** The defect is
precisely that the diagnostic is present on stderr and absent from stdout: a
``tracing::warn!`` line survives either way. An assertion over the combined
streams passes before the fix and after it, and pins nothing. The two strings
differ, so the mistake is survivable but silent:

* stderr, present with or without the fix:
  ``prescan: file could not be parsed path="…" line=1``
* stdout, absent before the fix:
  ``unexpected EOF while parsing; fixtures in this file will not be registered``

``--warnings`` expands the diagnostics block. Without it the block collapses to
a count, which one test pins so the no-flag path is covered too.
"""

import json

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ

#: The stdout half of the diagnostic. Deliberately not the stderr wording.
STDOUT_DIAGNOSTIC = "unexpected EOF while parsing"

#: An unparsable declaration file that registers nothing.
BROKEN_DECLARATIONS = "def broken(\n"

PYPROJECT = """\
    [project]
    name = "probe"
    version = "0.1.0"
    [tool.oxitest]
    testpaths = ["pkg"]
"""

PYPROJECT_FX = """\
    [project]
    name = "probe"
    version = "0.1.0"
    [tool.oxitest]
    testpaths = ["proj"]
"""


def _write_pkg(tmp: TempDir, extra: dict[str, str] | None = None) -> None:
    """Scaffold a package whose ``__init__.py`` cannot be parsed."""
    files = {
        "pkg/__init__.py": BROKEN_DECLARATIONS,
        "pkg/test_x.py": 'def test_x() -> None:\n    assert True, "sanity"\n',
    }
    if extra:
        files.update(extra)
    integ.write_project(tmp, tests={}, pyproject=PYPROJECT, extra_files=files)


def test_passing_run_still_reports_the_registration_diagnostic(tmp: TempDir) -> None:
    """The control: with no collection error the diagnostic was never dropped."""
    _write_pkg(tmp)

    out, _err, rc = helpers.run_oxitest(tmp, "--warnings")

    assert rc == 0, (
        "an unparsable __init__.py is survivable by #1765's decision, so this "
        f"run must still pass; a non-zero exit means that decision moved: {out!r}"
    )
    assert STDOUT_DIAGNOSTIC in out, (
        "this run has no collection error, so the diagnostic reaches the "
        "reporter through ready.rs as it always did — if it is missing here the "
        f"fix has broken the path it was not meant to touch: {out!r}"
    )


def test_unrelated_collection_error_keeps_the_diagnostic(tmp: TempDir) -> None:
    """A collection error in a *different* file no longer discards it."""
    _write_pkg(tmp, {"pkg/test_unrelated.py": "def test_unrelated(\n"})

    out, _err, rc = helpers.run_oxitest(tmp, "--warnings")

    assert rc == 3, (
        f"an unparsable test file is a collection error, which exits 3: got {rc}"
    )
    assert STDOUT_DIAGNOSTIC in out, (
        "the collection error is in test_unrelated.py and the diagnostic is "
        "about __init__.py — one must not discard the other, which is the "
        f"whole defect #2055 reports: {out!r}"
    )


def test_collapsed_count_appears_without_the_warnings_flag(tmp: TempDir) -> None:
    """Without ``--warnings`` the block collapses to a count, and still prints."""
    _write_pkg(tmp, {"pkg/test_unrelated.py": "def test_unrelated(\n"})

    out, _err, _rc = helpers.run_oxitest(tmp)

    assert "1 warning" in out, (
        "the collapsed form is what a user sees by default, so the drain must "
        "reach the reporter whether or not --warnings expands it; without this "
        f"the fix would only work for a flag nobody passes: {out!r}"
    )
    assert STDOUT_DIAGNOSTIC not in out, (
        "the collapsed form must stay collapsed — the maintainer decided on "
        "2026-08-12 that the error path does not force expansion, and this "
        f"pins that decision rather than the absence of a bug: {out!r}"
    )


def test_misattributed_fixture_not_found_carries_its_cause(tmp: TempDir) -> None:
    """The headline case: the error names the wrong thing, so the cause must print.

    This exits at ``collected.rs:50``. A drain at the collection-error exit
    alone does not reach it, which is why this test exists separately from
    ``test_unrelated_collection_error_keeps_the_diagnostic``.
    """
    integ.write_project(
        tmp,
        tests={},
        pyproject=PYPROJECT,
        extra_files={
            "pkg/__init__.py": (
                "import oxitest as oxi\n"
                "\n"
                "@oxi.fixture\n"
                "def conn() -> int:\n"
                "    return 1\n"
                "\n"
                "def typo(\n"
            ),
            "pkg/test_h.py": (
                "from oxitest import Fixture\n"
                "\n"
                "def test_h(conn: Fixture[int]) -> None:\n"
                '    assert conn == 1, "the fixture must be injected"\n'
            ),
        },
    )

    out, _err, _rc = helpers.run_oxitest(tmp, "--warnings")

    assert "fixture 'conn' not found" in out, (
        "the arrange step of this test is the misattribution itself — if this "
        "string is gone the scenario has changed and the assertion below no "
        f"longer pins what it claims to: {out!r}"
    )
    assert STDOUT_DIAGNOSTIC in out, (
        "conn IS declared correctly; it is absent only because __init__.py "
        "could not be parsed. Reporting the fixture as missing without that "
        "cause sends the user to look for a typo in a name that is right, "
        f"which is the failure #2055 was filed for: {out!r}"
    )


def test_doctest_coverage_warning_survives_a_collection_error(tmp: TempDir) -> None:
    """The second producer: the fix is channel-agnostic, not fixture-specific.

    ``strict`` must be ``enforce`` or ``abort`` or the coverage rule returns an
    empty vector and this test passes without measuring anything
    (``collection.rs:1161``).
    """
    integ.write_project(
        tmp,
        tests={},
        pyproject="""\
            [project]
            name = "probe"
            version = "0.1.0"
            [tool.oxitest]
            testpaths = ["pkg"]
            strict = "enforce"
            [tool.oxitest.doctest]
            scope = "public"
        """,
        extra_files={
            "pkg/__init__.py": "",
            "pkg/mod.py": (
                "def documented(x: int) -> int:\n"
                '    """Return x. No Examples section on purpose."""\n'
                "    return x\n"
            ),
            "pkg/test_x.py": 'def test_x() -> None:\n    assert True, "sanity"\n',
            "pkg/test_unrelated.py": "def test_unrelated(\n",
        },
    )

    out, _err, _rc = helpers.run_oxitest(tmp, "--warnings")

    assert "doctest.coverage" in out, (
        "the doctest coverage rule fills the same queue as fixture "
        "registration, so a fix that only rescued registration diagnostics "
        f"would leave this one dropped: {out!r}"
    )


def test_fx_boundary_refusal_carries_the_diagnostic(tmp: TempDir) -> None:
    """The third exit: ``refuse_fx_boundaries`` at ``collected.rs:74``.

    ⚠️ The broken ``__init__.py`` sits in the package that holds the violating
    test, and that placement is load-bearing. A first attempt put it in a
    package of its own with no test files in it and produced **no diagnostic at
    all** — prescan walks up from test files, so it never visited that package.
    The run looked exactly like a fix that had failed. Moving the broken file
    into ``admin/`` is what makes the diagnostic exist to be dropped.

    The exit code must stay 4. ``refuse_fx_boundaries`` discards the helper's
    return value on purpose, and a fix that returned it instead would change
    this to 3 and silently undo the decision recorded above that function.
    """
    integ.write_project(
        tmp,
        tests={},
        pyproject=PYPROJECT_FX,
        extra_files={
            "proj/__init__.py": "",
            "proj/api/__init__.py": "",
            "proj/api/__fixtures__.py": (
                "import oxitest as oxi\n"
                "\n"
                '@oxi.fixture(lifetime="function")\n'
                "def api_only() -> str:\n"
                '    return "api"\n'
            ),
            "proj/api/test_api.py": (
                "from oxitest import Fixtures\n"
                "\n"
                "def test_inside(fx: Fixtures) -> None:\n"
                '    assert fx.api.api_only == "api", "the anchor sees its own"\n'
            ),
            # Unparsable, and in the package that holds the violating test.
            "proj/admin/__init__.py": BROKEN_DECLARATIONS,
            "proj/admin/test_admin.py": (
                "from oxitest import Fixtures\n"
                "\n"
                "def test_cross(fx: Fixtures) -> None:\n"
                '    assert fx.api.api_only == "api", "a sibling must not cross B1"\n'
            ),
        },
    )

    out, _err, rc = helpers.run_oxitest(tmp, "--warnings")

    assert rc == 4, (
        "a fixture wiring error exits 4 by the decision recorded above "
        "refuse_fx_boundaries; 3 would mean the drain started returning the "
        f"helper's exit code instead of discarding it: got {rc}\n{out}"
    )
    assert "fixture-boundary" in out, (
        "the arrange step is the B1 refusal itself — without it this test "
        f"measures an ordinary run and the assertion below proves nothing: {out!r}"
    )
    assert STDOUT_DIAGNOSTIC in out, (
        "the boundary refusal is a third exit above the only drain, so it "
        "discarded the registration diagnostic exactly as the other two did "
        f"(#2055): {out!r}"
    )


def test_the_drain_does_not_reach_the_ctrf_report(tmp: TempDir) -> None:
    """The change is console-only, and this pins that it stays so.

    ``json.rs`` does not override ``record_diagnostics``, so the no-op default
    in ``traits.rs`` applies and CTRF carries no diagnostic on either path.
    Measured before this test was written, on a passing run and on a failing
    one. The drain feeds the same composite that owns the JSON reporter, so
    this asserts the drain did not leak into a format that never carried
    diagnostics before.
    """
    _write_pkg(tmp, {"pkg/test_unrelated.py": "def test_unrelated(\n"})
    report = tmp / "report.json"

    out, _err, _rc = helpers.run_oxitest(tmp, "--warnings", "--json", str(report))

    assert STDOUT_DIAGNOSTIC in out, (
        "the console half must still work with --json passed, otherwise this "
        f"test would pass by measuring a run that did nothing: {out!r}"
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert STDOUT_DIAGNOSTIC not in json.dumps(payload), (
        "CTRF has never carried diagnostics and this change does not add them "
        "— a diagnostic appearing here means the drain reached a reporter the "
        f"maintainer did not decide to change: {payload!r}"
    )
