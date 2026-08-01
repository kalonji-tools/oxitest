"""Slice-6 acceptance: B1 boundary enforcement (#1713).

Runs oxitest as a subprocess — B1 fires during a real run's fixture resolution,
so the assertions have to be about a run rather than about registry state.

Every project is re-run under ``-n 2``. That is load-bearing rather than
ceremony: the determinism of the BoundaryError-versus-FixtureNotFoundError
split rests entirely on ``worker.py`` re-registering every declaration home into
every worker. If that regresses, the verdict degrades to "not found" **in
parallel only** — invisible to every serial test in this file. Slices 2-4 did
this re-run; slice 5 skipped it.

Both resolution routes are covered, and they do not report alike. ``fx.<ns>.<name>``
raises ``BoundaryError`` (code ``fixture-boundary``) because the namespace segment
gives it an anchor to name; a ``Fixture[T]`` parameter is a bare-name lookup with
no segment, so it raises ``FixtureNotFoundError``. The injection test pins the
absence of the code for exactly that reason.

``-n N`` is ``ExecutionMode::Parallel { workers: Fixed(N) }``, which forces the
parallel path regardless of test count, so the data projects deliberately leave
``min_parallel_tests`` at its default. Lowering it would have made the "serial"
half of each parametrize case run through workers too, and this file would then
have no serial coverage at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import oxitest as oxi
from tests import helpers

_DATA_ROOT = Path(__file__).parent / "data"
_LEGAL = _DATA_ROOT / "slice6_boundary"
_CROSS = _DATA_ROOT / "slice6_cross_boundary"
_UNKNOWN = _DATA_ROOT / "slice6_unknown_namespace"
_DEPENDENCY = _DATA_ROOT / "slice6_dependency_anchor"
_INJECTION = _DATA_ROOT / "slice6_injection_boundary"

#: 1 in test_root.py + 2 in api/test_api.py + 2 in api/v1/test_v1.py
#: + 1 in admin/v1/test_admin_v1.py. The rootdir one is not decoration:
#: declaration homes are registered per directory that holds a test file, so
#: without it the rootdir __fixtures__.py is never discovered and the ancestor
#: lookup fails for a registration reason rather than a visibility one.
_LEGAL_TESTS = 6


@dataclass(frozen=True)
class RunMode:
    """Serial versus parallel, as one parametrize case."""

    label: str
    args: tuple[str, ...]


_SERIAL = RunMode(label="serial", args=())
_PARALLEL = RunMode(label="-n 2", args=("-n", "2"))

#: ``ExitCode::Failure`` (``src/types/exit.rs``). Spec decision 8: a B1
#: violation is a test ERROR and the run completes, so it exits 1 like any
#: other failing run. The neighbouring codes are the reason this is worth
#: pinning rather than asserting ``rc != 0`` — 3 is ``CollectError`` and 4 is
#: ``UsageError``, and both would also be non-zero. Those two say "the run was
#: misconfigured", which is how CI tells a genuine test failure from a broken
#: invocation; a regression that turned a boundary violation into a collection
#: abort would silently change that verdict.
_EXIT_FAILURE = 1


@oxi.parametrize(serial=_SERIAL, parallel=_PARALLEL)
def test_the_legal_tree_passes_whole(case: RunMode) -> None:
    """Descendant access, rootdir access, and two packages both named `v1`."""
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_LEGAL, *case.args)

    # Assert
    assert rc == 0, (
        f"the legal tree must pass under {case.label}; a filter that refused "
        f"everything would satisfy the violation tests just as well as a "
        f"correct one\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert f"{_LEGAL_TESTS} passed" in stdout, (
        f"all {_LEGAL_TESTS} must run — an anchor-blind collision check kills "
        f"the run on the duplicate 'v1' namespace, and a collection-level "
        f"failure would skip every visibility assertion; got:\n{stdout}"
    )


@oxi.parametrize(serial=_SERIAL, parallel=_PARALLEL)
def test_cross_boundary_access_is_a_boundary_error(case: RunMode) -> None:
    """Sibling package, prefix-sibling package, and a typo across the boundary."""
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_CROSS, *case.args)
    output = stdout + stderr

    # Assert
    assert rc == _EXIT_FAILURE, (
        f"three cross-boundary accesses must fail the run under {case.label}, "
        f"and fail it as ERRORed tests — exit 3 (CollectError) or 4 "
        f"(UsageError) would mean the boundary aborted the run instead of "
        f"reporting per-test verdicts, and CI reads those as 'the invocation "
        f"was wrong' rather than 'tests failed'\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert output.count("fixture-boundary") >= 3, (
        f"each of the three violations must carry the stable code — the code is "
        f"what lets docs link this failure and CI grep for it without matching "
        f"on prose; got:\n{output}"
    )
    assert output.count("will not make this access legal") == 1, (
        f"exactly one of the three is a typo. More than one means a fixture "
        f"that should exist was not registered, so the other errors are about "
        f"absence rather than about the boundary; none means the leaf fact is "
        f"not being appended at all; got:\n{output}"
    )
    assert "1 passed" in output, (
        f"the positive control in api/test_api.py must still resolve its own "
        f"package's fixture — without it a tree where api_conn never registered "
        f"would produce the same errors for entirely the wrong reason; "
        f"got:\n{output}"
    )


def test_the_prefix_sibling_is_not_treated_as_a_descendant() -> None:
    """`apiv2` starts with `api` as a string and is a sibling as a path."""
    # Act
    stdout, stderr, _rc = helpers.run_oxitest(_CROSS)
    output = stdout + stderr

    # Assert
    assert "test_prefix_sibling_is_not_a_descendant" in output, (
        "a startswith-based predicate declares apiv2 visible from an anchor at "
        "api and this test would silently pass — the failure has to be "
        f"attributed by name, or a green run means nothing here; got:\n{output}"
    )


@oxi.parametrize(serial=_SERIAL, parallel=_PARALLEL)
def test_the_injection_route_refuses_a_cross_boundary_fixture(case: RunMode) -> None:
    """Route 2: `Fixture[T]` injection, which has no namespace to blame.

    The `fx.` proxy is only one of the two ways a fixture is reached. A
    `Fixture[T]` parameter is a bare-name lookup, and it enforces B1 in a
    different place — `FixtureInstantiator.resolve_param` hands off to
    `resolve_fixture`, whose `get_visible` call is the check. Nothing in this
    file exercised that path, so the whole route could have been reverted to
    the unfiltered `get` without turning anything red.

    ``cwd`` is the project root here, unlike every other case in this file:
    the binding types live in an importable module of the project, and only a
    matching cwd puts that project on ``sys.path``. Sharing one class object
    between the declaration file and the test is what keeps the *type* branch
    of ``resolve_param`` the branch under test — re-importing a
    ``__fixtures__.py`` by package path yields a second, unequal class, and
    resolution would quietly fall through to the name branch instead.
    """
    # Act
    stdout, stderr, rc = helpers.run_oxitest(
        _INJECTION, *case.args, cwd=str(_INJECTION)
    )
    output = stdout + stderr

    # Assert
    assert rc == _EXIT_FAILURE, (
        f"injecting a sibling package's fixture by `Fixture[T]` must fail the "
        f"run under {case.label}, and fail it as an ERRORed test — exit 3 "
        f"(CollectError) would mean the refusal moved to collection time, "
        f"where the name index is deliberately unfiltered so that this "
        f"reaches the runtime B1 check at all\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "fixture-boundary" not in output, (
        f"the injection route reports not-found on purpose: a bare-name lookup "
        f"has no namespace segment, so it cannot name the anchor the way "
        f"BoundaryError does. Pinning the absence keeps the two routes' "
        f"contract visible — if this ever starts emitting the code, the "
        f"message has gained an anchor and the docs owe users the difference; "
        f"got:\n{output}"
    )
    assert "2 passed" in output, (
        f"both positive controls must survive: api/test_api.py proves the "
        f"fixture registered at all, and admin's same-typed injection proves "
        f"B1 refuses only what it must. Without them a project where nothing "
        f"registered would produce the same errors; got:\n{output}"
    )
    assert "2 errors" in output and "api_ledger' not found" in output, (
        f"the second refusal is the one that matters for the type index: "
        f"admin declares its own LedgerHandle fixture, so a resolver that "
        f"answered 'any fixture of this type I can see' would hand the test "
        f"a substitute and pass. Two errors out of four, with api_ledger "
        f"named, is the shape that says B1 refused rather than swapped; "
        f"got:\n{output}"
    )


@oxi.parametrize(serial=_SERIAL, parallel=_PARALLEL)
def test_unknown_namespace_is_not_reported_as_a_boundary(case: RunMode) -> None:
    """The other half of the taxonomy: unknown segment, not unreachable one."""
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_UNKNOWN, *case.args)
    output = stdout + stderr

    # Assert
    assert rc == _EXIT_FAILURE, (
        f"reaching a namespace that exists nowhere must still fail under "
        f"{case.label} — and as an ERRORed test, not as exit 3 "
        f"(CollectError) or 4 (UsageError). An unknown segment is only "
        f"knowable once the access runs, so hoisting it to collection time "
        f"would report a typo in one test as a whole-run misconfiguration\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "fixture-boundary" not in output, (
        f"a namespace that exists nowhere is a typo, not a boundary violation; "
        f"reporting it as one sends the user looking for a package that was "
        f"never declared; got:\n{output}"
    )
    assert "nope" in output, (
        f"the message must name the segment the user actually typed; got:\n{output}"
    )
    assert "conftest.py" not in output, (
        f"the stale hint told users to define a Fixtures() instance in "
        f"conftest.py, which has not been the primary declaration route since "
        f"slice 1; got:\n{output}"
    )


def test_a_fixture_cannot_depend_below_its_own_anchor() -> None:
    """The test's position is legal; the fixture's dependency is not.

    The only end-to-end coverage of the boundary descent added in this slice.
    Reverting the boundary narrowing in ``_resolve_fixture_defn`` flips this
    project from failing to passing.
    """
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_DEPENDENCY)
    output = stdout + stderr

    # Assert
    assert rc == _EXIT_FAILURE, (
        f"an api/-anchored fixture depending on an api/v1/-anchored one must "
        f"fail even though the calling test can see both — otherwise the "
        f"boundary is laundered through the test's position, and a wider "
        f"lifetime would cache a value from the narrower boundary. Exit "
        f"{_EXIT_FAILURE} specifically: the descent happens during the "
        f"dependency's resolution, so it is one test ERRORing, not exit 3 "
        f"(CollectError) or 4 (UsageError) killing the run\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "thing" in output, (
        f"the failure must name the dependency that could not be resolved, or "
        f"the user sees only that 'leaky' broke; got:\n{output}"
    )
    assert "1 passed" in output, (
        f"api/test_api.py's dependency-free fixture must still resolve — "
        f"without that, 'api' might simply have failed to register and the "
        f"failure above would be about absence, not about the anchor; "
        f"got:\n{output}"
    )
