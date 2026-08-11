"""A declaring module never spans two dispatch phases (#1750).

``lifetime="module"`` promises one instance per test module. A phase owns its
own fixture session, so a module whose items land in two phases builds its
fixture once in each and the promise does not hold. Two independent routes put
one module into two phases, and the issue recorded only the first:

- ``@oxi.mark.inprocess`` on some of a module's tests, which
  ``partition_inprocess_groups`` splits;
- ``@oxi.arrange`` on some of a module's tests, which
  ``partition_by_fixture_groups`` buckets — **no mark involved**.

Every cell here carries its own control. Two builds is not "one per test", and
one build is only evidence of a fix if the control also builds once: a rule
that serialised the whole suite would report one build and look identical.

The projects are built inline for the reason ``test_arrange_scheduling.py``
gives — a directory per cell keeps ``.oxitest_cache`` cold and stops one cell's
``pyproject.toml`` reaching the next.
"""

from __future__ import annotations

from pathlib import Path

from oxitest import TempDir
from tests.helpers.event_logs import EventLogRun, run_with_event_log

_LOG_ENV = "MODULE_PHASE_LOG"

# A module-tier fixture that records every build and which process built it.
#
# The role is read through ``__main__.__spec__``. A worker is started as
# ``python -m oxitest._bridge.worker``, which binds that module to ``__main__``
# and leaves ``sys.modules["oxitest._bridge.worker"]`` absent, so the obvious
# membership test reports "runner" in every process and cannot fail.
_FIXTURES = """\
from __future__ import annotations

import itertools
import os
import sys
from pathlib import Path

import oxitest as oxi

_COUNTER = itertools.count(1)


def _record(event: str) -> None:
    with Path(f"{{os.environ['{log_env}']}}.{{os.getpid()}}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"{{event}}\\n")


@oxi.fixture(lifetime="module")
def resource() -> str:
    main_spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    is_worker = getattr(main_spec, "name", None) == "oxitest._bridge.worker"
    role = "worker" if is_worker else "runner"
    _record(f"SETUP {{os.getpid()}}-{{next(_COUNTER)}} {{role}}")
    return "resource"
"""

# No module-tier fixture anywhere — the bounded-cost cell.
_FIXTURES_FUNCTION_TIER = """\
from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
def resource() -> str:
    return "resource"
"""

_PYPROJECT = """\
[project]
name = "probe"
version = "0.0.0"

[tool.oxitest]
testpaths = ["probe"]
python_files = ["test_*.py"]
strict = "abort"
min_parallel_tests = 1
"""

# One module, two tests, one of them marked. The mark is the split.
_MIXED_INPROCESS = """\
from __future__ import annotations

import os
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture


def _use(label: str) -> None:
    with Path(f"{os.environ['MODULE_PHASE_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"USE {label} {os.getpid()} -\\n")


@oxi.mark.inprocess
def test_marked(resource: Fixture[str]) -> None:
    _use("marked")
    assert resource == "resource", "the module-tier fixture must be injected"


def test_plain(resource: Fixture[str]) -> None:
    _use("plain")
    assert resource == "resource", "the module-tier fixture must be injected"
"""

# The control: same module, same fixture, no mark. One phase either way.
_CONTROL = """\
from __future__ import annotations

from oxitest import Fixture


def test_one(resource: Fixture[str]) -> None:
    assert resource == "resource", "the module-tier fixture must be injected"


def test_two(resource: Fixture[str]) -> None:
    assert resource == "resource", "the module-tier fixture must be injected"
"""

# The arrangement route. No mark anywhere — @oxi.arrange alone splits it.
_MIXED_ARRANGE = """\
from __future__ import annotations

import oxitest as oxi
from oxitest import Fixture


@oxi.fixture(lifetime="function")
def side() -> str:
    return "side"


@oxi.arrange("side")
def test_arranged(resource: Fixture[str]) -> None:
    assert resource == "resource", "the module-tier fixture must be injected"


def test_plain(resource: Fixture[str]) -> None:
    assert resource == "resource", "the module-tier fixture must be injected"
"""

# The access form the visibility rule exists for: fixture_deps cannot see this.
_MIXED_PROXY = """\
from __future__ import annotations

import oxitest as oxi
from oxitest import Fixtures


@oxi.mark.inprocess
def test_marked(fx: Fixtures) -> None:
    assert fx.probe.resource == "resource", "reached through the fx proxy"


def test_plain(fx: Fixtures) -> None:
    assert fx.probe.resource == "resource", "reached through the fx proxy"
"""

# Many items on each side of the split, rather than one and one.
_MIXED_MANY_ITEMS = """\
from __future__ import annotations

import oxitest as oxi
from oxitest import Fixture


@oxi.mark.inprocess
def test_marked_a(resource: Fixture[str]) -> None:
    assert resource == "resource", "the module-tier fixture must be injected"


@oxi.mark.inprocess
def test_marked_b(resource: Fixture[str]) -> None:
    assert resource == "resource", "the module-tier fixture must be injected"


@oxi.mark.inprocess
def test_marked_c(resource: Fixture[str]) -> None:
    assert resource == "resource", "the module-tier fixture must be injected"


def test_plain_a(resource: Fixture[str]) -> None:
    assert resource == "resource", "the module-tier fixture must be injected"


def test_plain_b(resource: Fixture[str]) -> None:
    assert resource == "resource", "the module-tier fixture must be injected"


def test_plain_c(resource: Fixture[str]) -> None:
    assert resource == "resource", "the module-tier fixture must be injected"
"""

# No module-tier fixture, so nothing to protect and the split must survive.
_MIXED_NO_MODULE_FIXTURE = """\
from __future__ import annotations

import os
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture


def _use(label: str) -> None:
    with Path(f"{os.environ['MODULE_PHASE_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"USE {label} {os.getpid()} -\\n")


@oxi.mark.inprocess
def test_marked(resource: Fixture[str]) -> None:
    _use("marked")
    assert resource == "resource", "a function-tier fixture, rebuilt per test"


def test_plain(resource: Fixture[str]) -> None:
    _use("plain")
    assert resource == "resource", "a function-tier fixture, rebuilt per test"
"""


def _build(tmp: TempDir, cell: str, body: str, *, fixtures: str | None = None) -> Path:
    """Write a one-module project for *cell* and return its root."""
    root = Path(tmp) / f"proj_{cell}"
    pkg = root / "probe"
    pkg.mkdir(parents=True)
    (root / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__fixtures__.py").write_text(
        (fixtures if fixtures is not None else _FIXTURES).format(log_env=_LOG_ENV),
        encoding="utf-8",
    )
    (pkg / "test_mod.py").write_text(body, encoding="utf-8")
    return root


def _run(
    tmp: TempDir, cell: str, body: str, *args: str, fixtures: str | None = None
) -> EventLogRun:
    """Run one cell, defaulting to ``-n 4`` so placement is observable."""
    root = _build(tmp, cell, body, fixtures=fixtures)
    return run_with_event_log(
        root, tmp, _LOG_ENV, *(args or ("-n", "4")), log_name=f"{cell}.log"
    )


def test_a_mixed_declaring_module_builds_its_fixture_once(tmp: TempDir) -> None:
    """The inprocess route, with its control (#1750)."""
    marked = _run(tmp, "mixed_inprocess", _MIXED_INPROCESS)
    control = _run(tmp, "control_inprocess", _CONTROL)

    assert marked.rc == 0, f"the probe project must pass:\n{marked.stdout}"
    assert len(marked.setups) == 1, (
        f"a module that can resolve a module-tier fixture must stay in one dispatch "
        f"phase; two setups means its items reached two fixture sessions and the "
        f"tier's once-per-module promise did not hold. Got {marked.setups}"
    )
    assert len(control.setups) == 1, (
        f"the control must build once both before and after the fix, or one build in "
        f"the marked cell cannot be told from a rule that serialised everything. "
        f"Got {control.setups}"
    )


def test_an_arranged_declaring_module_builds_its_fixture_once(tmp: TempDir) -> None:
    """The arrangement route — no ``@oxi.mark.inprocess`` anywhere (#1750).

    This is the split site the issue never recorded. ``@oxi.arrange`` on one of
    two tests buckets that test into a component and leaves its sibling in the
    parallel remainder, which is two phases and two sessions.
    """
    run = _run(tmp, "mixed_arrange", _MIXED_ARRANGE)

    assert run.rc == 0, f"the probe project must pass:\n{run.stdout}"
    assert len(run.setups) == 1, (
        f"arranging one of a module's tests must not split the module; the second "
        f"setup is a second fixture session reached with no mark involved. "
        f"Got {run.setups}"
    )


def test_a_declaring_module_reached_through_the_proxy_builds_once(
    tmp: TempDir,
) -> None:
    """The access form a usage-keyed rule cannot see (#1750).

    ``fixture_deps`` is built from annotated parameters, so ``fx.<ns>.<name>``
    contributes nothing to it. This cell is what makes the rule's visibility
    basis checkable rather than merely stated.
    """
    run = _run(tmp, "mixed_proxy", _MIXED_PROXY)

    assert run.rc == 0, f"the probe project must pass:\n{run.stdout}"
    assert len(run.setups) == 1, (
        f"proxy access reaches the same fixture and must be protected the same way; "
        f"a usage-keyed rule would report one build here only by accident. "
        f"Got {run.setups}"
    )


def test_a_declaring_module_with_many_items_builds_once(tmp: TempDir) -> None:
    """One module contributing many items to each side of the split (#1750).

    Every other cell puts one item on each side. The rule keys on the module
    rather than on the item count, and this is what says so.
    """
    run = _run(tmp, "many_items", _MIXED_MANY_ITEMS)

    assert run.rc == 0, f"the probe project must pass:\n{run.stdout}"
    assert len(run.setups) == 1, (
        f"three marked items and three plain ones are still one module and must "
        f"reach one session. Got {run.setups}"
    )


def test_a_mixed_module_without_a_module_tier_fixture_still_splits(
    tmp: TempDir,
) -> None:
    """The bounded cost, pinned so a later widening fails loudly (#1750).

    The rule is deliberately narrow: a mixed module with nothing to protect
    keeps its split and its parallelism. Widening it to every mixed module
    would move roughly a hundred tests in this repo onto the coordinator to
    protect fixtures they never resolve.
    """
    run = _run(
        tmp,
        "no_module_fixture",
        _MIXED_NO_MODULE_FIXTURE,
        "-n",
        "4",
        fixtures=_FIXTURES_FUNCTION_TIER,
    )

    assert run.rc == 0, f"the probe project must pass:\n{run.stdout}"
    assert len(run.running_pids) == 2, (
        f"the marked test runs on the coordinator and its sibling in a worker, which "
        f"is two processes; collapsing them would be the blanket rule this avoids. "
        f"Got {run.running_pids}"
    )


def test_the_rule_is_inert_in_a_serial_run(tmp: TempDir) -> None:
    """A serial run has one session for everything, so nothing changes (#1750).

    Every measurement behind this change ran under ``-n``. This cell varies the
    execution mode, which the premise set otherwise held constant.
    """
    run = _run(tmp, "serial", _MIXED_INPROCESS, "--serial")

    assert run.rc == 0, f"the probe project must pass:\n{run.stdout}"
    assert len(run.setups) == 1, (
        f"a serial run owns one session, so the module-tier fixture is built once "
        f"whether or not the rule fires. Got {run.setups}"
    )
