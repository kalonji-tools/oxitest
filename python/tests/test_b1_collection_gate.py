"""Acceptance tests for the collection-time B1 gate (#1758).

The gate resolves every statically visible ``fx.`` access before any test runs,
so a violation can no longer hide inside code the access-time gate never
reaches. ADR-0009 Rule 3, Amendment 14, states both gates and their different
reach.

Two levels, because two different things can break. The unit half pins *which*
accesses the gate refuses — the branch structure it shares with the proxy, and
the three exemptions that make a legal access legal. The end-to-end half pins
that the refusal actually happens before any test executes, which is the whole
charter and is not observable from a resolver call.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import oxitest as oxi
from oxitest import TempDir
from tests import helpers
from tests.helpers.factories import (
    make_fixture_def,
    make_session,
    session_from_declarations,
)

_DATA_ROOT = Path(__file__).parent / "data"
_REACH = _DATA_ROOT / "b1_static_reach"

#: ``ExitCode::UsageError``. A fixture wiring error keeps its class wherever it
#: is caught, so a collection-time refusal exits 4 and not the 3 of the
#: transition it lives in — see ``docs/user/reference/exit-codes.md``.
_EXIT_USAGE = 4


@dataclass(frozen=True)
class RunMode:
    """Serial versus parallel, as one parametrize case.

    Load-bearing rather than ceremony: a B1 verdict has degraded in parallel
    only once already (#1713), invisible to every serial test.
    """

    label: str
    args: tuple[str, ...]


_SERIAL = RunMode(label="serial", args=("--serial",))
#: One worker, which is a distinct mode rather than a slower ``--serial``: it
#: takes the parallel path and so has a worker catalog, while leaving no second
#: worker for assignment to vary across. A verdict that held at ``--serial`` and
#: at ``-n 2`` was never asserted here in between (#1759).
_SINGLE = RunMode(label="-n 1", args=("-n", "1"))
_PARALLEL = RunMode(label="-n 2", args=("-n", "2"))


def _usages(*accesses: tuple[str | None, str]) -> list[dict[str, object]]:
    """One module's worth of extracted accesses, as the bridge sends them."""
    return [
        {
            "module_path": "/proj/admin/test_admin.py",
            "usages": [
                {"namespace": namespace, "name": name, "lineno": index + 1}
                for index, (namespace, name) in enumerate(accesses)
            ],
        }
    ]


# ── The three B1 exemptions ─────────────────────────────────────────────────


def test_an_ambient_namespace_is_never_a_boundary_violation() -> None:
    """Conftest, plugin and framework fixtures carry no anchor (ADR-0009 6+7).

    This is the false-positive class the validated prototype could not express.
    It modelled one exemption of the three and indexed everything else as
    anchored, and its corpus could not reveal the gap because every
    plugin-namespace access in this repository lives inside a string literal
    that generates a throwaway project at run time — so an AST scan never
    parses one as code.

    The gate avoids the class by construction rather than by testing for it: it
    asks the live registry, where an exempt source reports ``anchor is None``
    and is visible from everywhere.
    """
    # Arrange — a FrameworkSource def, which is anchor-less exactly as a
    # plugin-provided one is.
    session = make_session(make_fixture_def("conn", namespace="infra"))

    # Act
    errors = session.validate_fx_boundaries(_usages(("infra", "conn")))

    # Assert
    assert errors == [], (
        f"a fixture with no anchor is ambient by design, so no module can be "
        f"out of reach of it; flagging one would refuse every project that "
        f"uses a plugin's fixtures from more than one package; got {errors}"
    )


def test_the_builtin_namespace_is_never_a_boundary_violation() -> None:
    """`fx.oxi.<name>` is the reserved ambient namespace."""
    # Arrange — deliberately a session that has never heard of `oxi`, so a
    # gate reaching the registry at all would report the namespace missing.
    session = make_session(make_fixture_def("conn", namespace="infra"))

    # Act
    errors = session.validate_fx_boundaries(_usages(("oxi", "tmp")))

    # Assert
    assert errors == [], (
        f"built-ins are resolved by the proxy before the registry is consulted, "
        f"so the gate has to short-circuit on `oxi` the same way; got {errors}"
    )


def test_reading_an_attribute_off_a_fixture_is_not_a_fixture_access() -> None:
    """`fx.<fixture>.<attr>` is attribute access on a value, not a lookup.

    The extractor cannot tell this shape from `fx.<namespace>.<name>` — both
    are two attributes on the proxy — so the gate has to, exactly as the proxy
    does: a segment that is not a namespace is resolved as a shortcut and
    Python takes the attribute from its value. Checking the leaf here would
    report a violation for every test that reads a field off a fixture it
    legally owns.
    """
    # Arrange
    session = make_session(make_fixture_def("conn", namespace=""))

    # Act — `conn` is a visible fixture, `port` an attribute of its value.
    errors = session.validate_fx_boundaries(_usages(("conn", "port")))

    # Assert
    assert errors == [], (
        f"the segment resolves as a shortcut, so only the segment is the "
        f"gate's business; got {errors}"
    )


# ── What the gate does refuse ───────────────────────────────────────────────


def test_a_shortcut_that_resolves_to_nothing_is_refused() -> None:
    """A bare name with no visible declaration, caught before the run."""
    # Arrange
    session = make_session(make_fixture_def("conn", namespace=""))

    # Act
    errors = session.validate_fx_boundaries(_usages((None, "nope")))

    # Assert
    assert len(errors) == 1, (
        f"a bare name that no visible declaration provides cannot resolve at "
        f"run time either, so refusing it early costs nothing; got {errors}"
    )
    _module, _lineno, message = errors[0]
    assert "cannot resolve fixture 'nope'" in message, (
        f"the shortcut miss names the segment it could not resolve, whatever "
        f"the cause; separating a typo from a cross-boundary reach still needs "
        f"the unfiltered catalog and stays deferred (#1759 non-goal 2). The "
        f"remedy clause does branch, on a pure name test — see "
        f"test_shortcut_miss_message.py; got {message}"
    )


def test_an_anchored_fixture_out_of_reach_is_refused(tmp: TempDir) -> None:
    """The boundary case proper, against a real anchored declaration."""
    # Arrange — a declaration anchored at /proj/api, read from a sibling.
    anchor = Path(tmp) / "api"
    anchor.mkdir()
    declarations = anchor / "__fixtures__.py"
    declarations.write_text(
        "import oxitest as oxi\n\n\n"
        '@oxi.fixture(lifetime="function")\n'
        "def api_conn() -> str:\n"
        '    return "api"\n',
        encoding="utf-8",
    )
    session = session_from_declarations(declarations, anchor_package_path=str(anchor))

    # Act
    errors = session.validate_fx_boundaries(
        [
            {
                "module_path": str(Path(tmp) / "admin" / "test_admin.py"),
                "usages": [{"namespace": "api", "name": "api_conn", "lineno": 9}],
            }
        ]
    )

    # Assert
    assert len(errors) == 1, (
        f"a sibling package is not in the anchor's descendant chain, so this is "
        f"the violation the whole gate exists for; got {errors}"
    )
    _module, lineno, message = errors[0]
    assert lineno == 9, (
        f"the refusal must carry the line it was extracted from — with no test "
        f"running there is no node id to attribute it to, so file and line are "
        f"the only handle a reader gets; got {lineno}"
    )
    assert "fixture-boundary" in message, (
        f"the stable diagnostic code is what lets docs link this failure and CI "
        f"grep for it without matching on prose; got {message}"
    )


def test_the_same_module_reaches_its_own_inline_declaration(tmp: TempDir) -> None:
    """The `anchor == defining` branch, exercised with a *legal* access.

    ``is_visible`` tells an inline declaration from a package one by comparing
    the anchor to the defining module, and the inline branch is exact string
    equality rather than a component prefix — so it has no tolerance for a path
    spelled two ways. Every other test here would still pass if that branch
    were broken, because a negative case that keeps refusing looks exactly like
    one that refuses for the right reason.
    """
    # Arrange
    anchor = Path(tmp) / "pkg"
    anchor.mkdir()
    declarations = anchor / "__fixtures__.py"
    declarations.write_text(
        "import oxitest as oxi\n\n\n"
        '@oxi.fixture(lifetime="function")\n'
        "def conn() -> str:\n"
        '    return "c"\n',
        encoding="utf-8",
    )
    session = session_from_declarations(declarations, anchor_package_path=str(anchor))

    # Act — a test inside the anchor package itself.
    errors = session.validate_fx_boundaries(
        [
            {
                "module_path": str(anchor / "test_own.py"),
                "usages": [{"namespace": "pkg", "name": "conn", "lineno": 4}],
            }
        ]
    )

    # Assert
    assert errors == [], (
        f"a test in the anchor package is the most ordinary legal access there "
        f"is; if this refuses, the gate refuses correct code and the negative "
        f"tests above cannot show it; got {errors}"
    )


# ── The charter: reach the access-time gate cannot ──────────────────────────


def test_every_unreachable_violation_is_refused_before_any_test_runs() -> None:
    """The measurement #1758 was re-chartered on.

    Three cross-boundary accesses that differ only in how the test is reached.
    Before this gate all three exited 0: the skip and the dead branch reported
    nothing, and the ``xfail`` absorbed the ``BoundaryError`` and reported it as
    ``1 passed - 1 xfailed`` — a green suite recording a boundary violation as
    the test working correctly.
    """
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_REACH)
    output = stdout + stderr

    # Assert
    assert rc == _EXIT_USAGE, (
        f"a wiring error keeps its class wherever it is caught, so the refusal "
        f"exits 4. Exit 3 would mean it inherited the CollectError path of the "
        f"transition it lives in; exit 0 would mean the masking is back\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert output.count("fixture-boundary") == 3, (
        f"all three reach-variants must be reported in one run — a gate that "
        f"stopped at the first would refuse just as loudly, exit 4 just the "
        f"same, and report a third of the truth; got:\n{output}"
    )
    assert "passed" not in output, (
        f"the refusal must come before any test executes. That earliness is the "
        f"charter: a run that got as far as reporting a pass would have run the "
        f"anchor package's test first, and the skip and xfail rows would be "
        f"back to being decided by whether their bodies ran; got:\n{output}"
    )


def test_the_anchor_package_still_resolves_its_own_fixture() -> None:
    """The control for the refusal above.

    Without it, a tree where ``api_conn`` never registered at all would produce
    the same three refusals for entirely the wrong reason.
    """
    # Act
    stdout, stderr, rc = helpers.run_oxitest(
        _REACH / "b1_static_reach" / "api" / "test_api.py"
    )

    # Assert
    assert rc == 0, (
        f"the anchor package holds no cross-boundary access; rc={rc}\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "1 passed" in stdout, (
        f"api_conn must resolve for the package that declares it, which is what "
        f"makes the sibling's refusals boundary results rather than absence; "
        f"got:\n{stdout}"
    )


def test_a_deselected_module_is_not_judged_against_a_narrowed_catalog() -> None:
    """The gate ranges over what was collected, not over what was prescanned.

    `fx_usages` is filled during prescan, which runs *before* `filter_metadata`
    narrows the import set for a node ID, `-E`, `--failed=only` or
    `--affected`. The registry is filled by the imports that survive that
    narrowing. Read unscoped, the two disagree: a deselected module's accesses
    are judged against a catalog that never loaded its declaring package, and
    perfectly legal code is refused with exit 4.

    Every other test in this file runs a whole project, where the two sets
    coincide and the bug is invisible. That is exactly how it shipped.
    """
    # Act — select only the anchor package's own test. The `admin` module and
    # its three violations are deselected; `api` is legal and self-contained.
    stdout, stderr, rc = helpers.run_oxitest(
        _REACH, "-E", "name(test_the_anchor_package_resolves_its_own_fixture)"
    )

    # Assert
    assert rc == 0, (
        f"the selected test holds no illegal access, and the deselected "
        f"module's accesses are not this run's business — refusing here would "
        f"make `-E`, `--failed=only` and `--affected` unusable on any tree that "
        f"contains a violation anywhere; rc={rc}\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "1 passed" in stdout, (
        f"the selected test must actually run; a refusal that exited 0 without "
        f"running anything would satisfy the assertion above for the wrong "
        f"reason; got:\n{stdout}"
    )


def test_a_selected_violation_is_still_refused_under_the_same_filter() -> None:
    """The control for the test above.

    Scoping the gate to collected modules must narrow *what* is checked, never
    weaken the check. Without this, deleting the gate entirely would satisfy
    the preceding test.
    """
    # Act — the same filter shape, now selecting a test that does violate.
    stdout, stderr, rc = helpers.run_oxitest(_REACH, "-E", "name(test_inside_an_xfail)")

    # Assert
    assert rc == _EXIT_USAGE, (
        f"the violation is in a module this filter selects, so it is in scope "
        f"and must still refuse; rc={rc}\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )


@oxi.parametrize(serial=_SERIAL, single=_SINGLE, parallel=_PARALLEL)
def test_the_refusal_does_not_depend_on_the_execution_mode(case: RunMode) -> None:
    """The verdict is reached before any worker exists, so it cannot vary.

    A B1 verdict has degraded in parallel only once already (#1713), invisible
    to every serial test, which is why this is asserted rather than assumed.
    """
    # Act
    stdout, stderr, rc = helpers.run_oxitest(_REACH, *case.args)
    output = stdout + stderr

    # Assert
    assert rc == _EXIT_USAGE, (
        f"the refusal happens in the coordinator before scheduling, so the "
        f"execution mode cannot reach it under {case.label}; rc={rc}\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert output.count("fixture-boundary") == 3, (
        f"all three must survive the mode change; a count that dropped under "
        f"-n would mean the gate moved into the worker path; got:\n{output}"
    )
