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
_TYPE_INDEX_GUARD = _DATA_ROOT / "b1_type_index_guard"
_TYPE_INDEX_GUARD_ANCHOR = _TYPE_INDEX_GUARD / "b1_type_index_guard" / "vault"

#: 2 in api/test_api.py + 2 in api/v1/test_v1.py + 1 in admin/v1/test_admin_v1.py.
#: The rootdir package holds no test of its own, which is the point: its
#: __fixtures__.py must still be discovered by the tests below it (#1765).
_LEGAL_TESTS = 5


@dataclass(frozen=True)
class RunMode:
    """Serial versus parallel, as one parametrize case."""

    label: str
    args: tuple[str, ...]


_SERIAL = RunMode(label="serial", args=())
_PARALLEL = RunMode(label="-n 2", args=("-n", "2"))

#: ``ExitCode::UsageError`` (``src/types/exit.rs``). A B1 violation is a test
#: ERROR, the run completes and every test reports — and then the run exits 4
#: because the suite is wired wrong (#1761).
#:
#: This was ``_EXIT_FAILURE = 1`` until #1761, on this reasoning: *"a B1
#: violation is a test ERROR and the run completes, so it exits 1 like any
#: other failing run … 3 is CollectError and 4 is UsageError … Those two say
#: 'the run was misconfigured', which is how CI tells a genuine test failure
#: from a broken invocation."* The **second half is what was overturned**: a
#: fixture reached across its anchor *is* a misconfiguration, ADR-0009 said so
#: and named #1761 as the fix.
#:
#: The **first half still holds**, and is why the exact code is still pinned
#: rather than asserting ``rc != 0``: a regression that turned a boundary
#: violation into a collection abort would move it to 3 and silently change
#: what CI is told, exactly as the original comment warned.
_EXIT_USAGE = 4

#: ``ExitCode::CollectError`` (``src/types/exit.rs``). Distinguishing it from
#: ``_EXIT_USAGE`` is the entire point of the #1768 guard below: the two
#: verdicts differ by *when* the refusal happened, and that is the coupling.
#: Since #1761 both codes say "misconfigured", so the guard now pins *which*
#: kind of misconfiguration rather than misconfigured-versus-failed.
_EXIT_COLLECT_ERROR = 3


@oxi.parametrize(serial=_SERIAL, parallel=_PARALLEL)
def test_the_legal_tree_passes_whole(case: RunMode) -> None:
    """Descendant access, rootdir access, and two packages both named `v1`."""
    # Act
    # ``--warnings`` is load-bearing, not decoration: without it the reporter
    # prints "N notices (--warnings to expand)" and never the message text, so
    # the shadow assertion below has nothing to match and cannot fail. Caught
    # by mutation — the assertion survived a `_can_see_both` that always
    # returned True.
    stdout, stderr, rc = helpers.run_oxitest(_LEGAL, "--warnings", *case.args)

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
    assert "shadows" not in stdout + stderr, (
        f"api/v1 and admin/v1 are disjoint subtrees, so neither declaration of "
        f"'thing' overrides the other. This is the only end-to-end coverage of "
        f"the visibility gate under {case.label} — the unit tests build "
        f"FixtureDefs by hand and would stay green if collection stopped "
        f"passing real anchors to register()\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )


@oxi.parametrize(serial=_SERIAL, parallel=_PARALLEL)
def test_cross_boundary_access_is_a_boundary_error(case: RunMode) -> None:
    """Sibling package, prefix-sibling package, and a typo across the boundary."""
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_CROSS, *case.args)
    output = stdout + stderr

    # Assert
    assert rc == _EXIT_USAGE, (
        f"three cross-boundary accesses must fail the run under {case.label} "
        f"as ERRORed tests that each still report, and the run must then exit "
        f"4 because the suite is wired wrong (#1761). Exit 3 (CollectError) "
        f"would mean the boundary aborted collection instead of producing "
        f"per-test verdicts; exit 1 would mean CI cannot tell this from an "
        f"assertion failure\n"
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
    assert rc == _EXIT_USAGE, (
        f"injecting a sibling package's fixture by `Fixture[T]` must fail the "
        f"run under {case.label} as an ERRORed test, and exit 4. This route "
        f"reports FixtureNotFoundError rather than BoundaryError because a "
        f"bare name has no namespace segment to attribute, so a vote wired to "
        f"BoundaryError alone would leave this B1 violation at exit 1 (#1761). "
        f"Exit 3 (CollectError) would mean the refusal moved to collection "
        f"time, where the name index is deliberately unfiltered so that this "
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
    assert rc == _EXIT_USAGE, (
        f"reaching a namespace that exists nowhere must still fail under "
        f"{case.label} as an ERRORed test, and exit 4: asking for a fixture "
        f"the run cannot supply is a wiring mistake whether the segment is "
        f"unknown or merely invisible, and ADR-0009 Rule 5 makes the two "
        f"deliberately indistinguishable here (#1761). Exit 3 (CollectError) "
        f"would be wrong for a different reason — an unknown segment is only "
        f"knowable once the access runs, so hoisting it to collection time "
        f"would abort the whole run over one test\n"
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
    assert rc == _EXIT_USAGE, (
        f"an api/-anchored fixture depending on an api/v1/-anchored one must "
        f"fail even though the calling test can see both — otherwise the "
        f"boundary is laundered through the test's position, and a wider "
        f"lifetime would cache a value from the narrower boundary. Exit "
        f"{_EXIT_USAGE} specifically: the descent happens during the "
        f"dependency's resolution, so it is one test ERRORing and the run "
        f"still reporting, not exit 3 (CollectError) killing the run before "
        f"any test produced a verdict\n"
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


def test_the_collection_validator_stays_name_based() -> None:
    """Pin the coupling that keeps the unfiltered `_by_type` index harmless (#1768).

    ``FixtureInstantiator.resolve_param`` resolves ``Fixture[T]`` by *type*
    first, through ``FixtureRegistry.resolve``, which reads ``_by_type`` — an
    index with no B1 filtering, unlike ``get_visible``. That type hit is
    discarded today only because ``FixtureValidator.validate_fixture_names``
    rejects, at collection time, any ``Fixture[T]`` parameter whose *name*
    matches no registered fixture, however well its type matches. B1 survives
    the index by an accident of ordering, not by construction, and this test is
    where that accident is written down.

    ``pool`` is registered nowhere; ``LedgerHandle`` is registered exactly once,
    in a package ``audit/`` cannot see. One candidate of that type is
    deliberate — ``resolve`` short-circuits on a lone candidate without
    consulting the qualifier at all, which is the case closest to the type index
    having the final say.

    Drop the name branch from ``validate_fixture_names`` and both halves of the
    verdict move: the run stops exiting 3 and ERRORs a *test* instead, and the
    message starts naming ``vault_ledger`` — a fixture ``audit/test_audit.py``
    never mentions, chosen for it by the type index. B1 itself still holds one
    step further on, at ``resolve_fixture``'s ``get_visible``; that residual
    guard is why #1768 is a latent hazard rather than a live bypass, and why the
    *verdict* is the only thing there is to pin. There is no observable for
    "resolution consulted ``_by_name`` before ``_by_type``" short of
    monkeypatching.

    If a future slice has to delete this test to make type-only resolution work,
    the deletion is the moment to filter ``_by_type`` through the visibility
    predicate — option 1 in #1768, deliberately not done here because filtering
    an index nothing consults yet buys a scan and no behaviour.

    No ``-n 2`` case, unlike the rest of this file: the refusal happens in the
    Rust ``FixtureValidationPhase`` before any worker is spawned, so there is no
    per-worker registration for the parallel path to disagree about. Verified —
    ``-n 2`` produces the same exit 3 and the same message.
    """
    # Act
    stdout, stderr, rc = helpers.run_oxitest(
        _TYPE_INDEX_GUARD, cwd=str(_TYPE_INDEX_GUARD)
    )
    output = stdout + stderr
    anchor_stdout, anchor_stderr, anchor_rc = helpers.run_oxitest(
        _TYPE_INDEX_GUARD_ANCHOR, cwd=str(_TYPE_INDEX_GUARD)
    )
    anchor_output = anchor_stdout + anchor_stderr

    # Assert
    assert anchor_rc == 0 and "1 passed" in anchor_output, (
        f"the anchor package's own injection must resolve — it is what proves "
        f"LedgerHandle has a registered, injectable match, so the refusal below "
        f"is about the parameter's name rather than about a type nothing "
        f"provides\nstdout:\n{anchor_stdout}\nstderr:\n{anchor_stderr}"
    )
    assert rc == _EXIT_COLLECT_ERROR, (
        f"a Fixture[T] parameter whose name matches nothing must be refused at "
        f"collection, not at run time. Exit {_EXIT_USAGE} here means the "
        f"validator stopped being name-based and the refusal slid downstream to "
        f"resolution, where the only thing still holding B1 is get_visible — "
        f"see #1768\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "fixture 'pool' not found" in output, (
        f"the refusal must name the parameter the test actually wrote; naming "
        f"anything else means the name was resolved to something before being "
        f"reported; got:\n{output}"
    )
    assert "vault_ledger" not in output, (
        f"the type-resolved fixture must never reach the user's screen. It is "
        f"anchored where this test cannot see it, so a message naming it is the "
        f"tell that the unfiltered _by_type index got the final say — the exact "
        f"bypass #1768 exists to keep unreachable; got:\n{output}"
    )
