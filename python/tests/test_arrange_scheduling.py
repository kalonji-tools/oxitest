"""End-to-end scheduling behaviour of ``@oxi.arrange`` (#1848).

#1848 retired the lifetime-derived arrangement inference. A component is now
the set of fixtures a collected test named in ``@oxi.arrange``, so the tier no
longer decides membership and the decorator is effective at every tier.

Nothing covered this before: ``test_arrange_execution.py`` pins that an
arranged fixture's setup and teardown *run*, and no test anywhere asserted
which process the tests then land in.

The projects are built inline rather than checked in under ``data/`` because
the questions vary two dimensions — the lifetime tier and the consumption form
— and a directory per cell would be eight of them. Each cell gets its own
directory, which is also what keeps the cache cold and stops one cell's
``pyproject.toml`` reaching the next.
"""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import TempDir
from tests.helpers.event_logs import EventLogRun, run_with_event_log
from tests.helpers.runners import run_oxitest

_LOG_ENV = "ARRANGE_TIERS_LOG"

_FIXTURES = '''\
"""One fixture at the tier under test, recording every build.

Instance ids are PID-qualified because the question is how many builds
happened and in which process.
"""

from __future__ import annotations

import itertools
import os
import sys
from pathlib import Path

import oxitest as oxi

_COUNTER = itertools.count(1)


def _record(event: str) -> None:
    """Append one event line to the per-pid shard of the log."""
    with Path(f"{{os.environ['{log_env}']}}.{{os.getpid()}}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"{{event}}\\n")


@oxi.fixture(lifetime="{tier}")
def dsn() -> str:
    """The fixture whose builds and placement the acceptance tests count.

    The third field is the role. An arranged component runs on the process
    that invoked oxitest. Counting distinct PIDs cannot tell "the runner" from
    "one worker", and one worker is exactly what a broken arrangement would
    look like.

    Read through ``__main__.__spec__``, not through ``sys.modules``. A worker
    is started as ``python -m oxitest._bridge.worker``, which binds that module
    to ``__main__`` and leaves ``sys.modules["oxitest._bridge.worker"]``
    **absent** — so the obvious membership test reports ``runner`` in every
    process, including all four workers, and an assertion built on it cannot
    fail.
    """
    main_spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    is_worker = getattr(main_spec, "name", None) == "oxitest._bridge.worker"
    role = "worker" if is_worker else "runner"
    _record(f"SETUP {{os.getpid()}}-{{next(_COUNTER)}} {{role}}")
    return "postgres://probe"
'''

# An async fixture at function tier — the cell ArrangeError governs.
_ASYNC_FIXTURES = '''\
from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
async def adsn() -> str:
    """Async and function-scope: illegal to arrange from a sync test."""
    return "postgres://async"
'''

_FORM_ARRANGE_ASYNC_FIXTURE_SYNC_TEST = """\
from __future__ import annotations

import oxitest as oxi
from oxitest import Fixtures


@oxi.arrange("adsn")
def test_{n}(fx: Fixtures) -> None:
    assert True, "a sync test arranging an async function-scope fixture"
"""

# The consumption forms. Each reaches the same value a different way; #1848's
# whole claim is that the reach stops mattering once the decorator decides.
_FORM_PLAIN = """\
from __future__ import annotations

from oxitest import Fixture


def test_{n}(dsn: Fixture[str]) -> None:
    assert dsn.startswith("postgres"), "the fixture must be injected"
"""

_FORM_PROXY = """\
from __future__ import annotations

from oxitest import Fixtures


def test_{n}(fx: Fixtures) -> None:
    assert fx.probe.dsn.startswith("postgres"), "the fixture must resolve via the proxy"
"""

_FORM_HELPER = """\
from __future__ import annotations

from oxitest import Fixtures


def _use(fx: Fixtures) -> None:
    assert fx.probe.dsn.startswith("postgres"), "reached through a same-module helper"


def test_{n}(fx: Fixtures) -> None:
    _use(fx)
"""

_FORM_ARRANGE_PROXY = """\
from __future__ import annotations

import oxitest as oxi
from oxitest import Fixtures


@oxi.arrange("dsn")
def test_{n}(fx: Fixtures) -> None:
    assert fx.probe.dsn.startswith("postgres"), "the fixture must resolve via the proxy"
"""

_FORM_ARRANGE_HELPER = """\
from __future__ import annotations

import oxitest as oxi
from oxitest import Fixtures


def _use(fx: Fixtures) -> None:
    assert fx.probe.dsn.startswith("postgres"), "reached through a same-module helper"


@oxi.arrange("dsn")
def test_{n}(fx: Fixtures) -> None:
    _use(fx)
"""

# The type-entry spelling. Only an @injectable type is accepted — a builtin or
# a plugin type — so a plain user class raises at collection.
_FORM_ARRANGE_TYPE_BUILTIN = """\
from __future__ import annotations

import os
from pathlib import Path

import oxitest as oxi
from oxitest import TempDir


@oxi.arrange(TempDir)
def test_{n}() -> None:
    with Path(f"{{os.environ['ARRANGE_TIERS_LOG']}}.{{os.getpid()}}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"USE type_entry {{os.getpid()}} -\\n")
    assert True, "the type entry must co-locate its tests, as a name entry does"
"""

# A module that never touches the fixture, so it stays outside every component.
_FORM_INERT = """\
from __future__ import annotations


def test_inert_{n}() -> None:
    assert True, "this module never touches the fixture"
"""

_PYPROJECT = """\
[project]
name = "probe"
version = "0.0.0"

[tool.oxitest]
testpaths = ["probe"]
python_files = ["test_*.py"]
strict = "abort"
# Force parallelism regardless of test count, so placement is observable at
# all. Without an explicit -n as well, the run never fans out and every cell
# reads the same.
min_parallel_tests = 1
"""


def _build_project(  # noqa: PLR0913 — cell spec, every kwarg has a default
    tmp: TempDir,
    cell: str,
    *,
    tier: str,
    form: str,
    modules: int = 4,
    inert: int = 0,
    fixtures: str | None = None,
) -> Path:
    """Write a throwaway project for one cell and return its root.

    One directory per cell, which keeps ``.oxitest_cache`` cold and stops one
    cell's config reaching the next — two of the three traps recorded on #1848.

    *inert* adds modules that never touch the fixture. They matter when the
    cell arranges: a project whose every module is in one component has no
    parallel work left, so the run is effectively serial and a parallel-only
    diagnostic correctly says nothing. An inert module keeps the run genuinely
    parallel while a component exists.
    """
    root = Path(tmp) / f"proj_{cell}"
    pkg = root / "probe"
    pkg.mkdir(parents=True)
    (root / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__fixtures__.py").write_text(
        fixtures
        if fixtures is not None
        else _FIXTURES.format(tier=tier, log_env=_LOG_ENV),
        encoding="utf-8",
    )
    for n in range(modules):
        (pkg / f"test_use_{n}.py").write_text(form.format(n=n), encoding="utf-8")
    for n in range(inert):
        (pkg / f"test_inert_{n}.py").write_text(
            _FORM_INERT.format(n=n), encoding="utf-8"
        )
    return root


def _run(tmp: TempDir, cell: str, *, tier: str, form: str) -> EventLogRun:
    """Build and run one cell at ``-n 4``, returning its event log."""
    root = _build_project(tmp, cell, tier=tier, form=form)
    return run_with_event_log(root, tmp, _LOG_ENV, "-n", "4", log_name=f"{cell}.log")


def _run_capturing_warnings(  # noqa: PLR0913 — cell spec, every kwarg has a default
    tmp: TempDir,
    cell: str,
    *,
    tier: str,
    form: str,
    workers: int = 4,
    inert: int = 0,
) -> tuple[str, str, int]:
    """Build and run one cell with the tracing layer emitting warnings.

    The wide-lifetime warning is a ``tracing::warn!``, not a Diagnostic, so
    ``--warnings`` does not reach it and neither does the diagnostics summary.
    ``RUST_LOG=warn`` is what surfaces it, which is also what
    ``docs/user/how-to/run-in-parallel.md`` tells the user to set.
    """
    root = _build_project(tmp, cell, tier=tier, form=form, inert=inert)
    env = {
        **os.environ,
        _LOG_ENV: str(Path(tmp) / f"{cell}.log"),
        "RUST_LOG": "warn",
    }
    return run_oxitest(root, "-n", str(workers), env=env)


# ── AC9: the warning is no longer suppressed by active arrangement ───────────


def test_module_lifetime_warning_fires_on_a_default_run(tmp: TempDir) -> None:
    """#1848 ungated the warning, and the gate was on for every default run.

    The suppression tested ``auto_arrange_threshold > 0``, whose default was
    70, so a plain project like this one printed nothing at all. Arrangement
    was measured not to reduce a build at module tier in any of the eight cells
    of the consumption-form matrix, so what the gate hid was true exactly when
    the user had been told the case was handled.
    """
    stdout, stderr, rc = _run_capturing_warnings(
        tmp, "warn_default", tier="module", form=_FORM_PLAIN
    )

    assert rc == 0, f"the probe project must pass:\n{stdout}\n{stderr}"
    assert "wide-lifetime" in stdout + stderr, (
        "a module-tier fixture is rebuilt once per task group whether or not "
        "anything arranges it, so a default run must not suppress the warning"
    )
    assert "@oxi.arrange" in stdout + stderr, (
        "the warning is the only place a user who lost auto-grouping learns how "
        "to ask for it back, so it must name the decorator"
    )


def test_module_lifetime_warning_fires_while_a_component_is_live(
    tmp: TempDir,
) -> None:
    """The warning survives alongside a real arranged component.

    The inert modules are load-bearing. Arranging every module puts the whole
    suite in one component, which leaves no parallel work, and the warning is
    parallel-only by construction — its sole call site is inside the
    ``ExecutionStrategy::Parallel`` arm. Without them this cell would report a
    correct silence and read as a regression.
    """
    stdout, stderr, rc = _run_capturing_warnings(
        tmp, "warn_component", tier="module", form=_FORM_ARRANGE_PROXY, inert=4
    )

    assert rc == 0, f"the probe project must pass:\n{stdout}\n{stderr}"
    assert "wide-lifetime" in stdout + stderr, (
        "an arranged component does not stop the fixture being rebuilt per task "
        "group, so the warning must still fire while one is live"
    )


def _roles(run: EventLogRun) -> list[str]:
    """The role field of every SETUP line: ``runner`` or ``worker``."""
    return [e.split()[2] for e in run.setups]


# ── AC7: @oxi.arrange is effective at every tier ─────────────────────────────


def test_arrange_groups_at_function_tier(tmp: TempDir) -> None:
    """#1848: at function tier the decorator was a silent no-op before this.

    Placement changes and the build count does not. A function-scope fixture
    is rebuilt per test by construction, so co-locating its consumers cannot
    share anything — what the user gets is the tests landing together, which
    is a real thing to want for a port, a lock file or a device.
    """
    run = _run(tmp, "fn_arranged", tier="function", form=_FORM_ARRANGE_PROXY)

    assert run.rc == 0, f"the probe project must pass:\n{run.stdout}\n{run.stderr}"
    assert len(run.setup_pids) == 4, (
        f"function scope rebuilds per test, so 4 tests must build 4 times; "
        f"got {run.setup_pids}"
    )
    assert len(set(run.setup_pids)) == 1, (
        f"the arranged component must land in one process; got "
        f"{set(run.setup_pids)}. Before #1848 this cell measured four, because "
        f"the tier filter discarded the declaration"
    )


def test_arrange_groups_at_module_tier_on_the_runner(tmp: TempDir) -> None:
    """The component runs on the process that invoked oxitest, not on a worker.

    One PID alone does not say this — a single worker would also read as one.
    The role field is what separates them.
    """
    run = _run(tmp, "mod_arranged", tier="module", form=_FORM_ARRANGE_PROXY)

    assert run.rc == 0, f"the probe project must pass:\n{run.stdout}\n{run.stderr}"
    assert len(run.setup_pids) == 4, (
        f"module scope rebuilds per module, and a module is the scheduling "
        f"unit, so arrangement cannot reduce this below 4; got {run.setup_pids}"
    )
    assert set(_roles(run)) == {"runner"}, (
        f"an arranged component runs on the main process; got roles "
        f"{_roles(run)}, so the build landed in a worker instead"
    )


def test_arrange_reduces_builds_at_process_tier(tmp: TempDir) -> None:
    """The one cell where arrangement actually saves a build: 4 becomes 1.

    ``lifetime="process"`` means once per process, so collapsing four workers
    into one collapses four builds into one. #1848's issue body called
    "build the shared fixture once" impossible; that is true at module tier
    and was stated generally. The tier filter is the only reason this has
    never been reachable.
    """
    run = _run(tmp, "proc_arranged", tier="process", form=_FORM_ARRANGE_PROXY)

    assert run.rc == 0, f"the probe project must pass:\n{run.stdout}\n{run.stderr}"
    assert len(run.setup_pids) == 1, (
        f"process scope builds once per process and the component is one "
        f"process, so 4 modules must build once; got {run.setup_pids}. "
        f"Unarranged the same project builds 4 times"
    )
    assert set(_roles(run)) == {"runner"}, (
        f"the single build must happen on the main process; got {_roles(run)}"
    )


# ── AC8: the accepted cost — module tier is no longer grouped for free ───────


def test_module_tier_without_arrange_is_not_grouped(tmp: TempDir) -> None:
    """Retiring the inference costs module-tier consumers their free grouping.

    Intended, not a regression. Before #1848 this cell measured one PID
    because the tier alone put the fixture in a component. A user who wants
    the old behaviour adds ``@oxi.arrange``, which is what the wide-lifetime
    warning now tells them.
    """
    run = _run(tmp, "mod_plain", tier="module", form=_FORM_PLAIN)

    assert run.rc == 0, f"the probe project must pass:\n{run.stdout}\n{run.stderr}"
    assert len(set(run.setup_pids)) > 1, (
        f"nothing arranges here, so no component exists and the modules must "
        f"reach workers; got {set(run.setup_pids)}. One PID means an inference "
        f"is still deriving components from the lifetime tier"
    )
    assert set(_roles(run)) == {"worker"}, (
        f"got roles {_roles(run)}. This is the control for every 'runner' "
        f"assertion in this file: if the role signal cannot report 'worker' "
        f"here, where the builds provably happen in four separate worker "
        f"processes, then it reports 'runner' unconditionally and those "
        f"assertions cannot fail. The first version of this signal tested "
        f"'oxitest._bridge.worker' in sys.modules, which is False even inside "
        f"a worker, because -m binds the module to __main__"
    )


# ── AC6: the consumption form no longer changes scheduling ───────────────────


def test_consumption_form_does_not_change_scheduling(tmp: TempDir) -> None:
    """The asymmetry #1848 was filed for: three reaches, three verdicts.

    Before this change the annotation form was grouped and the other two were
    not, because only the annotation form put the fixture in ``fixture_deps``
    where the partitioner could see it. The difference was invisible and
    nothing diagnosed it. With no inference there is nothing to be asymmetric
    about: none of the three is grouped, because none of them asked to be.
    """
    forms = {
        "annotation": _FORM_PLAIN,
        "proxy": _FORM_PROXY,
        "helper": _FORM_HELPER,
    }

    placements = {
        name: len(set(_run(tmp, f"form_{name}", tier="module", form=form).setup_pids))
        for name, form in forms.items()
    }

    assert len(set(placements.values())) == 1, (
        f"the three documented ways to reach a fixture must schedule "
        f"identically once nothing is inferred from the tier; got {placements}"
    )
    assert all(count > 1 for count in placements.values()), (
        f"none of these forms arranges, so every one of them must fan out; "
        f"got {placements}"
    )


# ── Not reached by: the async cell every other premise held constant ─────────


def test_sync_test_arranging_an_async_function_fixture_is_still_refused(
    tmp: TempDir,
) -> None:
    """Making @oxi.arrange effective at function tier must not open ArrangeError's cell.

    Every other premise on this branch held the fixture and the test
    synchronous. #1848 makes the decorator effective at ``function`` tier for
    the first time, and ``function`` tier is exactly where the illegal
    ``(sync test x async fixture)`` cell lives — so the guard and the
    scheduler now meet on an entry that previously reached only the guard.

    Measured: the refusal still happens at collection and the run never
    schedules, so the two do not interact.
    """
    root = _build_project(
        tmp,
        "async_illegal",
        tier="function",
        form=_FORM_ARRANGE_ASYNC_FIXTURE_SYNC_TEST,
        modules=2,
        fixtures=_ASYNC_FIXTURES,
    )

    stdout, stderr, rc = run_oxitest(root, "-n", "2")

    assert rc != 0, (
        f"a sync test arranging an async function-scope fixture is the illegal "
        f"cell and must be refused; got rc={rc}\n{stdout}"
    )
    assert "adsn" in stdout + stderr, (
        f"the refusal must name the offending fixture so the user can find "
        f"it\n{stdout}\n{stderr}"
    )


def test_arrange_through_a_helper_groups_the_same(tmp: TempDir) -> None:
    """The decorator decides; the reach does not — the arranged half of AC6.

    ``test_consumption_form_does_not_change_scheduling`` shows the three
    unarranged forms agree. This is the same claim on the other side of the
    decorator, and it is the sharpest case: the test body never mentions the
    fixture at all, so nothing but ``@oxi.arrange`` could put it in a
    component.
    """
    through_helper = _run(tmp, "arr_helper", tier="module", form=_FORM_ARRANGE_HELPER)
    through_proxy = _run(tmp, "arr_proxy", tier="module", form=_FORM_ARRANGE_PROXY)

    assert through_helper.rc == 0, (
        f"the probe project must pass:\n{through_helper.stdout}\n"
        f"{through_helper.stderr}"
    )
    assert (
        len(set(through_helper.setup_pids)) == len(set(through_proxy.setup_pids)) == 1
    ), (
        f"reaching the fixture through a same-module helper must schedule "
        f"exactly as reaching it directly; helper "
        f"{set(through_helper.setup_pids)} vs proxy "
        f"{set(through_proxy.setup_pids)}"
    )


def test_a_type_entry_co_locates_its_tests(tmp: TempDir) -> None:
    """#2045: the type spelling groups, exactly as the name spelling does.

    ``@oxi.arrange`` accepts a type as well as a name, but only for an
    ``@injectable`` one — a builtin or a plugin type. Before #2045 that form
    was accepted and then ignored: ``ArrangedEntry::Type`` and
    ``ArrangedEntry::Name`` were flattened to the same string before crossing
    to Python, and a type's ``__name__`` is not a registry key. A builtin
    registers under its **impl** class name, so ``TempDir`` could never match
    ``_TempDirFixture`` and the component never formed.

    The predecessor of this test asserted only ``rc == 0`` and ``"4 passed"``,
    so it passed both before and after the behaviour changed and pinned nothing
    about grouping at all. Placement is what the issue is about, so placement
    is what this asserts.
    """
    root = _build_project(
        tmp, "type_entry", tier="module", form=_FORM_ARRANGE_TYPE_BUILTIN
    )
    run = run_with_event_log(root, tmp, _LOG_ENV, "-n", "4", log_name="type_entry.log")

    assert run.rc == 0, f"the probe project must pass:\n{run.stdout}\n{run.stderr}"
    assert len(run.uses) == 4, (
        f"all four tests must run, or a placement count means nothing; got {run.uses}"
    )
    assert len(run.running_pids) == 1, (
        f"the type entry must put its four tests in one process, which is what a "
        f"name entry already does; four processes is the silent no-op #2045 "
        f"removes. Got {run.running_pids}"
    )


# ── The type entry's two edges: refusal, and how it is named back (#2045) ────

_FORM_ARRANGE_UNRESOLVABLE_TYPE = """\
from __future__ import annotations

import oxitest as oxi


@oxi.injectable
class NotAFixture:
    \"\"\"Injectable, so the decorator accepts it, and no fixture provides it.\"\"\"


@oxi.arrange(NotAFixture)
def test_{n}() -> None:
    assert True, "collection must refuse before this runs"
"""


def test_a_type_entry_that_resolves_to_nothing_is_refused(tmp: TempDir) -> None:
    """#2045: an accepted spelling that does nothing is the defect, one level out.

    ``oxitest.injectable`` is public, so a user can mark their own class and
    clear the decorator's ``__oxitest_injectable__`` check. The decorator cannot
    do better — it runs before any registry exists.

    The refusal is ``validate_fixture_names``, which this branch deliberately
    does not change: ``_augment_fixture_deps`` writes the type name as the
    qualifier, and only *builtin* type names are exempt from that check. Pinned
    here because the fix depends on that gate staying where it is — rewriting
    the qualifier would move the refusal without replacing it.
    """
    root = _build_project(
        tmp, "unresolvable", tier="module", form=_FORM_ARRANGE_UNRESOLVABLE_TYPE
    )

    stdout, stderr, rc = run_oxitest(root, "-n", "4")

    assert rc != 0, (
        f"an @injectable type that no fixture provides must refuse the run; exiting 0 "
        f"is the silent no-op #2045 removes\n{stdout}\n{stderr}"
    )
    assert "NotAFixture" in stdout + stderr, (
        f"the refusal must name the type the user wrote, or they cannot tell which "
        f"entry is wrong\n{stdout}\n{stderr}"
    )


def test_the_scheduling_diagnostic_names_the_user_spelling(tmp: TempDir) -> None:
    """#2045: the diagnostic says ``TempDir`` and never ``_TempDirFixture``.

    A component is keyed by the registry name. For a builtin that is the private
    impl class, so printing the component verbatim would show a name the user
    never typed and cannot look up. The display map exists for this line alone,
    which is why it is pinned here.
    """
    root = _build_project(
        tmp, "display", tier="module", form=_FORM_ARRANGE_TYPE_BUILTIN
    )
    env = {**os.environ, _LOG_ENV: str(Path(tmp) / "display.log")}

    stdout, stderr, _rc = run_oxitest(root, "-n", "4", "-v", env=env)

    output = stdout + stderr
    assert "auto-arranged" in output, (
        f"the scheduling diagnostic must appear at all, or the naming assertion "
        f"below cannot fire\n{output}"
    )
    assert "TempDir" in output, (
        f"the diagnostic must name the fixture as the user spelled it\n{output}"
    )
    assert "_TempDirFixture" not in output, (
        f"the private impl class name must never reach the user — showing it invites "
        f"them to type a name that is not public API\n{output}"
    )
