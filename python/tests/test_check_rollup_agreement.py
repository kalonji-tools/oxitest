"""Tests for the rollup-agreement check in ``check_rollup_agreement.py``.

A ``* (required)`` job is a rollup: it waits on other jobs via ``needs:`` and
then inspects their results in its steps. A job listed in ``needs:`` but never
referenced by any step is waited on and never checked — a silent hole (#1944).

actionlint checks the *opposite* direction of the same invariant and is
provably blind to this one, so the two gates are not redundant. Both were
mutation-tested against ``.github/workflows/test.yml``: dropping a job from the
allowlist loop SURVIVED actionlint, and adding ``needs.no-such-job`` was KILLED
by it (#1974 §2, §3).

Tests cover the extractor functions in isolation plus subprocess runs of the
full script — against a mock layout with a deliberate mismatch, and against
this repo's own workflows.
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
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_rollup_agreement.py"


def _load_script_module() -> ModuleType:
    """Load ``scripts/check_rollup_agreement.py`` as a module.

    The scripts directory is not a package, so we use ``importlib.util`` rather
    than a normal import. Fresh module object per call — each load overwrites
    the previous one, so there is no cross-test state.

    The ``sys.modules`` registration is load-bearing, unlike in the sibling
    loader in ``test_check_protocol_version.py``: the script defines a
    ``@dataclass``, and ``dataclasses._process_class`` resolves the defining
    module through ``sys.modules.get(cls.__module__).__dict__``. Executing the
    module without registering it first makes that ``None`` and the decorator
    dies with ``AttributeError: 'NoneType' object has no attribute '__dict__'``.
    """
    spec = importlib.util.spec_from_file_location(
        "check_rollup_agreement_under_test", _SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _job(module: ModuleType, source: str, job_id: str = "gate") -> dict:
    """Parse a workflow literal and return one job mapping."""
    return module.load_workflow_text(textwrap.dedent(source))["jobs"][job_id]


# ── `needs:` normalisation ───────────────────────────────────────────────────


@dataclass(frozen=True)
class NeedsCase:
    """One spelling of a ``needs:`` declaration, with the ids it must yield."""

    source: str


@oxi.parametrize(
    flow_sequence=NeedsCase("""
        name: probe
        on: push
        jobs:
          gate:
            name: Probe (required)
            needs: [alpha, beta]
            steps:
              - run: echo x
        """),
    block_sequence=NeedsCase("""
        name: probe
        on: push
        jobs:
          gate:
            name: Probe (required)
            needs:
              - alpha
              - beta
            steps:
              - run: echo x
        """),
)
def test_needs_normalisation_handles_flow_and_block(case: NeedsCase) -> None:
    """``needs:`` may be a flow sequence or a block sequence.

    Only flow style appears in this repo today, so a parser written from the
    tree alone would leave block style unexercised — and an unrecognised form
    resolves to the empty set, which exempts the job from the gate silently.
    """
    # Arrange
    module = _load_script_module()
    job = _job(module, case.source)

    # Act
    declared = module.declared_needs(job)

    # Assert
    assert declared == {"alpha", "beta"}, (
        "a `needs:` form the parser does not recognise resolves to the empty "
        "set, which makes the gate pass on a workflow it never actually read"
    )


def test_needs_scalar_form_is_normalised() -> None:
    """A single dependency may be written as a bare scalar, not a list."""
    # Arrange
    module = _load_script_module()
    job = _job(
        module,
        """
        name: probe
        on: push
        jobs:
          gate:
            name: Probe (required)
            needs: alpha
            steps:
              - run: echo x
        """,
    )

    # Act
    declared = module.declared_needs(job)

    # Assert
    assert declared == {"alpha"}, (
        "a scalar `needs:` is a one-element dependency list; treating it as "
        "unparsable would silently exempt the job from the gate"
    )


# ── reference extraction ─────────────────────────────────────────────────────


def test_references_exclude_the_needs_key_itself() -> None:
    """``needs:`` names dependencies; that is not a reference to their results.

    Counting the key itself would make every job trivially self-satisfying and
    the gate could never fire on anything.
    """
    # Arrange
    module = _load_script_module()
    job = _job(
        module,
        """
        name: probe
        on: push
        jobs:
          gate:
            name: Probe (required)
            needs: [alpha]
            steps:
              - run: echo hi
        """,
    )

    # Act
    referenced = module.referenced_needs(job)

    # Assert
    assert referenced == set(), (
        "if the `needs:` key counted as a reference, a rollup that checks "
        "nothing would pass — the exact defect this gate exists to catch"
    )


def test_reference_found_in_a_case_statement() -> None:
    """``Quality (required)`` reads its dependency in a ``case``, not a loop.

    A rule that looks only for the allowlist ``for`` loop fails on two of this
    repo's three rollups on day one.
    """
    # Arrange
    module = _load_script_module()
    job = _job(
        module,
        """
        name: probe
        on: push
        jobs:
          gate:
            name: Probe (required)
            needs: [alpha]
            steps:
              - run: |
                  case "${{ needs.alpha.result }}" in
                    success) ;;
                    *) exit 1 ;;
                  esac
        """,
    )

    # Act
    referenced = module.referenced_needs(job)

    # Assert
    assert referenced == {"alpha"}, (
        "the checker must find references anywhere in the job, not only in a "
        "`for` loop — quality.yml uses a `case` and test.yml a `for`"
    )


def test_reference_found_in_a_job_level_if() -> None:
    """A rollup may gate on a dependency from its job-level ``if:``.

    Scanning only ``steps`` would miss it and report a false violation.
    """
    # Arrange
    module = _load_script_module()
    job = _job(
        module,
        """
        name: probe
        on: push
        jobs:
          gate:
            name: Probe (required)
            needs: [alpha]
            if: needs.alpha.result == 'success'
            steps:
              - run: echo x
        """,
    )

    # Act
    referenced = module.referenced_needs(job)

    # Assert
    assert referenced == {"alpha"}, (
        "a reference outside `steps` is still a check; missing it reports a "
        "violation against correct code, which is how a gate gets suppressed"
    )


# ── scoping ──────────────────────────────────────────────────────────────────


def test_non_required_job_is_ignored() -> None:
    """docs.yml's ``deploy`` has ``needs: build`` and references nothing.

    That is correct — the dependency is for ordering. An unscoped rule fires
    here, gets suppressed, and the gate dies.
    """
    # Arrange
    module = _load_script_module()
    workflow = module.load_workflow_text(
        textwrap.dedent("""
            name: probe
            on: push
            jobs:
              build:
                steps:
                  - run: echo build
              deploy:
                name: Deploy to GitHub Pages
                needs: build
                steps:
                  - uses: actions/deploy-pages@v5
        """)
    )

    # Act
    violations = module.check_workflow(workflow, Path("probe.yml"))

    # Assert
    assert violations == [], (
        "ordering-only `needs:` on a non-rollup job is normal; firing on it "
        "makes the gate noisy and it gets turned off"
    )


def test_workflow_without_any_required_job_is_clean() -> None:
    """publish.yml, release.yml and benchmarks.yml hold no ``* (required)`` job."""
    # Arrange
    module = _load_script_module()
    workflow = module.load_workflow_text(
        textwrap.dedent("""
            name: probe
            on: push
            jobs:
              solo:
                steps:
                  - run: echo x
        """)
    )

    # Act
    violations = module.check_workflow(workflow, Path("probe.yml"))

    # Assert
    assert violations == [], (
        "a workflow with no rollup must exit clean rather than crash on a "
        "missing `name` or `needs` key"
    )


# ── the defect itself ────────────────────────────────────────────────────────


def test_unreferenced_dependency_is_reported() -> None:
    """Declared in ``needs:``, never read by any step — the #1944 hole."""
    # Arrange
    module = _load_script_module()
    workflow = module.load_workflow_text(
        textwrap.dedent("""
            name: probe
            on: push
            jobs:
              gate:
                name: Probe (required)
                needs: [alpha, beta]
                steps:
                  - run: echo "${{ needs.alpha.result }}"
        """)
    )

    # Act
    violations = module.check_workflow(workflow, Path("probe.yml"))

    # Assert
    assert len(violations) == 1, (
        "one rollup with one unreferenced dependency must produce exactly one "
        "violation — a count of 0 means the gate is inert"
    )
    assert violations[0].unreferenced == ("beta",), (
        "the report must name the job that is waited on but never checked, or "
        "the reader cannot act on it"
    )


# ── end to end ───────────────────────────────────────────────────────────────


def test_script_exits_1_on_a_mock_layout_with_a_mismatch(tmp: TempDir) -> None:
    """The full script, as a subprocess, against a mock repo layout."""
    # Arrange
    workflows = tmp / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "broken.yml").write_text(
        textwrap.dedent("""
            name: broken
            on: push
            jobs:
              gate:
                name: Broken (required)
                needs: [alpha, beta]
                steps:
                  - run: echo "${{ needs.alpha.result }}"
        """),
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
        f"a workflow with an unreferenced dependency must fail the hook, or "
        f"the prek gate passes on the defect; got {result.returncode} with "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "beta" in result.stdout, (
        f"the failure output must name the unreferenced job so the developer "
        f"can act without re-deriving the rule; got stdout={result.stdout!r}"
    )


def test_script_exits_0_on_this_repo() -> None:
    """The gate must be green on ``main`` as it stands, including #1961's shape.

    Every rollup now has ``changes`` first in ``needs:`` and deliberately
    outside the allowlist loop — it is checked by its own stricter step. A
    checker that demanded membership of the loop would fail here immediately.
    """
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
        f"the checker must pass on this repo's own workflows or it cannot be "
        f"adopted as a gate; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
