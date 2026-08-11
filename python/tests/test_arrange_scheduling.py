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
    """The fixture whose builds and placement the acceptance tests count."""
    _record(f"SETUP {{os.getpid()}}-{{next(_COUNTER)}}")
    return "postgres://probe"
'''

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
        _FIXTURES.format(tier=tier, log_env=_LOG_ENV), encoding="utf-8"
    )
    for n in range(modules):
        (pkg / f"test_use_{n}.py").write_text(form.format(n=n), encoding="utf-8")
    for n in range(inert):
        (pkg / f"test_inert_{n}.py").write_text(
            _FORM_INERT.format(n=n), encoding="utf-8"
        )
    return root


def _run(
    tmp: TempDir, cell: str, *, tier: str, form: str, workers: int = 4
) -> EventLogRun:
    """Build and run one cell, returning its event log."""
    root = _build_project(tmp, cell, tier=tier, form=form)
    return run_with_event_log(
        root, tmp, _LOG_ENV, "-n", str(workers), log_name=f"{cell}.log"
    )


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
