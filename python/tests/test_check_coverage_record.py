"""Tests for the coverage obligation record checker.

``scripts/check_coverage_record.py`` is a gate in ``just check``. ADR-0019
replaces ``codecov.yml``'s hand-written ``ignore:`` list with a record whose
completeness refuses, and this script is the thing that refuses.

A gate that cannot fail is worse than no gate, because it reads as coverage.
Three mutants on the committed record proved the refusals fire, but a mutant can
only reach a case the record already contains. These tests reach the cases it
does not: an unknown state, a malformed row, and a ``codecov.yml`` with no
``ignore:`` block.

The load-bearing test is ``test_a_region_with_no_row_is_refused``. The record
holds a row for every product region rather than only the excluded ones, because
a record of exclusions can never reach ``unowned`` and its gate would be
unreachable. That is the one failure this record catches which a reader of
``codecov.yml`` could not, so it is the one that must be pinned.

This file carries the ``tooling`` attribute, whose one obligation is that a test
with it makes its tool fail. Every ``refuses`` test here discharges it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from oxitest import TempDir

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_coverage_record.py"


def _load_script_module() -> ModuleType:
    """Load the checker as a module so its functions can be called directly.

    ``scripts/`` is not a package. The module is registered in ``sys.modules``
    before it executes because ``@dataclass`` resolves
    ``sys.modules[cls.__module__]`` while processing the class, and an
    unregistered module makes that lookup return ``None``.
    """
    name = "check_coverage_record_under_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT_PATH)
    if spec is None or spec.loader is None:
        msg = f"could not load module spec from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _row(module: ModuleType, state: str, region: str, reason: str = "-") -> object:
    """Build one record row with the instrument its path implies."""
    instrument = "rust" if region.startswith("src/") else "python"
    return module.Row(state, region, instrument, reason)


# ── The completeness gate ────────────────────────────────────────────────────


def test_a_region_with_no_row_is_refused() -> None:
    """The tree holding a region the record does not is the gate's whole point."""
    # Arrange
    module = _load_script_module()
    regions = {"src/a.rs": "rust", "src/b.rs": "rust"}
    rows = [_row(module, "measured", "src/a.rs")]

    # Act
    problems = module.validate(rows, regions)

    # Assert
    assert any("src/b.rs" in problem for problem in problems), (
        "a product file with no row must be refused by name; without this the "
        "record can only ever describe the exclusions somebody remembered, "
        f"which is the defect it replaces. Got: {problems}"
    )


def test_a_row_for_a_deleted_region_is_refused() -> None:
    """The record holding a region the tree does not is the inverse failure."""
    # Arrange
    module = _load_script_module()
    regions = {"src/a.rs": "rust"}
    rows = [
        _row(module, "measured", "src/a.rs"),
        _row(module, "measured", "src/gone.rs"),
    ]

    # Act
    problems = module.validate(rows, regions)

    # Assert
    assert any("src/gone.rs" in problem for problem in problems), (
        "a row whose file was deleted is the way the old ignore: list rotted — "
        "it carried import_graph.py for months after 5e75a5c3 removed it. "
        f"Got: {problems}"
    )


def test_a_clean_record_is_accepted() -> None:
    """The control. Without it every refusal above could be unconditional."""
    # Arrange
    module = _load_script_module()
    regions = {"src/a.rs": "rust", "python/oxitest/b.py": "python"}
    rows = [
        _row(module, "measured", "src/a.rs"),
        _row(module, "exempt", "python/oxitest/b.py", "no instrumented line"),
    ]

    # Act
    problems = module.validate(rows, regions)

    # Assert
    assert problems == [], (
        f"a record that accounts for every region must pass, or the gate "
        f"refuses every branch and nobody can land one. Got: {problems}"
    )


# ── The state rules ──────────────────────────────────────────────────────────


def test_an_unowned_row_is_refused() -> None:
    """`unowned` is the refusing state, and nothing else in the file says so."""
    # Arrange
    module = _load_script_module()
    regions = {"src/a.rs": "rust"}
    rows = [_row(module, "unowned", "src/a.rs")]

    # Act
    problems = module.validate(rows, regions)

    # Assert
    assert any("unowned" in problem for problem in problems), (
        "`--update` writes a new region as unowned, so a gate that accepts it "
        f"lets an undecided region land silently. Got: {problems}"
    )


def test_an_unknown_state_is_refused() -> None:
    """An invented state must not read as one the checker silently ignores."""
    # Arrange
    module = _load_script_module()
    regions = {"src/a.rs": "rust"}
    rows = [_row(module, "partial", "src/a.rs")]

    # Act
    problems = module.validate(rows, regions)

    # Assert
    assert any("partial" in problem for problem in problems), (
        "an unrecognised state must be named, not skipped. A state like this is "
        "the kind a future author invents, and a skipped row leaves a region "
        f"with no state at all. Got: {problems}"
    )


def test_an_exempt_row_without_a_reason_is_refused() -> None:
    """An exemption with no reason is the claim codecov.yml made and never proved."""
    # Arrange
    module = _load_script_module()
    regions = {"src/a.rs": "rust"}
    rows = [_row(module, "exempt", "src/a.rs", "-")]

    # Act
    problems = module.validate(rows, regions)

    # Assert
    assert any("needs a reason" in problem for problem in problems), (
        "the reason is what separates a recorded judgement from a bare label, "
        f"and the no-reason marker is exactly the omission. Got: {problems}"
    )


def test_a_measured_row_carrying_a_reason_is_refused() -> None:
    """A reason nothing acts on is the shape this record replaces."""
    # Arrange
    module = _load_script_module()
    regions = {"src/a.rs": "rust"}
    rows = [_row(module, "measured", "src/a.rs", "covered by the integration tests")]

    # Act
    problems = module.validate(rows, regions)

    # Assert
    assert any("must carry" in problem for problem in problems), (
        "prose on a measured row is unread and drifts, which is how the old "
        f"ignore: list came to describe a repository nobody made. Got: {problems}"
    )


def test_an_instrument_that_disagrees_with_the_path_is_refused() -> None:
    """A Python instrument on a Rust path would send the CI half to the wrong tool."""
    # Arrange
    module = _load_script_module()
    regions = {"src/a.rs": "rust"}
    rows = [module.Row("measured", "src/a.rs", "python", "-")]

    # Act
    problems = module.validate(rows, regions)

    # Assert
    assert any("instrument" in problem for problem in problems), (
        f"the instrument decides which report answers for the region, so a "
        f"wrong one makes a measured claim unverifiable. Got: {problems}"
    )


# ── Parsing ──────────────────────────────────────────────────────────────────


def test_a_row_with_the_wrong_field_count_is_refused() -> None:
    """A stripped trailing tab turns four fields into three, and it is silent."""
    # Arrange
    module = _load_script_module()
    text = "state\tregion\tinstrument\treason\nmeasured\tsrc/a.rs\trust\n"

    # Act
    _rows, problems = module.parse_record(text)

    # Assert
    assert any("tab-separated" in problem for problem in problems), (
        "prek's trailing-whitespace hook strips the tab a row with no reason "
        "would end in, so this is the failure the no-reason marker exists to "
        f"prevent, and it must be loud. Got: {problems}"
    )


def test_a_wrong_header_is_refused() -> None:
    """A reordered header would silently swap the meaning of every column."""
    # Arrange
    module = _load_script_module()
    text = "region\tstate\tinstrument\treason\nsrc/a.rs\tmeasured\trust\t-\n"

    # Act
    _rows, problems = module.parse_record(text)

    # Assert
    assert any("header" in problem for problem in problems), (
        f"swapping state and region parses cleanly and records nonsense, so "
        f"the header is checked rather than assumed. Got: {problems}"
    )


# ── Emission ─────────────────────────────────────────────────────────────────


def test_ignore_emits_exempt_and_not_uncovered() -> None:
    """The distinction the fourth state exists for, asserted rather than described."""
    # Arrange
    module = _load_script_module()
    rows = [
        _row(module, "exempt", "src/entry.rs", "PyO3 entry point"),
        _row(module, "uncovered", "src/untested.rs", "0.0% of 43 lines"),
        _row(module, "measured", "src/tested.rs"),
    ]

    # Act
    emitted = module.emit_ignore(rows)

    # Assert
    assert "src/entry.rs" in emitted, (
        f"an exempt region has lines no instrument can cover, so counting them "
        f"distorts the denominator. Got:\n{emitted}"
    )
    assert "src/untested.rs" not in emitted, (
        "an uncovered region is real untested product code. Hiding it from a "
        "report that refuses nothing only flatters the number, which is the "
        f"anti-pattern CLAUDE.md names. Got:\n{emitted}"
    )
    assert "src/tested.rs" not in emitted, (
        f"a measured region belongs in the report by definition. Got:\n{emitted}"
    )


def test_split_codecov_returns_the_block_with_its_header() -> None:
    """Emitted and committed text are compared, so both need the header."""
    # Arrange
    module = _load_script_module()
    document = "coverage:\n  status: {}\n\n" + module.IGNORE_HEADER + '  - "src/a.rs"\n'

    # Act
    _head, tail = module.split_codecov(document)

    # Assert
    assert tail == module.IGNORE_HEADER + '  - "src/a.rs"\n', (
        "leaving the comment lines in the head compares a block carrying its "
        "header against one without, which can never match and reports a stale "
        f"codecov.yml forever. Got:\n{tail!r}"
    )


def test_a_codecov_without_an_ignore_block_yields_an_empty_tail() -> None:
    """The file may legitimately have no exclusions, and that must not crash."""
    # Arrange
    module = _load_script_module()

    # Act
    head, tail = module.split_codecov("coverage:\n  status: {}\n")

    # Assert
    assert tail == "", (
        f"an absent block is an empty tail, which compares equal to an emitter "
        f"that produced nothing. Got: {tail!r}"
    )
    assert head.startswith("coverage:"), (
        f"the head must survive intact, or --update would truncate the file it "
        f"rewrites. Got: {head!r}"
    )


# ── Region discovery ─────────────────────────────────────────────────────────


def test_both_spellings_of_a_test_only_module_are_excluded(tmp: TempDir) -> None:
    """`src/` is about half test code, and the #[path] spelling is easy to miss."""
    # Arrange
    module = _load_script_module()
    root = Path(tmp)
    source = root / "src"
    source.mkdir()
    (source / "lib.rs").write_text(
        "#[cfg(test)]\nmod plain_helper;\n\n"
        '#[cfg(test)]\n#[path = "redirected_helper.rs"]\nmod aliased;\n\n'
        "pub fn product() {}\n",
        encoding="utf-8",
    )
    (source / "plain_helper.rs").write_text("pub fn helper() {}\n", encoding="utf-8")
    (source / "redirected_helper.rs").write_text(
        "pub fn other() {}\n", encoding="utf-8"
    )

    # Act
    excluded = module.test_only_modules(root)

    # Assert
    assert "src/plain_helper.rs" in excluded, (
        f"a plain #[cfg(test)] mod is test code and must not become a region. "
        f"Got: {excluded}"
    )
    assert "src/redirected_helper.rs" in excluded, (
        "the #[path] attribute sits between the gate and the declaration, so a "
        "pattern expecting them adjacent misses it — and this crate uses that "
        f"form three times. Got: {excluded}"
    )
    assert "src/lib.rs" not in excluded, (
        f"the file holding the declarations is product code itself. Got: {excluded}"
    )


def test_a_test_only_module_is_not_a_product_region(tmp: TempDir) -> None:
    """The exclusion has to reach product_regions, not only its own helper."""
    # Arrange
    module = _load_script_module()
    root = Path(tmp)
    source = root / "src"
    source.mkdir()
    (source / "lib.rs").write_text(
        "#[cfg(test)]\nmod doubles;\n\npub fn product() {}\n", encoding="utf-8"
    )
    (source / "doubles.rs").write_text("pub fn fake() {}\n", encoding="utf-8")

    # Act
    regions = module.product_regions(root)

    # Assert
    assert "src/doubles.rs" not in regions, (
        "counting a test file as a region would put it in codecov.yml and let a "
        f"reader believe the record covers a surface it does not. Got: {regions}"
    )
    assert "src/lib.rs" in regions, (
        f"the product file beside it must still be a region. Got: {regions}"
    )
