"""Integration tests for lazy collection pipeline.

Each test here names an *import* behaviour, and a passed-test count cannot
observe an import: a module that oxitest imported and then deselected reports
the same count as one it never imported at all (#2111).

So every module that must not be imported writes a marker file beside itself
at module scope. Importing the module is the only way that file can appear,
which makes the absence of the marker a direct observation of the skip. Each
test also asserts a *positive* control — the marker of a module that must be
imported — so a run in which the marker mechanism silently stopped working
cannot pass by writing no markers at all.
"""

import os
import time

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ

#: Written at module scope, so the file exists if and only if the module was
#: imported. ``__file__`` keeps each marker beside its own module.
_IMPORT_MARKER = (
    "import pathlib\n"
    "pathlib.Path(__file__).with_suffix('.imported').write_text(\n"
    "    'x', encoding='utf-8'\n"
    ")\n"
)


def _was_imported(tmp: TempDir, module: str) -> bool:
    """Whether *module* (a stem such as ``test_a``) left its import marker."""
    return (tmp / f"{module}.imported").exists()


def test_lazy_collection_single_node_id_skips_other_modules(tmp: TempDir) -> None:
    """Running a single test by node ID should not import unmatched modules."""
    # Arrange
    integ.write_project(
        tmp,
        tests={
            "test_a.py": (
                f"{_IMPORT_MARKER}def test_one(): pass\ndef test_two(): pass\n"
            ),
            "test_b.py": f"{_IMPORT_MARKER}def test_three(): pass\n",
            "test_c.py": f"{_IMPORT_MARKER}def test_four(): pass\n",
        },
    )

    # Act
    out, _, rc = helpers.run_oxitest_subcmd(
        tmp,
        "run",
        "test_a.py::test_one",
        cwd=".",
    )

    # Assert
    integ.assert_passed(out, rc, count=1)
    assert _was_imported(tmp, "test_a"), (
        "the selected module must be imported, or the two absences below prove "
        "only that the marker mechanism is broken"
    )
    assert not _was_imported(tmp, "test_b"), (
        "a node-id selecting test_a must not import test_b — the count stays 1 "
        "either way, so the marker is the only thing that can catch a "
        "regression back to eager collection"
    )
    assert not _was_imported(tmp, "test_c"), (
        "test_c holds no selected test either; a skip that covers one unmatched "
        "module and not the rest is still a regression"
    )


def test_lazy_collection_expression_filter(tmp: TempDir) -> None:
    """Expression filter should only import modules with matching tests."""
    # Arrange
    integ.write_project(
        tmp,
        tests={
            "test_auth.py": (
                f"{_IMPORT_MARKER}"
                "import oxitest as oxi\n@oxi.mark.slow\ndef test_login(): pass\n"
            ),
            "test_db.py": f"{_IMPORT_MARKER}def test_query(): pass\n",
        },
        pyproject='[tool.oxitest]\nmarkers = ["slow: slow tests"]\n',
    )

    # Act
    out, _, rc = helpers.run_oxitest(
        tmp,
        "-E",
        "mark(slow)",
    )

    # Assert
    integ.assert_passed(out, rc, count=1)
    assert _was_imported(tmp, "test_auth"), (
        "the module holding the matching mark must be imported, or the absence "
        "below proves nothing"
    )
    assert not _was_imported(tmp, "test_db"), (
        "no test in test_db carries the slow mark, so the prescan must decide "
        "that from the AST and never import the module"
    )


def test_lazy_collection_dynamic_file_falls_back_to_eager(tmp: TempDir) -> None:
    """A file defining tests through exec() is imported and its tests collected.

    The prescan reads the AST, where an ``exec()``-defined test does not
    appear. Only the import finds it, so the count is what distinguishes a
    module that was really imported from one whose statically visible test
    alone was collected.
    """
    # Arrange
    integ.write_project(
        tmp,
        tests={
            "test_dynamic.py": (
                f"{_IMPORT_MARKER}"
                "def test_static(): pass\n"
                "exec('def test_generated(): pass\\n')\n"
            ),
        },
    )

    # Act
    out, _, rc = helpers.run_oxitest(tmp)

    # Assert
    integ.assert_passed(out, rc, count=2)
    assert _was_imported(tmp, "test_dynamic"), (
        "the module must be imported eagerly; the AST cannot enumerate what "
        "exec() defines, so a prescan-only path would never reach test_generated"
    )


def test_lazy_collection_no_filter_imports_all(tmp: TempDir) -> None:
    """Running without any filter should import all modules (no regression)."""
    integ.write_project(
        tmp,
        tests={
            "test_a.py": "def test_one(): pass\n",
            "test_b.py": "def test_two(): pass\n",
        },
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc, count=2)


def test_lazy_collection_last_failed_only_imports_matched(tmp: TempDir) -> None:
    """--failed=only should only import modules with previously failed tests."""
    # Arrange
    integ.write_project(
        tmp,
        tests={
            "test_a.py": f"{_IMPORT_MARKER}def test_pass(): pass\n",
            "test_b.py": f"{_IMPORT_MARKER}def test_fail(): assert False\n",
        },
    )

    # First run — test_fail fails, and both modules are imported
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_failed(out, rc)

    # Fix the test, then clear the markers so the second run starts clean.
    (tmp / "test_b.py").write_text(
        f"{_IMPORT_MARKER}def test_fail(): pass\n", encoding="utf-8"
    )
    # Force both modules to miss the item cache: it keys on whole-second mtime
    # and collect_items serves a hit by `continue`-ing *before* the import, so
    # on a warm cache neither marker is rewritten and both assertions below go
    # vacuous. Rewriting the file is a race — it usually lands in the same
    # second as the original write. os.utime is the deterministic form.
    stamp = time.time() + 10
    for module in ("test_a", "test_b"):
        os.utime(tmp / f"{module}.py", (stamp, stamp))
        (tmp / f"{module}.imported").unlink()

    # Act — second run with --failed=only
    out, _, rc = helpers.run_oxitest(tmp, "--failed=only")

    # Assert
    integ.assert_passed(out, rc, count=1)
    assert _was_imported(tmp, "test_b"), (
        "the module holding the previously-failed test must be imported, or "
        "the absence below proves only that the markers were never rewritten"
    )
    assert not _was_imported(tmp, "test_a"), (
        "test_a held no failing test, so --failed=only must skip its import; "
        "the count is 1 whether test_a was imported and deselected or never "
        "imported at all"
    )
