"""A fixture's dependencies dispose at *its* boundary, not the first test's (#1958).

A built-in or plugin-provided fixture resolved as a dependency of a fixture at
``module`` lifetime or wider used to register its cleanup on the constructing
test's ``fn_teardowns`` list, so it was disposed after whichever test happened
to build the owner — while the owner stayed cached and kept being handed out.

Projects are written inline rather than kept under ``data/`` because each needs
a writable log the assertions read back; writing that into a checked-in fixture
directory would mutate the repo during a test run. Same reason as
``test_process_lifetime_builtin_deps.py``, whose rule applies here verbatim: a
green suite is not evidence, only an explicit observation of the resource at
teardown time is.
"""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import TempDir
from tests import helpers

_PYPROJECT = """\
[tool.oxitest]
testpaths = ["probe"]
python_files = ["test_*.py"]
"""

_LOG_HEADER = """\
import os
from pathlib import Path

LOG = Path(os.environ["PROBE_LOG"])


def _record(msg: str) -> None:
    with LOG.open("a") as fh:
        fh.write(msg + "\\n")
"""

# ── P1: TempDir under a module-lifetime fixture ──────────────────────────────

_FIXTURES_TMPDIR = (
    _LOG_HEADER
    + """
from collections.abc import Iterator

import oxitest as oxi
from oxitest import TempDir


@oxi.fixture(lifetime="module")
def workspace(tmp: TempDir) -> Iterator[str]:
    marker = Path(str(tmp.path)) / "marker.txt"
    marker.write_text("alive")
    _record("MOD SETUP")
    yield str(marker)
    _record("MOD TEARDOWN")
"""
)

_TEST_TMPDIR = (
    _LOG_HEADER
    + """
from oxitest import Fixture


def test_a_first(workspace: Fixture[str]) -> None:
    _record(f"A exists={Path(str(workspace)).exists()}")


def test_b_second(workspace: Fixture[str]) -> None:
    _record(f"B exists={Path(str(workspace)).exists()}")
"""
)

# ── P2: TestContext.addfinalizer under a module-lifetime fixture ─────────────

_FIXTURES_CTX = (
    _LOG_HEADER
    + """
from collections.abc import Iterator

import oxitest as oxi
from oxitest import TestContext


@oxi.fixture(lifetime="module")
def owner(ctx: TestContext) -> Iterator[str]:
    _record("MOD SETUP")
    ctx.addfinalizer(lambda: _record("FINALIZER"))
    yield "value"
    _record("MOD TEARDOWN")
"""
)

_TEST_CTX = (
    _LOG_HEADER
    + """
from oxitest import Fixture


def test_a_first(owner: Fixture[str]) -> None:
    _record("A BODY")


def test_b_second(owner: Fixture[str]) -> None:
    _record("B BODY")
"""
)

# ── P3: a plugin FixtureProvider under a module-lifetime fixture ─────────────

_PLUGIN = """\
import os
from pathlib import Path
from typing import Any

from oxitest.plugin import Plugin

LOG = Path(os.environ["PROBE_LOG"])


def _record(msg: str) -> None:
    with LOG.open("a") as fh:
        fh.write(msg + "\\n")


class Conn:
    def __init__(self) -> None:
        self.alive = True


class ConnProvider:
    @property
    def name(self) -> str:
        return "conn"

    @property
    def fixture_type(self) -> type:
        return Conn

    @property
    def scope(self) -> str:
        return "each"

    @property
    def autouse(self) -> bool:
        return False

    def create(self, **_: Any) -> Conn:
        return Conn()

    def teardown(self, **kw: Any) -> None:
        value = kw.get("value")
        if value is not None:
            value.alive = False
        _record("PLUGIN TEARDOWN")


def oxitest_plugin(config=None):
    return Plugin(fixture_providers=(ConnProvider(),))
"""

_PYPROJECT_PLUGIN = """\
[tool.oxitest]
testpaths = ["probe"]
python_files = ["test_*.py"]
plugins = ["probe_plugin"]
"""

_FIXTURES_PLUGIN = (
    _LOG_HEADER
    + """
from collections.abc import Iterator

import oxitest as oxi
from oxitest import Fixture
from probe_plugin import Conn


@oxi.fixture(lifetime="module")
def owner(conn: Fixture[Conn]) -> Iterator[Conn]:
    _record("MOD SETUP")
    yield conn
    _record("MOD TEARDOWN")
"""
)

_TEST_PLUGIN = (
    _LOG_HEADER
    + """
from oxitest import Fixture


def test_a_first(owner: Fixture[object]) -> None:
    _record(f"A alive={owner.alive}")


def test_b_second(owner: Fixture[object]) -> None:
    _record(f"B alive={owner.alive}")
"""
)

# ── P4: the negative pin — function lifetime must be unchanged ──────────────

_FIXTURES_FUNCTION = (
    _LOG_HEADER
    + """
from collections.abc import Iterator

import oxitest as oxi
from oxitest import TempDir


@oxi.fixture(lifetime="function")
def per_test(tmp: TempDir) -> Iterator[str]:
    marker = Path(str(tmp.path)) / "marker.txt"
    marker.write_text("alive")
    _record(f"SETUP {marker}")
    yield str(marker)
"""
)

_TEST_FUNCTION = (
    _LOG_HEADER
    + """
from oxitest import Fixture


def test_a_first(per_test: Fixture[str]) -> None:
    _record(f"A path={per_test}")


def test_b_second(per_test: Fixture[str]) -> None:
    _record(f"B path={per_test}")
"""
)

# ── P5: async wide-lifetime fixture ─────────────────────────────────────────

_FIXTURES_ASYNC = (
    _LOG_HEADER
    + """
from collections.abc import AsyncIterator

import oxitest as oxi
from oxitest import TempDir


@oxi.fixture(lifetime="module")
async def workspace(tmp: TempDir) -> AsyncIterator[str]:
    marker = Path(str(tmp.path)) / "marker.txt"
    marker.write_text("alive")
    _record("MOD SETUP")
    yield str(marker)
    _record("MOD TEARDOWN")
"""
)


def _scaffold(
    root: Path,
    *,
    fixtures: str,
    tests: str,
    pyproject: str = _PYPROJECT,
    plugin: str | None = None,
) -> None:
    """Write a probe project: one fixtures module, one test module."""
    probe = root / "probe"
    probe.mkdir(parents=True)
    (probe / "__fixtures__.py").write_text(fixtures)
    (probe / "test_p.py").write_text(tests)
    (root / "pyproject.toml").write_text(pyproject)
    if plugin is not None:
        pkg = root / "probe_plugin"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(plugin)


def _run(root: Path, log: Path) -> tuple[str, str, int]:
    """Run the scaffolded project serially with the log path exported."""
    return helpers.run_oxitest(
        root, "--serial", env={**os.environ, "PROBE_LOG": str(log)}
    )


def _events(log: Path) -> list[str]:
    """Every recorded line, in order."""
    return log.read_text().splitlines() if log.exists() else []


def test_tempdir_survives_the_first_test(tmp: TempDir) -> None:
    """A module-lifetime fixture's TempDir outlives the test that built it."""
    # Arrange
    root = Path(str(tmp.path))
    log = root / "events.log"
    _scaffold(root, fixtures=_FIXTURES_TMPDIR, tests=_TEST_TMPDIR)

    # Act
    _, _, rc = _run(root, log)

    # Assert
    assert rc == 0, (
        "the probe project must pass; a non-zero rc means the probe itself is "
        "broken, not the behaviour under test"
    )
    assert "B exists=True" in _events(log), (
        "a TempDir resolved for a module-lifetime fixture must live as long as "
        "that fixture; disposing it after the first test hands every later "
        "test a deleted directory while the fixture stays cached (#1958)"
    )


def test_addfinalizer_runs_at_the_owning_fixtures_boundary(tmp: TempDir) -> None:
    """A ctx finalizer registered in a wide fixture fires at that fixture's end."""
    # Arrange
    root = Path(str(tmp.path))
    log = root / "events.log"
    _scaffold(root, fixtures=_FIXTURES_CTX, tests=_TEST_CTX)

    # Act
    _, _, rc = _run(root, log)
    events = _events(log)

    # Assert
    assert rc == 0, (
        "the probe project must pass; a non-zero rc means the probe itself is "
        "broken, not the behaviour under test"
    )
    assert "FINALIZER" in events, (
        "the finalizer must run at all; if it never fires the ordering "
        "assertion below would raise ValueError instead of failing usefully"
    )
    assert events.index("FINALIZER") > events.index("B BODY"), (
        "a finalizer registered through ctx inside a module-lifetime fixture "
        "must run at that fixture's boundary, after every test that used it — "
        "not after whichever test happened to construct it (#1958)"
    )


def test_plugin_fixture_survives_the_first_test(tmp: TempDir) -> None:
    """provider.teardown() fires at the owner's end, not the first test's."""
    # Arrange
    root = Path(str(tmp.path))
    log = root / "events.log"
    _scaffold(
        root,
        fixtures=_FIXTURES_PLUGIN,
        tests=_TEST_PLUGIN,
        pyproject=_PYPROJECT_PLUGIN,
        plugin=_PLUGIN,
    )

    # Act
    _, _, rc = _run(root, log)

    # Assert
    assert rc == 0, (
        "the probe project must pass; a non-zero rc means the probe itself is "
        "broken, not the behaviour under test"
    )
    assert "B alive=True" in _events(log), (
        "provider.teardown() is a disposal hook; firing it after the first "
        "test leaves a module-lifetime fixture handing out a disposed value "
        "for the rest of the module (#1958)"
    )


def test_function_lifetime_still_disposes_per_test(tmp: TempDir) -> None:
    """The negative pin: the function tier is not widened by the fix."""
    # Arrange
    root = Path(str(tmp.path))
    log = root / "events.log"
    _scaffold(root, fixtures=_FIXTURES_FUNCTION, tests=_TEST_FUNCTION)

    # Act
    _, _, rc = _run(root, log)
    paths = [
        line.split("=", 1)[1]
        for line in _events(log)
        if line.startswith(("A path=", "B path="))
    ]

    # Assert
    assert rc == 0, (
        "the probe project must pass; a non-zero rc means the probe itself is "
        "broken, not the behaviour under test"
    )
    assert len(paths) == 2, (
        "both tests must have recorded a path, otherwise the comparison below "
        "is vacuous"
    )
    assert paths[0] != paths[1], (
        "a function-lifetime fixture must still be rebuilt per test — without "
        "this, 'bind the owner's teardown list' is satisfiable by binding "
        "everything to the widest scope in the run (#1958)"
    )
    assert not Path(paths[0]).exists(), (
        "the first test's temp dir must be gone once its test ended; if it "
        "survives, the function tier has been widened by the fix"
    )


def test_async_wide_fixture_survives_the_first_test(tmp: TempDir) -> None:
    """The async route bypasses _instantiate and needs the same owner binding."""
    # Arrange
    root = Path(str(tmp.path))
    log = root / "events.log"
    _scaffold(root, fixtures=_FIXTURES_ASYNC, tests=_TEST_TMPDIR)

    # Act
    _, _, rc = _run(root, log)

    # Assert
    assert rc == 0, (
        "the probe project must pass; a non-zero rc means the probe itself is "
        "broken, not the behaviour under test"
    )
    assert "B exists=True" in _events(log), (
        "the async resolution route reaches _resolve_deps without passing "
        "through _instantiate, so it needs the same owner binding as the sync "
        "route (#1958)"
    )
