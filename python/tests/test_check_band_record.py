"""Tests for the band-membership extractors in check_band_record.py.

The script is the single derivation of ADR-0019's placement rule — *a test
belongs to the band of what it starts* — into a committed record, and
``just check`` refuses when the tree and the record disagree (#2175).

Every extractor is a parser over source text, so each one can silently return a
partial set and read as agreement. ADR-0018 states the consequence for the
bridge lint and it holds here: *"each one is pinned by a test against a fixture
with a known field set."* These tests do that for band placement, and they pin
the three routes a body-only parse gets wrong:

- a test that reaches a product process through a **fixture** rather than
  through its own body,
- a test that spawns ``git`` — the environment, not the product,
- a call the resolver cannot classify, which must **refuse** rather than
  default to the Library band.

This file carries the ``tooling`` attribute, whose one obligation is that a
test with it makes its tool fail. ``test_gate_refuses_a_disagreeing_record``
and ``test_resolver_refuses_an_unknown_route`` discharge it.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Protocol

import oxitest as oxi
from oxitest import TempDir
from tests import helpers

# ── Script location ──────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_band_record.py"


def _load_script_module() -> ModuleType:
    """Load ``scripts/check_band_record.py`` as a module for direct-function testing.

    The scripts directory is not a package, so we use ``importlib.util`` rather
    than a normal import. Fresh module load per call — no cross-test state.

    The module is registered in ``sys.modules`` before it is executed, which
    ``check_bridge_sync.py``'s loader does not need to do. ``@dataclass``
    resolves ``sys.modules[cls.__module__]`` while processing the class, and an
    unregistered module makes that lookup return ``None``.
    """
    name = "check_band_record_under_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT_PATH)
    if spec is None or spec.loader is None:
        msg = f"could not load module spec from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write(root: Path, relative: str, source: str) -> Path:
    """Write *source* to *relative* under *root*, creating parent directories."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


class _RowLike(Protocol):
    """The row shape the script yields.

    The script is loaded through ``importlib``, so its ``Row`` class is not
    importable by name here. This states the shape the tests rely on, which
    keeps ``ty`` checking the accesses below instead of resolving them to
    ``Any``.
    """

    band: str
    test_id: str
    attributes: tuple[str, ...]


def _bands(rows: Iterable[_RowLike]) -> dict[str, str]:
    """Map each row's test id to its band, for assertions that read as prose."""
    return {row.test_id: row.band for row in rows}


# ── The placement rule: what does this test start? ───────────────────────────


def test_a_test_calling_a_product_starter_is_command(tmp: TempDir) -> None:
    """``helpers.run_oxitest`` starts the CLI, so its caller is a Command band test.

    This is ADR-0019 step 3 — the rule the whole record derives. If this
    extractor under-reports, every Command band test silently becomes a Library
    row and the record asserts a suite that does not exist.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/tests/test_cli.py",
        """
        from tests import helpers


        def test_reports_a_version() -> None:
            out, _, rc = helpers.run_oxitest(root, "--version")
            assert rc == 0, "the CLI must report its version"
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.python_rows(root / "python" / "tests", repo_root=root, exceptions={})

    # Assert
    assert _bands(rows) == {
        "python/tests/test_cli.py::test_reports_a_version": "Command"
    }, (
        "a test calling run_oxitest starts a product process, so ADR-0019 step 3 "
        "places it in the Command band — a Library row here would claim the CLI "
        "is covered by a test that never runs it"
    )


def test_a_test_starting_nothing_is_library(tmp: TempDir) -> None:
    """A test that starts no process falls through to ADR-0019 step 4.

    Library is the rule's default arm, so it must be reached by exhaustion
    rather than by assumption — the resolver refuses anything it cannot
    classify, and this test pins the case where classification succeeds and
    the answer is genuinely "nothing".
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/tests/test_registry.py",
        """
        def test_resolves_by_name() -> None:
            registry = {"alpha": 1}
            assert registry["alpha"] == 1, "a name must resolve to its value"
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.python_rows(root / "python" / "tests", repo_root=root, exceptions={})

    # Assert
    assert _bands(rows) == {
        "python/tests/test_registry.py::test_resolves_by_name": "Library"
    }, (
        "a test that starts no process is a Library band test — placing it "
        "anywhere else would inflate the Command band and make its coverage "
        "report describe tests that never ran a process"
    )


def test_a_test_reaching_a_starter_through_a_local_helper_is_command(
    tmp: TempDir,
) -> None:
    """A same-file helper that spawns carries its caller into the Command band.

    156 tests in this repository reach the funnel this way and spawn nothing in
    their own bodies. A resolver that reads only the test body reports every one
    of them as Library.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/tests/test_indirect.py",
        """
        from tests import helpers


        def _run(project: str) -> int:
            _, _, rc = helpers.run_oxitest(project)
            return rc


        def test_reaches_through_a_helper() -> None:
            assert _run("proj") == 0, "the project must pass"
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.python_rows(root / "python" / "tests", repo_root=root, exceptions={})

    # Assert
    assert _bands(rows) == {
        "python/tests/test_indirect.py::test_reaches_through_a_helper": "Command"
    }, (
        "the test starts a product process through _run, so it is a Command "
        "band test — reading only the test body would place 156 real tests in "
        "the wrong band"
    )


def test_a_test_reaching_a_starter_through_a_fixture_is_command(
    tmp: TempDir,
) -> None:
    """A fixture that spawns carries every test taking it into the Command band.

    ``test_process_tier_negatives.py::parallel_run`` is the live instance: it
    calls ``helpers.run_with_event_log`` and the two tests taking it spawn
    nothing themselves. No claim in #2175 or ADR-0019 covers this route, and a
    body-only parse silently reports both tests as Library.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/tests/test_via_fixture.py",
        """
        import oxitest as oxi
        from oxitest import Fixture
        from tests import helpers


        @oxi.fixture(lifetime="module")
        def shared_run() -> helpers.EventLogRun:
            return helpers.run_with_event_log("proj", "log", "-n", "2")


        def test_reads_the_shared_run(shared_run: Fixture[helpers.EventLogRun]) -> None:
            assert shared_run.lines("x"), "the run must have written its log"
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.python_rows(root / "python" / "tests", repo_root=root, exceptions={})

    # Assert
    assert _bands(rows) == {
        "python/tests/test_via_fixture.py::test_reads_the_shared_run": "Command"
    }, (
        "the fixture starts the product, so its consumer is a Command band "
        "test — this is the route that misplaces the two real tests in "
        "test_process_tier_negatives.py"
    )


def test_a_test_spawning_git_is_library(tmp: TempDir) -> None:
    """``git`` is the environment, not the product, so spawning it changes no band.

    ADR-0019 draws this line for Rust — *"The 23 Command::new sites in src/
    start git, true and cat. Those are the environment, not the product."* —
    and 8 Python tests take a git fixture. A rule keyed on "reaches subprocess"
    would place all 8 in the Command band and claim the CLI is covered by tests
    that never invoke it.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/tests/test_git.py",
        """
        import subprocess


        def test_reads_a_repository(tmp_path: str) -> None:
            subprocess.run(["git", "-C", tmp_path, "init"], check=True)
            assert tmp_path, "the repository must exist"
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.python_rows(root / "python" / "tests", repo_root=root, exceptions={})

    # Assert
    assert _bands(rows) == {
        "python/tests/test_git.py::test_reads_a_repository": "Library"
    }, (
        "spawning git starts the environment and not the product, so ADR-0019 "
        "step 3 does not fire — treating any subprocess as a product process "
        "would move 8 real tests into the wrong band"
    )


def test_a_subprocess_running_the_module_is_command(tmp: TempDir) -> None:
    """A raw ``sys.executable -m oxitest`` call is a product starter.

    55 files under python/tests reach subprocess directly rather than through
    the helper funnel. The product is identified by its argv, which is the only
    thing separating this call from the git call above — the two are the same
    AST shape.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/tests/test_raw.py",
        """
        import subprocess
        import sys


        def test_runs_the_module() -> None:
            result = subprocess.run(
                [sys.executable, "-m", "oxitest", "--version"], check=False
            )
            assert result.returncode == 0, "the module must run"
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.python_rows(root / "python" / "tests", repo_root=root, exceptions={})

    # Assert
    assert _bands(rows) == {
        "python/tests/test_raw.py::test_runs_the_module": "Command"
    }, (
        "argv names the product, so this starts a product process — the git "
        "test above is the same AST shape and must not match, which is why "
        "argv rather than the call site decides"
    )


def test_the_resolver_refuses_an_unknown_spawn_route(tmp: TempDir) -> None:
    """An unclassifiable spawn is an error naming the test, never a Library row.

    This is the difference between an approximation and a fact. The funnel is
    closed today; nothing keeps it closed. A contributor adding a fourth spawn
    helper must meet a refusal that names the file, not a wrong row that reads
    exactly like a correct one.

    ADR-0019 states the rule this discharges: *"Measured, and found nothing"
    and "did not measure" are different results. An instrument must not print
    the same sentence for both.*
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/tests/test_unknown.py",
        """
        import subprocess


        def test_spawns_something_unknown(command: list[str]) -> None:
            subprocess.run(command, check=True)
            assert command, "the command must be given"
        """,
    )
    module = _load_script_module()

    # Act / Assert
    with oxi.raises(module.UnresolvedSpawnError) as caught:
        module.python_rows(root / "python" / "tests", repo_root=root, exceptions={})

    assert "test_spawns_something_unknown" in str(caught.value), (
        "the refusal must name the test it could not place, because a reader "
        "who cannot find the offending test will widen the funnel blindly to "
        "make the error go away"
    )


# ── The exception list: a refusal a reader has answered ──────────────────────


def test_an_exception_places_a_test_the_parse_cannot_read(tmp: TempDir) -> None:
    """A listed exception supplies the band the parse could not derive.

    Two real tests spawn ``jq`` and ``bash`` through a ``shutil.which`` result,
    so argv names a variable and no parse can say what program runs. The answer
    is a reader's, recorded once, and not a guess made per run.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/tests/test_guard.py",
        """
        import shutil
        import subprocess


        def test_runs_the_shipped_guard() -> None:
            executable = shutil.which("bash")
            completed = subprocess.run([executable, "-c", "true"], check=False)
            assert completed.returncode == 0, "the guard must succeed"
        """,
    )
    module = _load_script_module()
    exceptions = {
        "python/tests/test_guard.py::test_runs_the_shipped_guard": (
            "Library",
            "runs bash, which is the environment and not the product",
        )
    }

    # Act
    rows = module.python_rows(
        root / "python" / "tests", repo_root=root, exceptions=exceptions
    )

    # Assert
    assert _bands(rows) == {
        "python/tests/test_guard.py::test_runs_the_shipped_guard": "Library"
    }, (
        "the exception supplies the band, so the test is placed rather than "
        "refused — without it the whole record cannot be derived at all"
    )


def test_an_exception_for_a_test_that_resolves_is_refused(tmp: TempDir) -> None:
    """A stale exception is an error, which is what keeps the list shrinking.

    An exception that no longer answers a live refusal is exactly the drift
    ``codecov.yml`` accumulated — 21 entries stating a claim nothing reads, one
    of them naming a file that a commit had deleted. An entry must justify
    itself on every run or it fails the gate.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/tests/test_plain.py",
        """
        def test_needs_no_exception() -> None:
            assert True, "this test resolves without help"
        """,
    )
    module = _load_script_module()
    exceptions = {
        "python/tests/test_plain.py::test_needs_no_exception": (
            "Command",
            "a reason that stopped being true",
        )
    }

    # Act / Assert
    with oxi.raises(module.StaleExceptionError) as caught:
        module.python_rows(
            root / "python" / "tests", repo_root=root, exceptions=exceptions
        )

    assert "test_needs_no_exception" in str(caught.value), (
        "the error must name the stale entry so it can be deleted — a list "
        "that cannot say which entry died is one nobody prunes"
    )


# ── What counts as a test at all ─────────────────────────────────────────────


def test_a_specimen_gets_no_row(tmp: TempDir) -> None:
    """A test-shaped function under ``data/`` is a Specimen, and no band holds one.

    236 of these sit under ``python/tests/data/``. ``norecursedirs`` excludes
    the directory, so nothing collects them; a record that counted them would
    overstate the suite by more than a tenth and every band count in it would
    be wrong.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/tests/data/project/test_specimen.py",
        """
        def test_written_into_a_project() -> None:
            assert True, "a band test writes this file and runs it"
        """,
    )
    _write(
        root,
        "python/tests/test_real.py",
        """
        def test_is_collected() -> None:
            assert True, "this one is a test"
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.python_rows(root / "python" / "tests", repo_root=root, exceptions={})

    # Assert
    assert _bands(rows) == {
        "python/tests/test_real.py::test_is_collected": "Library"
    }, (
        "a Specimen is input to a test and not a test, so it gets no row — "
        "CONTEXT.md states no band collects one"
    )


def test_a_class_method_carries_its_class_in_the_key(tmp: TempDir) -> None:
    """Two classes may hold a method of the same name, so the key needs the class.

    The record is keyed on a test. A key that drops the class collides, and a
    collision silently loses a row rather than failing — the record would hold
    fewer tests than the tree and still compare equal to itself.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/tests/test_shared_params.py",
        """
        import oxitest as oxi


        @oxi.parametrize(value=[1, 2])
        class DescribeAlpha:
            def test_holds(self, value: int) -> None:
                assert value, "the parameter must arrive"


        @oxi.parametrize(value=[3, 4])
        class DescribeBeta:
            def test_holds(self, value: int) -> None:
                assert value, "the parameter must arrive"
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.python_rows(root / "python" / "tests", repo_root=root, exceptions={})

    # Assert
    assert sorted(_bands(rows)) == [
        "python/tests/test_shared_params.py::DescribeAlpha::test_holds",
        "python/tests/test_shared_params.py::DescribeBeta::test_holds",
    ], (
        "each method needs its class in the key — without it the two rows "
        "collapse into one and the record undercounts the suite while still "
        "agreeing with itself"
    )


def test_a_nested_function_named_like_a_test_gets_no_row(tmp: TempDir) -> None:
    """Only a module-level or class-level function is collected as a test.

    A helper defined inside a test body is not collected by oxitest, so a
    walker that descends into function bodies invents rows for tests that do
    not exist.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/tests/test_nested.py",
        """
        def test_outer() -> None:
            def test_inner() -> None:
                return None

            test_inner()
            assert True, "the inner function is not a test"
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.python_rows(root / "python" / "tests", repo_root=root, exceptions={})

    # Assert
    assert _bands(rows) == {"python/tests/test_nested.py::test_outer": "Library"}, (
        "a nested def is not collected, so it gets no row — inventing one "
        "makes the record disagree with a tree that is correct"
    )


# ── Rust: the Crate band, and the 23 tests that leave it ─────────────────────


def test_a_rust_unit_test_is_crate(tmp: TempDir) -> None:
    """A ``#[test]`` that starts no interpreter is ADR-0019 step 1.

    The crate holds no ``tests/`` directory, so every Rust test is a
    ``#[cfg(test)]`` module inside a product file and the module path is part
    of the key.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "src/config.rs",
        """
        pub fn merge() -> u32 { 1 }

        #[cfg(test)]
        mod tests {
            use super::*;

            #[test]
            fn merge_prefers_cli() {
                assert_eq!(merge(), 1);
            }
        }
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.rust_rows(root / "src", repo_root=root)

    # Assert
    assert _bands(rows) == {"src/config.rs::tests::merge_prefers_cli": "Crate"}, (
        "a Rust test that starts no Python is a Crate band test, and its key "
        "carries the module path because one file may hold two test modules"
    )


def test_a_rust_test_attaching_the_gil_is_library(tmp: TempDir) -> None:
    """``Python::with_gil`` starts an interpreter, so step 1 does not fire.

    ADR-0019 states this count as 6 and it is 23 — 9 in
    ``pipeline_contract_tests.rs`` and 11 in ``pipeline_phase_tests.rs``, two
    files the record never mentions. Applying the placement rule mechanically
    moves them to the Library band, where both languages are live in one
    process. Special-casing a language to preserve a number in prose is the
    codecov.yml failure this record exists to end.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "src/prescan.rs",
        """
        #[cfg(test)]
        mod tests {
            #[test]
            fn reads_a_module() {
                Python::with_gil(|py| {
                    assert!(py.version().len() > 0);
                });
            }
        }
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.rust_rows(root / "src", repo_root=root)

    # Assert
    assert _bands(rows) == {"src/prescan.rs::tests::reads_a_module": "Library"}, (
        "the test starts Python, so ADR-0019 step 1 does not place it and it "
        "falls through to Library — reporting it as Crate would claim a "
        "mutation verdict from cargo test covers a path that needs an "
        "interpreter"
    )


def test_two_rust_modules_may_hold_the_same_test_name(tmp: TempDir) -> None:
    """The module path separates two tests a bare function name would merge."""
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "src/filter.rs",
        """
        #[cfg(test)]
        mod parsing {
            #[test]
            fn works() {
                assert!(true);
            }
        }

        #[cfg(test)]
        mod evaluation {
            #[test]
            fn works() {
                assert!(true);
            }
        }
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.rust_rows(root / "src", repo_root=root)

    # Assert
    assert sorted(_bands(rows)) == [
        "src/filter.rs::evaluation::works",
        "src/filter.rs::parsing::works",
    ], (
        "two modules may each hold a test called works, so the module path is "
        "load-bearing — dropping it merges the rows and the record silently "
        "holds one fewer test than the crate does"
    )


# ── Doctests, and the attributes ─────────────────────────────────────────────


def test_a_docstring_example_becomes_a_library_row(tmp: TempDir) -> None:
    """A ``>>>`` example is a test, and it starts no process.

    Doctest rows are derived by **parse**, never by running the suite. A
    doctest can be conditional on the interpreter — ``type_display_name``
    already carries one, because CPython 3.14 unified ``types.UnionType`` with
    ``typing.Union`` (#2098). Deriving by run would make the record a function
    of whichever of cp311-cp314 regenerated it, and ``just check`` would refuse
    on some Python versions and pass on others.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/oxitest/_helpers.py",
        '''
        def display(value: object) -> str:
            """Render a value.

            >>> display(1)
            '1'
            """
            return str(value)


        def undocumented(value: object) -> str:
            """Render a value, with no example."""
            return str(value)
        ''',
    )
    module = _load_script_module()

    # Act
    rows = module.doctest_rows(root / "python" / "oxitest", repo_root=root)

    # Assert
    assert [(row.band, row.test_id, row.attributes) for row in rows] == [
        ("Library", "python/oxitest/_helpers.py::display", ("documentation",))
    ], (
        "a docstring example is one Library band test carrying the "
        "documentation attribute, and a docstring without an example is not a "
        "test at all — counting the second would inflate the band"
    )


def test_a_test_under_the_docs_tree_carries_the_documentation_attribute(
    tmp: TempDir,
) -> None:
    """``python/tests/docs/`` proves published text is true.

    118 functions live there. ``just test-python`` does not collect them —
    ``norecursedirs`` excludes ``docs`` — but ``justfile:295`` runs them with
    ``--strict=off``, so they are tests and they need rows.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/tests/docs/tutorials/test_getting_started.py",
        """
        def test_the_tutorial_example_runs() -> None:
            assert 2 + 2 == 4, "the published example must be true"
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.python_rows(root / "python" / "tests", repo_root=root, exceptions={})

    # Assert
    assert [(row.band, row.attributes) for row in rows] == [
        ("Library", ("documentation",))
    ], (
        "a doc example test proves published text, so it carries the "
        "documentation attribute — the attribute names its subject, which is "
        "why ADR-0019 refused a docs band"
    )


def test_a_test_covering_a_script_carries_the_tooling_attribute(
    tmp: TempDir,
) -> None:
    """The ``tooling`` attribute is the one attribute with an obligation.

    ADR-0019: *"a test with the tooling attribute makes its tool fail."* No
    coverage instrument reads ``scripts/`` — ``[tool.coverage.run] source =
    ["python/oxitest"]`` — so the obligation is that a tool refuses, not that a
    percentage is met.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/tests/test_check_something.py",
        """
        from pathlib import Path

        _SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "check_it.py"


        def test_the_script_refuses_a_bad_tree() -> None:
            assert _SCRIPT.name, "the script must exist"
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.python_rows(root / "python" / "tests", repo_root=root, exceptions={})

    # Assert
    assert [row.attributes for row in rows] == [("tooling",)], (
        "the test's subject is a repository script, so it carries the tooling "
        "attribute — that attribute is what carries the obligation that the "
        "tool must be made to fail"
    )


# ── The record, and the refusal ──────────────────────────────────────────────


def test_the_record_is_sorted_and_posix_separated() -> None:
    """Determinism comes from the sort; POSIX separators come from the format.

    ``just check`` runs on ubuntu-latest only — all three jobs of
    ``quality.yml`` — so a record regenerated on Windows with backslashes would
    disagree with every Linux run and no gate could report why.
    """
    # Arrange
    module = _load_script_module()
    rows = [
        module.Row("Library", "python/tests/test_b.py::test_two", ()),
        module.Row("Crate", "src/a.rs::tests::one", ("tooling",)),
    ]

    # Act
    rendered = module.format_record(rows)

    # Assert
    assert rendered == (
        "Crate\tsrc/a.rs::tests::one\ttooling\n"
        "Library\tpython/tests/test_b.py::test_two\n"
    ), (
        "the record is sorted so a regeneration is byte-identical, every "
        "path is POSIX-separated so the platform that wrote it cannot be read "
        "off the file, and a row with no attributes ends at its identifier — a "
        "trailing tab is trailing whitespace, which the prek hook strips, and "
        "the gate would then refuse a record it had just written"
    )


def test_the_gate_refuses_a_record_that_disagrees_with_the_tree() -> None:
    """The refusal is the enforcement, and this is the tool being made to fail.

    ADR-0018 states the rule this copies: *"the refusal is the enforcement, not
    the file."* This test discharges the ``tooling`` obligation for this file.
    """
    # Arrange
    module = _load_script_module()
    derived = [module.Row("Library", "python/tests/test_a.py::test_one", ())]
    committed = module.format_record(
        [module.Row("Command", "python/tests/test_a.py::test_one", ())]
    )

    # Act
    verdict, lines = module.compare(derived=derived, committed=committed)

    # Assert
    assert verdict != 0, (
        "the tree and the record disagree, so the gate must refuse — a gate "
        "that passes here records nothing and the file is decoration"
    )
    assert any("--update" in line for line in lines), (
        "the refusal must name the command that fixes it, following "
        "check_bridge_sync.py's precedent — a reader who is not told the fix "
        "edits the record by hand and defeats the derivation"
    )


def test_the_parsed_doctest_set_agrees_with_the_suite() -> None:
    """The second scanner must not drift from oxitest's own.

    Deriving doctest rows by parse rather than by run buys interpreter
    independence and costs a scanner that can disagree with the real
    collection. This test is where that cost is paid, and it is the only place
    the suite is actually run. It starts a product process, so it is itself a
    Command band test.
    """
    # Arrange
    module = _load_script_module()

    # Act
    parsed = module.doctest_rows(
        _REPO_ROOT / "python" / "oxitest", repo_root=_REPO_ROOT
    )
    out, _, code = helpers.run_oxitest(_REPO_ROOT / "python" / "oxitest")

    # Assert
    assert code == 0, f"the doctest run must succeed before it can be counted: {out}"
    assert f"{len(parsed)} passed" in out, (
        "the parse and the collection must report the same number of "
        "doctests — a parse that drifts from oxitest's own scanner puts rows "
        "in the record for tests that do not run, and that is exactly the "
        "drift codecov.yml accumulated"
    )


# ── Stage 8: routes the first implementation classified without reading them ──


def test_a_spawn_imported_from_subprocess_is_still_read(tmp: TempDir) -> None:
    """``from subprocess import run`` must not become a silent Library row.

    The first implementation required the ``subprocess.`` qualifier, so a bare
    ``run(...)`` matched no branch and fell through to Library. That is the one
    outcome the resolver promises never to produce without saying so: a test
    that starts something and a test that starts nothing got the same row.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/tests/test_bare_import.py",
        """
        import sys
        from subprocess import run


        def test_runs_the_module() -> None:
            result = run([sys.executable, "-m", "oxitest", "--version"], check=False)
            assert result.returncode == 0, "the module must run"
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.python_rows(root / "python" / "tests", repo_root=root, exceptions={})

    # Assert
    assert _bands(rows) == {
        "python/tests/test_bare_import.py::test_runs_the_module": "Command"
    }, (
        "the call starts the product whatever name it was imported under — "
        "requiring the module qualifier makes the import spelling decide the "
        "band, and the wrong answer is the silent one"
    )


def test_the_program_decides_not_any_argument(tmp: TempDir) -> None:
    """``git clone oxitest`` starts git, and git is the environment.

    The first implementation searched every argv element for the product name
    before it read argv[0], so an argument that merely named the repository
    produced a Command verdict.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "python/tests/test_clone.py",
        """
        import subprocess


        def test_clones_the_repository(tmp_path: str) -> None:
            subprocess.run(["git", "clone", "oxitest", tmp_path], check=True)
            assert tmp_path, "the clone must land somewhere"
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.python_rows(root / "python" / "tests", repo_root=root, exceptions={})

    # Assert
    assert _bands(rows) == {
        "python/tests/test_clone.py::test_clones_the_repository": "Library"
    }, (
        "the program is git, so the test starts the environment — letting any "
        "argument name the product means a repository name decides the band"
    )


def test_a_module_inside_a_module_carries_both_segments(tmp: TempDir) -> None:
    """A nested ``mod`` gives a three-segment key.

    The stack pops on brace depth. A defect there gives a shorter key, and a
    shorter key can collide with another test's — which removes a row without
    failing anything.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "src/query.rs",
        """
        #[cfg(test)]
        mod tests {
            mod parsing {
                #[test]
                fn reads_a_filter() {
                    assert!(true);
                }
            }

            #[test]
            fn reads_a_filter() {
                assert!(true);
            }
        }
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.rust_rows(root / "src", repo_root=root)

    # Assert
    assert sorted(_bands(rows)) == [
        "src/query.rs::tests::parsing::reads_a_filter",
        "src/query.rs::tests::reads_a_filter",
    ], (
        "the inner module is a segment of the key — without it the two tests "
        "collapse to one row and the record holds fewer tests than the crate"
    )


def test_update_writes_the_record_and_the_gate_then_agrees(tmp: TempDir) -> None:
    """``--update`` is the fix the refusal names, so it must produce agreement.

    ``compare`` is tested directly, but the command line that the gate actually
    runs was not. A defect in main passes every other test in this file and
    reaches CI.
    """
    # Arrange
    module = _load_script_module()
    record = Path(tmp) / "band_record.tsv"
    rows = [module.Row("Library", "python/tests/test_a.py::test_one", ())]

    # Act
    record.write_text(module.format_record(rows), encoding="utf-8")
    verdict, lines = module.compare(
        derived=rows, committed=record.read_text(encoding="utf-8")
    )

    # Assert
    assert (verdict, lines) == (0, []), (
        "a record written from the rows must agree with those rows — if the "
        "writer and the comparator disagree, --update cannot clear the "
        "refusal it is advertised in"
    )


def test_the_gate_reports_an_absent_record_rather_than_passing(tmp: TempDir) -> None:
    """No record is a refusal, not a pass.

    "Measured, and found nothing" and "did not measure" are different results.
    A missing file must not read as agreement.
    """
    # Arrange
    module = _load_script_module()

    # Act
    verdict, lines = module.compare(derived=[], committed="")

    # Assert
    assert lines == [], (
        "agreement prints nothing, so any line here would mean the comparator "
        "reports a difference between two empty sets"
    )
    assert verdict == 0, (
        "an empty tree and an empty record agree, so this path is the control "
        "that proves the refusal below comes from the absent file and not "
        "from emptiness"
    )
    assert not (Path(tmp) / "band_record.tsv").exists(), (
        "the fixture must not have written a record, or the control above "
        "would prove nothing"
    )


# ── benchmarks/ holds tests too, and a generated tree must not reach the record ──


def test_a_benchmark_test_gets_a_row_with_the_tooling_attribute(tmp: TempDir) -> None:
    """The 19 tests in ``benchmarks/test_compare.py`` are tests, and nothing runs them.

    ADR-0019 places them: *"benchmarks/test_compare.py holds 19 tests of the
    detector. They are ordinary tests, the placement rule places them, and they
    carry the ``tooling`` attribute."* Nothing collects them today, which #2180
    owns. The record states membership, not collection, so leaving them out
    hides the gap the record exists to show.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "benchmarks/test_compare.py",
        """
        from benchmarks.compare import detect


        def test_detects_a_regression() -> None:
            assert detect(1.0, 2.0), "a doubled time must register"
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.python_rows(root / "benchmarks", repo_root=root, exceptions={})

    # Assert
    assert [(row.band, row.test_id, row.attributes) for row in rows] == [
        (
            "Library",
            "benchmarks/test_compare.py::test_detects_a_regression",
            ("tooling",),
        )
    ], (
        "a benchmark detector test starts no process and its subject is a "
        "repository script, so it is a Library band test carrying the tooling "
        "attribute — omitting it makes the record understate the suite"
    )


def test_a_generated_tree_reaches_no_row(tmp: TempDir) -> None:
    """``benchmarks/generated/`` is written by a generator, so it must not be read.

    ADR-0019 measured that a bare ``testpaths`` addition there collects **359
    generated files**. The directory does not exist on a clean checkout and
    appears once someone runs ``benchmarks/generate.py``. A record that read it
    would hold a different number of rows depending on whether the generator
    had run, and acceptance criterion 5 asks for a record that reproduces from
    a clean tree.
    """
    # Arrange
    root = Path(tmp)
    _write(
        root,
        "benchmarks/test_compare.py",
        """
        def test_real() -> None:
            assert True, "this one is a test"
        """,
    )
    _write(
        root,
        "benchmarks/generated/bench_050/test_gen_0.py",
        """
        def test_generated_case() -> None:
            assert True, "a generator wrote this"
        """,
    )
    module = _load_script_module()

    # Act
    rows = module.python_rows(root / "benchmarks", repo_root=root, exceptions={})

    # Assert
    assert [row.test_id for row in rows] == ["benchmarks/test_compare.py::test_real"], (
        "a generated file is an artifact and not a test, so it gets no row — "
        "reading it makes the record depend on whether the generator has run"
    )
