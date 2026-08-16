"""Tests for the platform-set check in ``check_platform_sets.py``.

Three files encode oxitest's platform set — ``test.yml``'s required rollup,
``publish.yml``'s wheel targets and ``pyproject.toml``'s classifiers — and
until #1950 nothing compared them (#1946). ADR-0013 makes the tested set the
definition and the other two derived; this checker holds that derivation.

The four checks are independent and each is exercised here separately, because
a set comparison that is really being satisfied by a neighbouring check reads
exactly like one that works. The parser tests matter for the same reason: the
three files spell a platform three ways, so every comparison is only as good as
the mapping into the canonical ``(os, arch)`` identity.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import oxitest as oxi
from oxitest import TempDir

# ── Script location ──────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_platform_sets.py"


def _load_script_module() -> ModuleType:
    """Load ``scripts/check_platform_sets.py`` as a module.

    The scripts directory is not a package, so this uses ``importlib.util``.
    The ``sys.modules`` registration is load-bearing: the script defines a
    ``@dataclass``, and ``dataclasses._process_class`` resolves the defining
    module through ``sys.modules``.
    """
    spec = importlib.util.spec_from_file_location(
        "check_platform_sets_under_test", _SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _agreeing_sets(
    module: ModuleType,
) -> tuple[set[str], set[str], set[str], set[str]]:
    """Three sets built from ``PLATFORMS``, so they agree by construction.

    Derived from the table rather than read from the three files. Reading the
    files was the first shape of this helper and it coupled every drift case to
    the repository's current platform set: mutating ``test.yml`` to drop the
    Windows job killed the mutant *and* knocked out an unrelated drift case,
    because that case's expected message depends on Windows being tested. A
    baseline that moves with the tree cannot isolate a single drift.

    Deriving it from ``PLATFORMS`` also keeps it correct when a platform is
    added, which a literal baseline would not — that would go stale silently
    and start passing vacuously. The real files are covered by
    ``test_script_exits_0_on_this_repo``.
    """
    needs = {job for platform in module.PLATFORMS for job in platform.test_jobs}
    needs |= set(module.NON_PLATFORM_JOBS)
    targets = {platform.publish_target for platform in module.PLATFORMS}
    classifiers = {platform.classifier for platform in module.PLATFORMS}
    gated = {platform.canonical for platform in module.PLATFORMS}
    return needs, targets, classifiers, gated


# ── parsing `publish.yml` ────────────────────────────────────────────────────


def test_wheel_targets_reads_matrix_and_literal_step_targets() -> None:
    """A target reaches maturin two ways and both must be seen.

    ``publish.yml``'s Linux job interpolates ``${{ matrix.target }}`` while the
    macOS and Windows jobs pass a literal. A parser that handled only one form
    would report a smaller shipped set than the file declares, and a smaller
    set compares equal to a smaller tested set without either being right.

    **The equality is exact on purpose, and that is load-bearing.** It is also
    what proves the interpolation is *not* admitted: a parser that took
    ``${{ matrix.target }}`` as a token would produce a four-member set here
    and fail. Relaxing this to a subset check silently drops that half.
    """
    # Arrange
    module = _load_script_module()
    workflow = module.load_yaml(
        textwrap.dedent("""
            name: probe
            on: push
            jobs:
              matrixed:
                strategy:
                  matrix:
                    target: [x86_64, aarch64]
                steps:
                  - uses: PyO3/maturin-action@v1
                    with:
                      target: ${{ matrix.target }}
              literal:
                steps:
                  - uses: PyO3/maturin-action@v1
                    with:
                      target: universal2-apple-darwin
        """)
    )

    # Act
    targets = module.wheel_targets(workflow)

    # Assert
    assert targets == {"x86_64", "aarch64", "universal2-apple-darwin"}, (
        "an unread target spelling shrinks the shipped set silently, and the "
        "comparison against the tested set then passes on a platform nobody "
        "checked"
    )


def test_wheel_targets_ignores_jobs_that_build_no_wheel() -> None:
    """The sdist job runs maturin with ``command: sdist`` and no target.

    A job that ships no wheel must contribute nothing, or the shipped set grows
    entries the tested set can never match.
    """
    # Arrange
    module = _load_script_module()
    workflow = module.load_yaml(
        textwrap.dedent("""
            name: probe
            on: push
            jobs:
              sdist:
                steps:
                  - uses: PyO3/maturin-action@v1
                    with:
                      command: sdist
                      args: --out dist
              publish:
                needs: [sdist]
                steps:
                  - uses: pypa/gh-action-pypi-publish@release/v1
        """)
    )

    # Act
    targets = module.wheel_targets(workflow)

    # Assert
    assert targets == set(), (
        "only a maturin step with a target ships a wheel; counting anything "
        "else puts a phantom platform in the shipped set"
    )


# ── parsing `test.yml` and `pyproject.toml` ──────────────────────────────────


def test_rollup_needs_ignores_jobs_outside_a_required_rollup() -> None:
    """Only a ``* (required)`` job's ``needs:`` confers support.

    ADR-0013 Rule 1 needs both halves: a job can run without being in the
    rollup, and such a job may be red indefinitely. Reading every job's
    ``needs:`` would let an advisory job declare a platform supported.
    """
    # Arrange
    module = _load_script_module()
    workflow = module.load_yaml(
        textwrap.dedent("""
            name: probe
            on: push
            jobs:
              advisory:
                name: Test (Experimental)
                needs: [changes]
                steps:
                  - run: echo x
              gate:
                name: Tests (required)
                needs: [changes, windows]
                steps:
                  - run: echo "${{ needs.windows.result }}"
        """)
    )

    # Act
    needs = module.rollup_needs(workflow)

    # Assert
    assert needs == {"changes", "windows"}, (
        "a platform job outside the rollup can stay red without blocking "
        "anything, so it must not confer support"
    )


def test_os_classifiers_ignores_every_other_classifier() -> None:
    """Only ``Operating System ::`` entries are part of the platform promise."""
    # Arrange
    module = _load_script_module()
    pyproject = {
        "project": {
            "classifiers": [
                "Operating System :: POSIX :: Linux",
                "License :: OSI Approved :: MIT License",
                "Topic :: Software Development :: Testing",
            ]
        }
    }

    # Act
    classifiers = module.os_classifiers(pyproject)

    # Assert
    assert classifiers == {"Operating System :: POSIX :: Linux"}, (
        "a licence or topic classifier says nothing about platforms; dragging "
        "it into the comparison makes the gate red on a correct file"
    )


# ── the four checks, one at a time ───────────────────────────────────────────


@dataclass(frozen=True)
class DriftCase:
    """One way the three declarations can disagree, and the words that prove it."""

    drop_job: str | None
    add_target: str | None
    drop_classifier: str | None
    add_job: str | None
    expected: str


@oxi.parametrize(
    undeclared_wheel_target=DriftCase(
        drop_job=None,
        add_target="windows-11-arm",
        drop_classifier=None,
        add_job=None,
        expected="wheel target `windows-11-arm` is not in PLATFORMS",
    ),
    undeclared_rollup_job=DriftCase(
        drop_job=None,
        add_target=None,
        drop_classifier=None,
        add_job="freebsd",
        expected="job `freebsd` is in the required rollup",
    ),
    shipped_but_not_tested=DriftCase(
        drop_job="windows",
        add_target=None,
        drop_classifier=None,
        add_job=None,
        expected="windows-x86_64 ships a wheel but no required job tests it",
    ),
    tested_but_not_promised=DriftCase(
        drop_job=None,
        add_target=None,
        drop_classifier="Operating System :: Microsoft :: Windows",
        add_job=None,
        expected="`Operating System :: Microsoft :: Windows` is missing",
    ),
)
def test_each_drift_is_reported(case: DriftCase) -> None:
    """Each of the four disagreement classes fires on its own.

    They are checked one at a time because a comparison satisfied by a
    neighbouring check reads exactly like one that works — and check 1 exists
    precisely because an entry the table cannot see is invisible to checks 2
    and 3 rather than caught by them.
    """
    # Arrange
    module = _load_script_module()
    needs, targets, classifiers, gated = _agreeing_sets(module)
    if case.drop_job is not None:
        needs = needs - {case.drop_job}
    if case.add_job is not None:
        needs = needs | {case.add_job}
    if case.add_target is not None:
        targets = targets | {case.add_target}
    if case.drop_classifier is not None:
        classifiers = classifiers - {case.drop_classifier}

    # Act
    problems = module.check(needs, targets, classifiers, gated=gated)

    # Assert
    assert any(case.expected in problem for problem in problems), (
        f"the drift must be reported in words the reader can act on without "
        f"re-deriving the rule; expected {case.expected!r} among {problems!r}"
    )


def test_empty_target_set_is_refused_rather_than_passing_vacuously() -> None:
    """A parser that returns nothing must fail, not satisfy every comparison.

    This is the precedent named in #1946 and set by
    ``test_no_builtin_is_reachable_by_shortcut``: *"the assertion below would
    hold vacuously and stop guarding anything"*.
    """
    # Arrange
    module = _load_script_module()
    needs, _, classifiers, gated = _agreeing_sets(module)

    # Act
    problems = module.check(needs, set(), classifiers, gated=gated)

    # Assert
    assert any("no wheel target was found" in problem for problem in problems), (
        f"an empty shipped set makes every comparison hold for free, so the "
        f"gate would pass having read nothing; got {problems!r}"
    )


def test_stale_allowlist_entry_is_refused() -> None:
    """An exemption for a job that no longer exists is an exemption nobody re-reads.

    The vacuity guard runs in both directions: over-matching and under-matching
    are the same hazard, and a set comparison catches neither.
    """
    # Arrange
    module = _load_script_module()
    needs, targets, classifiers, gated = _agreeing_sets(module)

    # Act
    problems = module.check(
        needs - {"tmpdir-symlink"}, targets, classifiers, gated=gated
    )

    # Assert
    assert any("NON_PLATFORM_JOBS exempts" in problem for problem in problems), (
        f"a standing exemption for a deleted job silently widens what the gate "
        f"tolerates; got {problems!r}"
    )


# ── check 5: the Distribution band gate installs every shipped platform ──────


def test_gate_platforms_reads_the_matrix_runner_axis() -> None:
    """The gate names runners; PLATFORMS maps each one to a canonical name."""
    # Arrange
    module = _load_script_module()
    workflow = module.load_yaml(
        textwrap.dedent("""
            jobs:
              gate:
                strategy:
                  matrix:
                    runner: [ubuntu-latest, windows-latest]
                    python-version: ['3.12']
        """)
    )

    # Act
    canonical = module.gate_platforms(workflow)

    # Assert
    assert canonical == {"linux-x86_64", "windows-x86_64"}, (
        "the gate matrix names runners and PLATFORMS names canonical "
        f"platforms; without the mapping the two sets can never compare; "
        f"got {canonical!r}"
    )


def test_an_unknown_gate_runner_is_returned_verbatim() -> None:
    """A runner PLATFORMS cannot see must reach check 1, not vanish."""
    # Arrange
    module = _load_script_module()
    workflow = module.load_yaml(
        textwrap.dedent("""
            jobs:
              gate:
                strategy:
                  matrix:
                    runner: [ubuntu-latest, freebsd-14]
        """)
    )

    # Act
    canonical = module.gate_platforms(workflow)

    # Assert
    assert "freebsd-14" in canonical, (
        "a runner the table does not know must survive into the compared set "
        "— dropping it makes the sets agree and the gate passes on the drift "
        f"it exists to find; got {canonical!r}"
    )


def test_a_gate_that_skips_a_shipped_platform_is_refused() -> None:
    """A wheel nobody installs is a wheel that reaches PyPI unexamined."""
    # Arrange
    module = _load_script_module()
    needs, targets, classifiers, gated = _agreeing_sets(module)

    # Act
    problems = module.check(
        needs, targets, classifiers, gated=gated - {"windows-x86_64"}
    )

    # Assert
    assert any("windows-x86_64" in problem for problem in problems), (
        "the Windows wheel would be uploaded with no install behind it, which "
        "PyPI makes permanent; the check must name the platform the gate "
        f"skipped; got {problems!r}"
    )


def test_an_empty_gate_matrix_is_refused_rather_than_passing_vacuously() -> None:
    """A gate that installs nothing satisfies every comparison for free."""
    # Arrange
    module = _load_script_module()
    needs, targets, classifiers, _ = _agreeing_sets(module)

    # Act
    problems = module.check(needs, targets, classifiers, gated=set())

    # Assert
    assert any("no gate runner was found" in problem for problem in problems), (
        "an empty gate set makes the comparison hold having read nothing, so "
        f"the upload would proceed with no artifact examined; got {problems!r}"
    )


# ── check 6: every interpreter declaration is the same set ───────────────────


def test_interpreter_sets_reads_both_spellings() -> None:
    """An interpreter set is declared two ways and both must be seen.

    ``publish.yml``'s Linux and macOS jobs pass ``-i python3.11 …`` to maturin.
    Its Windows and gate jobs use a ``python-version`` matrix, as ``test.yml``
    does. A parser that read one spelling would compare a smaller set against a
    smaller set and pass on the drift.
    """
    # Arrange
    module = _load_script_module()
    workflow = module.load_yaml(
        textwrap.dedent("""
            jobs:
              linux:
                steps:
                  - uses: PyO3/maturin-action@v1
                    with:
                      args: --release --out dist -i python3.11 python3.12
              windows:
                strategy:
                  matrix:
                    python-version: ['3.11', '3.12']
        """)
    )

    # Act
    sets = module.interpreter_sets(workflow)

    # Assert
    assert sets == {"linux": {"3.11", "3.12"}, "windows": {"3.11", "3.12"}}, (
        "both spellings state the same fact, and a parser blind to one of them "
        f"cannot compare them; got {sets!r}"
    )


def test_a_job_declaring_no_interpreter_is_absent_rather_than_empty() -> None:
    """An empty set compares equal to nothing and would pass silently."""
    # Arrange
    module = _load_script_module()
    workflow = module.load_yaml(
        textwrap.dedent("""
            jobs:
              sdist:
                steps:
                  - uses: PyO3/maturin-action@v1
                    with:
                      command: sdist
                      args: --out dist
        """)
    )

    # Act
    sets = module.interpreter_sets(workflow)

    # Assert
    assert sets == {}, (
        "the sdist job names no interpreter, and mapping it to an empty set "
        f"would make it disagree with every real declaration; got {sets!r}"
    )


def test_a_build_job_shipping_fewer_interpreters_is_refused() -> None:
    """A tested interpreter with no wheel is a user with no artifact."""
    # Arrange
    module = _load_script_module()

    # Act
    problems = module.check_interpreters(
        {"linux": {"3.11", "3.12", "3.13", "3.14"}, "macos": {"3.11", "3.12", "3.13"}},
        ">=3.11",
    )

    # Assert
    assert any("macos" in problem for problem in problems), (
        "a release that ships no 3.14 macOS wheel while CI tests 3.14 leaves a "
        f"supported interpreter with no artifact; got {problems!r}"
    )


def test_an_interpreter_below_requires_python_is_refused() -> None:
    """A wheel built for a version the metadata forbids installs nowhere."""
    # Arrange
    module = _load_script_module()

    # Act
    problems = module.check_interpreters({"linux": {"3.10"}}, ">=3.11")

    # Assert
    assert any("3.10" in problem for problem in problems), (
        "pip refuses a wheel whose interpreter is outside requires-python, so "
        f"that build job produces an artifact nobody can install; got {problems!r}"
    )


def test_a_requires_python_this_parser_cannot_read_is_refused() -> None:
    """Guessing a floor is worse than refusing one.

    ``removeprefix(">=")`` over ``>=3.11,<4.0`` leaves ``3.11,<4.0``, and a
    version key built by dropping every non-digit part reads ``(3, 0)``. Every
    version above 3.0 then satisfies it, so 3.10 would pass and the check would
    keep reporting success against a floor nobody wrote.
    """
    # Arrange
    module = _load_script_module()

    # Act
    problems = module.check_interpreters({"linux": {"3.10"}}, ">=3.11,<4.0")

    # Assert
    assert any("not a bare" in problem for problem in problems), (
        "the checker must say it cannot read the specifier, because the "
        f"alternative is a silent floor of (3, 0) that 3.10 satisfies; got "
        f"{problems!r}"
    )


def test_no_interpreter_declaration_at_all_is_refused() -> None:
    """A parser that read nothing must fail, not satisfy every comparison."""
    # Arrange
    module = _load_script_module()

    # Act
    problems = module.check_interpreters({}, ">=3.11")

    # Assert
    assert any("no interpreter" in problem for problem in problems), (
        "with nothing read, `all sets are equal` holds over the empty set and "
        f"the gate passes having compared nothing; got {problems!r}"
    )


# ── end to end ───────────────────────────────────────────────────────────────


def test_script_exits_1_on_a_mock_layout_with_a_mismatch(tmp: TempDir) -> None:
    """The full script, as a subprocess, against a mock repo layout."""
    # Arrange
    workflows = tmp / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "test.yml").write_text(
        textwrap.dedent("""
            name: Test
            on: push
            jobs:
              gate:
                name: Tests (required)
                needs: [changes, tmpdir-symlink, rust-tests, python-tests]
                steps:
                  - run: echo "${{ needs.rust-tests.result }}"
        """),
        encoding="utf-8",
    )
    (workflows / "publish.yml").write_text(
        textwrap.dedent("""
            name: Publish
            on: push
            jobs:
              linux:
                strategy:
                  matrix:
                    target: [x86_64, aarch64]
                steps:
                  - uses: PyO3/maturin-action@v1
                    with:
                      target: ${{ matrix.target }}
        """),
        encoding="utf-8",
    )
    (tmp / "pyproject.toml").write_text(
        '[project]\nclassifiers = ["Operating System :: POSIX :: Linux"]\n',
        encoding="utf-8",
    )

    # Act
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--root", str(tmp)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    # Assert
    assert result.returncode == 1, (
        f"linux-aarch64 ships a wheel that no job in this layout tests, so the "
        f"hook must refuse; got {result.returncode} with "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "linux-aarch64" in result.stdout, (
        f"the failure must name the platform that disagrees, or the developer "
        f"cannot act on it; got stdout={result.stdout!r}"
    )


def test_script_output_is_pure_ascii(tmp: TempDir) -> None:
    """Prek pipes stdout, so a non-ASCII byte dies on a cp1252 console.

    One em dash in a sibling checker sank PR #2019's Windows job, and the
    parent's own encoding does not help because the child is what writes.
    """
    # Arrange
    workflows = tmp / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "test.yml").write_text("jobs: {}\n", encoding="utf-8")
    (workflows / "publish.yml").write_text("jobs: {}\n", encoding="utf-8")
    (tmp / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    # Act
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--root", str(tmp)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    # Assert
    assert result.returncode == 1, (
        f"three empty sets must trip the vacuity guard rather than pass; got "
        f"{result.returncode} with stdout={result.stdout!r}"
    )
    assert result.stdout.isascii(), (
        f"a non-ASCII byte in this output is undecodable on a Windows console "
        f"and fails the job for a reason unrelated to platforms; got "
        f"stdout={result.stdout!r}"
    )


def test_script_exits_0_on_this_repo() -> None:
    """The gate must be green on this branch or it cannot be adopted."""
    # Act
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--root", str(_REPO_ROOT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    # Assert
    assert result.returncode == 0, (
        f"the three declarations must already agree on this branch; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
