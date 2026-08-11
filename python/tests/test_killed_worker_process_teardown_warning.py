"""A killed worker names the process-lifetime teardowns it never ran (#1777).

Decision 3. A worker owns its process tier alone — no other process will ever
run those teardowns — so killing it drops them permanently. That is accepted: a
graceful SIGTERM was rejected as *unsound*, not as expensive, because a C-level
block never reaches the bytecode boundary where the signal becomes a Python
exception. What is not accepted is doing it silently.

The warning names the fixtures **declared** in the suite rather than the ones
that worker actually built. Only the worker knows which it resolved, and it is
dead; asking would mean a round-trip on a path that must not wait. The message
says as much, and these tests pin that wording — a message claiming the worker
built all of them would be asserting something nothing checked.

Projects are written inline rather than kept under ``data/`` because one of
them deliberately kills its own process, which is not a thing to leave lying
around in a directory the main suite walks.
"""

from __future__ import annotations

from pathlib import Path

from oxitest import TempDir
from tests import helpers

_PYPROJECT = """\
[tool.oxitest]
testpaths = ["{pkg}"]
python_files = ["test_*.py"]
min_parallel_tests = 1
"""

_FIXTURES = """\
from collections.abc import Iterator

import oxitest as oxi


@oxi.fixture(lifetime="process")
def dbpool() -> Iterator[str]:
    yield "pool"


@oxi.fixture(lifetime="process")
def cachedir() -> Iterator[str]:
    yield "cache"
"""

_SUICIDAL_TEST = """\
import os

from oxitest import Fixture


def test_dies(dbpool: Fixture[str]) -> None:
    assert dbpool, "the fixture must be injected before the process dies"
    # os._exit, not sys.exit: this must close stdout without unwinding, which
    # is what the coordinator sees as a lost worker.
    os._exit(1)
"""

_SURVIVING_TEST = """\
from oxitest import Fixture


def test_ok(cachedir: Fixture[str]) -> None:
    assert cachedir, "the fixture must be injected"
"""

_PLAIN_SUICIDAL_TEST = """\
import os


def test_dies() -> None:
    os._exit(1)
"""

_PLAIN_SURVIVING_TEST = """\
def test_ok() -> None:
    assert True, "a second module so the run is genuinely parallel"
"""


def _write(
    root: Path, pkg: str, *, fixtures: str | None, modules: dict[str, str]
) -> None:
    """Scaffold a two-module project, optionally with process-lifetime fixtures."""
    package = root / pkg
    package.mkdir(parents=True)
    if fixtures is not None:
        (package / "__fixtures__.py").write_text(fixtures, encoding="utf-8")
    for name, body in modules.items():
        (package / name).write_text(body, encoding="utf-8")
    (root / "pyproject.toml").write_text(_PYPROJECT.format(pkg=pkg), encoding="utf-8")


def _run(root: Path) -> str:
    """Run the project in parallel, returning stdout+stderr.

    ``--warnings`` is load-bearing, not decoration: without it the reporter
    prints "N warnings (--warnings to expand)" and never the message text, so
    every assertion below would have nothing to match and could not fail.
    """
    stdout, stderr, _rc = helpers.run_oxitest(root, "-n", "2", "--warnings")
    return stdout + stderr


def test_a_killed_worker_warns_and_names_the_declared_fixtures(
    tmp: TempDir,
) -> None:
    """The warning fires, identifies the worker, and lists the tier's fixtures."""
    # Arrange
    root = Path(tmp) / "proj"
    _write(
        root,
        "killed",
        fixtures=_FIXTURES,
        modules={"test_a.py": _SUICIDAL_TEST, "test_b.py": _SURVIVING_TEST},
    )

    # Act
    output = _run(root)

    # Assert
    assert "process-lifetime teardown" in output, (
        f"a worker died holding process-lifetime fixtures and nothing said so. "
        f"That silence is the whole point of decision 3 — the teardowns are "
        f"gone and no other process will run them.\noutput:\n{output}"
    )
    for name in ("dbpool", "cachedir"):
        assert name in output, (
            f"the warning must name {name!r} so the user knows what was left "
            f"un-torn-down; a bare 'some fixtures leaked' is not actionable"
            f"\noutput:\n{output}"
        )
    assert "may have built only some of them" in output, (
        "the warning must say the list is what the suite *declares*, not what "
        "this worker built. The coordinator cannot know the latter — the worker "
        "is dead — and a message that claimed otherwise would be asserting "
        f"something nothing verified\noutput:\n{output}"
    )


def test_a_suite_without_the_tier_sees_no_warning(tmp: TempDir) -> None:
    """The negative pin: same crash, no declarations, no noise.

    Without this, the warning could be unconditional and the test above would
    still pass — every suite that ever loses a worker would then be told about
    a tier it does not use.
    """
    # Arrange — identical shape, minus the __fixtures__.py
    root = Path(tmp) / "proj"
    _write(
        root,
        "plain",
        fixtures=None,
        modules={
            "test_a.py": _PLAIN_SUICIDAL_TEST,
            "test_b.py": _PLAIN_SURVIVING_TEST,
        },
    )

    # Act
    output = _run(root)

    # Assert
    assert "process-lifetime teardown" not in output, (
        f"a suite declaring no process-lifetime fixtures must hear nothing "
        f"about them, even when it loses a worker\noutput:\n{output}"
    )
