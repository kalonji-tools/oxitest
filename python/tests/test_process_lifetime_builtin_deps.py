"""A process-lifetime fixture may depend on a session-scoped builtin (#1777).

``lifetime="session"`` fixtures cache in ``_process_scope`` and drain at
``end_process``; the builtins (``_TempDirFactoryFixture``) cache in
``_session_scope`` and drain at ``end_task``. Splitting those buckets inverted
a teardown ordering that used to hold *by construction*: both tiers shared one
``_Scope``, so its reverse-order drain always ran the dependent before the
builtin it was built on.

Without the routing in ``resolve_by_source``, a process-lifetime fixture whose
teardown touches a factory directory writes into a path removed at the task
boundary — and ``TempDirFactory.close()`` uses ``shutil.rmtree(...,
ignore_errors=True)``, so **nothing reports it**. That silence is why this file
exists: a green suite is not evidence here, only an explicit observation of the
directory at teardown time is.

Projects are written inline rather than kept under ``data/`` because each needs
a writable log the assertions read back; writing that into a checked-in fixture
directory would mutate the repo during a test run.
"""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import TempDir
from tests import helpers

_FIXTURES = """\
import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture, TempDirFactory

LOG = Path(os.environ["PROBE_LOG"])


def _record(event: str) -> None:
    with LOG.open("a") as fh:
        fh.write(event + "\\n")


@oxi.fixture(lifetime="session")
def workspace(factory: Fixture[TempDirFactory]) -> Iterator[Path]:
    d = factory.mktemp("ws").path
    # How many dirs this factory has handed out, counted the moment after our
    # own mktemp. A factory shared with the plain test below would already be
    # holding that test's directory too.
    _record(f"SETUP dirs={len(factory.dirs)}")
    yield d
    # The whole point: is the directory still there when the process-lifetime
    # owner is disposed?
    _record(f"TEARDOWN exists={d.exists()}")
"""

_TEST_MODULE = """\
from pathlib import Path

from oxitest import Fixture


def test_uses_workspace(workspace: Fixture[Path]) -> None:
    assert workspace.exists(), "the workspace must exist while the test runs"
"""

_TEST_MODULE_WITH_PLAIN_CONSUMER = """\
import os
from pathlib import Path

from oxitest import Fixture, TempDirFactory

LOG = Path(os.environ["PROBE_LOG"])


def test_a_plain_consumer_first(factory: TempDirFactory) -> None:
    d = factory.mktemp("plain").path
    with LOG.open("a") as fh:
        fh.write(f"PLAIN dirs={len(factory.dirs)}\\n")
    assert d.exists(), "the plain temp dir must exist during the test"


def test_b_uses_workspace(workspace: Fixture[Path]) -> None:
    assert workspace.exists(), "the workspace must exist while the test runs"
"""

_PYPROJECT = """\
[tool.oxitest]
testpaths = ["probe"]
python_files = ["test_*.py"]
"""


def _write_project(root: Path, test_module: str) -> None:
    """Scaffold a project whose process-lifetime fixture uses the factory."""
    probe = root / "probe"
    probe.mkdir(parents=True)
    (probe / "__fixtures__.py").write_text(_FIXTURES)
    (probe / "test_p.py").write_text(test_module)
    (root / "pyproject.toml").write_text(_PYPROJECT)


def _run(root: Path, log: Path) -> tuple[str, str, int]:
    """Run the scaffolded project serially with the log path exported."""
    return helpers.run_oxitest(
        root, "--serial", env={**os.environ, "PROBE_LOG": str(log)}
    )


def _lines(log: Path, prefix: str) -> list[str]:
    """Recorded lines with *prefix*, prefix stripped."""
    if not log.exists():
        return []
    return [
        ln.removeprefix(prefix)
        for ln in log.read_text().splitlines()
        if ln.startswith(prefix)
    ]


def test_process_lifetime_teardown_still_sees_its_factory_directory(
    tmp: TempDir,
) -> None:
    """The dependency survives to the process-lifetime fixture's teardown.

    Before the routing fix the builtin drained at ``end_task`` and this fixture
    at ``end_process``, so the directory was already removed — surfacing as
    ``exists=False`` rather than as any kind of error.
    """
    # Arrange
    root = Path(tmp) / "proj"
    _write_project(root, _TEST_MODULE)
    log = Path(tmp) / "probe.log"

    # Act
    out, err, rc = _run(root, log)

    # Assert
    assert rc == 0, f"the probe project must pass\nstdout:\n{out}\nstderr:\n{err}"
    observations = _lines(log, "TEARDOWN ")
    assert observations == ["exists=True"], (
        f"expected exactly one teardown observing a live directory, got "
        f"{observations} — 'exists=False' means the session-scoped builtin was "
        f"disposed at end_task while its process-lifetime dependent still held "
        f"the value, a use-after-teardown that TempDirFactory.close() swallows "
        f"via ignore_errors and therefore never reports"
    )


def test_a_plain_test_and_a_process_fixture_get_distinct_factories(
    tmp: TempDir,
) -> None:
    """The routing keys on who asked, so ordinary tests keep the task-scoped factory.

    The negative pin for the fix. Sending *every* builtin to the process scope
    would satisfy the test above while silently giving every suite
    process-lifetime temp dirs, so something must distinguish the two routes.

    Comparing the two directories would not: ``mktemp`` calls ``mkdtemp``, so
    two calls differ even when they come from one factory. ``factory.dirs`` is
    the real discriminator — it is per-instance state, and a shared factory
    would already be holding the plain test's directory when the fixture counts.
    """
    # Arrange — the plain consumer runs first (test_a_… before test_b_…), so a
    # shared factory would show 2 by the time the fixture is built.
    root = Path(tmp) / "proj"
    _write_project(root, _TEST_MODULE_WITH_PLAIN_CONSUMER)
    log = Path(tmp) / "probe.log"

    # Act
    out, err, rc = _run(root, log)

    # Assert
    assert rc == 0, f"the probe project must pass\nstdout:\n{out}\nstderr:\n{err}"
    plain = _lines(log, "PLAIN ")
    setup = _lines(log, "SETUP ")
    assert plain == ["dirs=1"], (
        f"the plain test must be the first consumer of its own factory, got "
        f"{plain} — if this is not 1 the ordering assumption below is wrong and "
        f"the next assertion proves nothing"
    )
    assert setup == ["dirs=1"], (
        f"the process-lifetime fixture saw {setup} directories on its factory, "
        f"expected exactly its own — anything higher means it received the same "
        f"instance the plain test used, so every suite would inherit "
        f"process-lifetime temp dirs whether or not it declared the tier"
    )
