"""Contract tests for kalonji-tools/oxitest#1775: function tier builds once per test.

ADR-0009's lifetime table says ``function`` means "once per test in the
fixture's B1 scope". Three access routes — the autouse pass, ``Fixture[T]``
parameter injection, and ``fx.<ns>.<name>`` proxy access — must therefore
share one per-test cache; before the fix each route built its own instance.

Runs oxitest as a subprocess on inline projects and asserts on a log the
fixtures write themselves (slice-2 precedent): the question is what the
fixture actually did, not how a reporter phrased it. Builds are identified by
a module-level counter, never ``id()`` — a freed instance's address can be
reused by the very next allocation, which would fake cross-test sharing.
All runs are ``--serial``: the assertions are ordering-sensitive.
"""

from __future__ import annotations

from pathlib import Path

from oxitest import TempDir
from tests import helpers

_PYPROJECT = (
    "[tool.oxitest]\n"
    'testpaths = ["suite"]\n'
    'python_files = ["test_*.py"]\n'
    'strict = "abort"\n'
)


def _scaffold(root: Path, files: dict[str, str]) -> None:
    """Write *files* (relative path → source) under *root*, creating dirs."""
    for rel_path, content in files.items():
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _events(log: Path) -> tuple[str, ...]:
    """The fixture-written event log, one event per line."""
    return tuple(log.read_text(encoding="utf-8").splitlines()) if log.exists() else ()


def _tagged(events: tuple[str, ...], tag: str) -> tuple[str, ...]:
    """Payloads of every event carrying *tag* (e.g. ``SETUP 3`` → ``3``)."""
    prefix = f"{tag} "
    return tuple(e.removeprefix(prefix) for e in events if e.startswith(prefix))


def _counting_fixtures_module(log: Path) -> str:
    """A ``__fixtures__.py`` whose function-lifetime yield fixture logs itself."""
    return (
        "from __future__ import annotations\n"
        "import itertools\n"
        "import pathlib\n"
        "from collections.abc import Iterator\n"
        "import oxitest as oxi\n"
        "from suite._kinds import Token\n\n"
        f"LOG = pathlib.Path({str(log)!r})\n"
        "_COUNTER = itertools.count(1)\n\n\n"
        '@oxi.fixture(lifetime="function")\n'
        "def tracked() -> Iterator[Token]:\n"
        "    token = Token(next(_COUNTER))\n"
        "    with LOG.open('a', encoding='utf-8') as fh:\n"
        "        fh.write(f'SETUP {token.seq}\\n')\n"
        "    yield token\n"
        "    with LOG.open('a', encoding='utf-8') as fh:\n"
        "        fh.write(f'TEARDOWN {token.seq}\\n')\n"
    )


def _async_counting_fixtures_module(log: Path) -> str:
    """The async twin of :func:`_counting_fixtures_module`.

    An ``async def`` yield fixture at ``function`` lifetime, logging its own
    setup and teardown. Shared by the two async sections — (f) proves the proxy
    routes converge on one build, (g) pins that adding the param route does not.
    """
    return (
        "from __future__ import annotations\n"
        "import itertools\n"
        "import pathlib\n"
        "from collections.abc import AsyncIterator\n"
        "import oxitest as oxi\n"
        "from suite._kinds import Token\n\n"
        f"LOG = pathlib.Path({str(log)!r})\n"
        "_COUNTER = itertools.count(1)\n\n\n"
        '@oxi.fixture(lifetime="function")\n'
        "async def channel() -> AsyncIterator[Token]:\n"
        "    token = Token(next(_COUNTER))\n"
        "    with LOG.open('a', encoding='utf-8') as fh:\n"
        "        fh.write(f'SETUP {token.seq}\\n')\n"
        "    yield token\n"
        "    with LOG.open('a', encoding='utf-8') as fh:\n"
        "        fh.write(f'TEARDOWN {token.seq}\\n')\n"
    )


_KINDS = (
    "from __future__ import annotations\n\n\n"
    "class Token:\n"
    "    def __init__(self, seq: int) -> None:\n"
    "        self.seq = seq\n"
)


# ── (a) Fixture[T] + fx.<ns>.<name> in one test: one build, same object ──────


def test_param_and_proxy_share_one_build(tmp: TempDir) -> None:
    """A test taking both routes gets one build and one object.

    This is the shipped-surface repro from the issue: before the fix the
    parameter and the proxy each built their own instance, so a side effect
    applied through one route was invisible through the other.
    """
    # Arrange
    root = Path(tmp) / "proj"
    log = Path(tmp) / "events.log"
    _scaffold(
        root,
        {
            "pyproject.toml": _PYPROJECT,
            "suite/__init__.py": "",
            "suite/_kinds.py": _KINDS,
            "suite/db/__init__.py": "",
            "suite/db/__fixtures__.py": (
                "from __future__ import annotations\n"
                "import itertools\n"
                "import pathlib\n"
                "import oxitest as oxi\n"
                "from suite._kinds import Token\n\n"
                f"LOG = pathlib.Path({str(log)!r})\n"
                "_COUNTER = itertools.count(1)\n\n\n"
                '@oxi.fixture(lifetime="function")\n'
                "def marker() -> Token:\n"
                "    token = Token(next(_COUNTER))\n"
                "    with LOG.open('a', encoding='utf-8') as fh:\n"
                "        fh.write(f'SETUP {token.seq}\\n')\n"
                "    return token\n"
            ),
            "suite/db/test_both.py": (
                "from __future__ import annotations\n"
                "from oxitest import Fixture, Fixtures\n"
                "from suite._kinds import Token\n\n\n"
                "def test_both_routes(marker: Fixture[Token], fx: Fixtures) -> None:\n"
                "    proxied = fx.db.marker\n"
                "    assert proxied is marker, (\n"
                "        'the Fixture[T] parameter and fx.db.marker must be the "
                "same object — ADR-0009 says a function-lifetime fixture builds "
                "once per test, so two objects means two builds'\n"
                "    )\n"
            ),
        },
    )

    # Act
    out, err, rc = helpers.run_oxitest(None, "--serial", cwd=str(root))
    events = _events(log)

    # Assert
    assert rc == 0, (
        f"the in-project identity assertion failed (rc={rc}) — the two access "
        f"routes handed the test different instances\n"
        f"stdout:\n{out}\nstderr:\n{err}"
    )
    assert len(_tagged(events, "SETUP")) == 1, (
        f"fixture was built {len(_tagged(events, 'SETUP'))} times inside one "
        f"test (events={events}) — every access route past the first must be "
        "a per-test cache hit, or side effects land on instances the test "
        "never sees"
    )


# ── (b) autouse build is the instance the test observes via fx. ──────────────


def test_autouse_instance_is_what_the_test_observes(tmp: TempDir) -> None:
    """An autouse function-lifetime fixture reached via ``fx.`` builds once.

    Before the fix the autouse pass built instance 1 for its side effects and
    ``fx.<name>`` built instance 2 for the test — so whatever the autouse pass
    prepared was thrown away invisibly.
    """
    # Arrange
    log = Path(tmp) / "events.log"
    (Path(tmp) / "__fixtures__.py").write_text(
        "from __future__ import annotations\n"
        "import itertools\n"
        "import pathlib\n"
        "from oxitest import fixture\n\n"
        f"LOG = pathlib.Path({str(log)!r})\n"
        "_COUNTER = itertools.count(1)\n\n\n"
        "class Stamp:\n"
        "    def __init__(self, seq: int) -> None:\n"
        "        self.seq = seq\n\n\n"
        "@fixture(lifetime='function', autouse=True)\n"
        "def stamp() -> Stamp:\n"
        "    instance = Stamp(next(_COUNTER))\n"
        "    with LOG.open('a', encoding='utf-8') as fh:\n"
        "        fh.write(f'SETUP {instance.seq}\\n')\n"
        "    return instance\n",
        encoding="utf-8",
    )
    (Path(tmp) / "test_autouse.py").write_text(
        "from __future__ import annotations\n"
        "import pathlib\n"
        "from oxitest import Fixtures\n\n"
        f"LOG = pathlib.Path({str(log)!r})\n\n\n"
        "def test_observes_autouse_instance(fx: Fixtures) -> None:\n"
        "    observed = fx.stamp\n"
        "    with LOG.open('a', encoding='utf-8') as fh:\n"
        "        fh.write(f'USE {observed.seq}\\n')\n"
        "    assert observed.seq is not None, (\n"
        "        'the stamp must carry its build number so the outer test can "
        "match it against the autouse pass'\n"
        "    )\n",
        encoding="utf-8",
    )

    # Act
    out, err, rc = helpers.run_oxitest(tmp, "--serial")
    events = _events(log)

    # Assert
    assert rc == 0, f"run failed (rc={rc})\nstdout:\n{out}\nstderr:\n{err}"
    setups = _tagged(events, "SETUP")
    uses = _tagged(events, "USE")
    assert len(setups) == 1, (
        f"autouse fixture was built {len(setups)} times in one test "
        f"(events={events}) — the fx. access must hit the per-test cache the "
        "autouse pass populated, not build a second instance"
    )
    assert uses == setups, (
        f"the test observed instance {uses} but the autouse pass built "
        f"{setups} — the autouse side effect ran on an instance the test "
        "never saw, which is exactly the defect #1775 describes"
    )


# ── (c) yield-fixture teardown runs exactly once per test ────────────────────


def test_yield_teardown_runs_exactly_once_per_test(tmp: TempDir) -> None:
    """Two tests, both routes each: one SETUP and one TEARDOWN per test.

    Before the fix each route's build registered its own teardown, so a test
    touching both routes drained two generators. The per-test cache must not
    double-register either: exactly one registration per build.
    """
    # Arrange
    root = Path(tmp) / "proj"
    log = Path(tmp) / "events.log"
    test_body = (
        "from __future__ import annotations\n"
        "from oxitest import Fixture, Fixtures\n"
        "from suite._kinds import Token\n\n\n"
        "def test_first(tracked: Fixture[Token], fx: Fixtures) -> None:\n"
        "    assert fx.suite.tracked is tracked, (\n"
        "        'both routes must observe the same per-test instance'\n"
        "    )\n\n\n"
        "def test_second(tracked: Fixture[Token], fx: Fixtures) -> None:\n"
        "    assert fx.suite.tracked is tracked, (\n"
        "        'both routes must observe the same per-test instance'\n"
        "    )\n"
    )
    _scaffold(
        root,
        {
            "pyproject.toml": _PYPROJECT,
            "suite/__init__.py": "",
            "suite/_kinds.py": _KINDS,
            "suite/__fixtures__.py": _counting_fixtures_module(log),
            "suite/test_teardown.py": test_body,
        },
    )

    # Act
    out, err, rc = helpers.run_oxitest(None, "--serial", cwd=str(root))
    events = _events(log)

    # Assert
    assert rc == 0, f"run failed (rc={rc})\nstdout:\n{out}\nstderr:\n{err}"
    setups = _tagged(events, "SETUP")
    teardowns = _tagged(events, "TEARDOWN")
    assert len(setups) == 2, (
        f"expected one build per test across 2 tests, got {len(setups)} "
        f"(events={events}) — more means a route bypassed the per-test cache"
    )
    assert len(teardowns) == 2, (
        f"expected one teardown per test across 2 tests, got {len(teardowns)} "
        f"(events={events}) — more means a build was double-registered, fewer "
        "means a cached instance's cleanup was skipped"
    )
    assert events == (
        f"SETUP {setups[0]}",
        f"TEARDOWN {setups[0]}",
        f"SETUP {setups[1]}",
        f"TEARDOWN {setups[1]}",
    ), (
        f"event order {events} is not build/teardown per test — a teardown "
        "that drifts past its own test's boundary means the instance outlived "
        "the test"
    )


# ── (d) no cross-test leakage: consecutive tests get distinct instances ──────


def test_consecutive_tests_get_distinct_instances(tmp: TempDir) -> None:
    """The per-test cache is disposed at the test boundary.

    Single-route on purpose: if disposal ever regressed, the second test would
    silently reuse the first test's instance and the build count would drop to
    one — this is the test that catches a cache outliving its test.
    """
    # Arrange
    root = Path(tmp) / "proj"
    log = Path(tmp) / "events.log"
    test_body = (
        "from __future__ import annotations\n"
        "from oxitest import Fixtures\n\n\n"
        "def test_one(fx: Fixtures) -> None:\n"
        "    assert fx.suite.tracked.seq > 0, 'fixture must inject a live token'\n\n\n"
        "def test_two(fx: Fixtures) -> None:\n"
        "    assert fx.suite.tracked.seq > 0, 'fixture must inject a live token'\n"
    )
    _scaffold(
        root,
        {
            "pyproject.toml": _PYPROJECT,
            "suite/__init__.py": "",
            "suite/_kinds.py": _KINDS,
            "suite/__fixtures__.py": _counting_fixtures_module(log),
            "suite/test_leak.py": test_body,
        },
    )

    # Act
    out, err, rc = helpers.run_oxitest(None, "--serial", cwd=str(root))
    events = _events(log)

    # Assert
    assert rc == 0, f"run failed (rc={rc})\nstdout:\n{out}\nstderr:\n{err}"
    setups = _tagged(events, "SETUP")
    assert len(setups) == 2, (
        f"2 tests must mean 2 builds, got {len(setups)} (events={events}) — "
        "fewer means the per-test cache leaked an instance into the next test"
    )
    assert setups[0] != setups[1], (
        f"both tests received build {setups[0]} — function lifetime ends at "
        "the test boundary, so sharing across tests is a scope leak"
    )


# ── (e) wider tiers keep their build counts ──────────────────────────────────


def test_wider_tiers_keep_their_build_counts(tmp: TempDir) -> None:
    """Module / package / session builds are untouched by the fix.

    Four tests across two modules touch every wider tier through both routes.
    The function-tier cache lives in ``_scope_for``'s final fallthrough; this
    pins that no wider branch was disturbed on the way there.

    Covered the legacy ``shared`` tier too, via a ``conftest.py`` scaffolded
    beside the declarations. That half is gone with the tier (#1720); the three
    tiers a ``@oxi.fixture`` can declare are what remain.
    """
    # Arrange
    root = Path(tmp) / "proj"
    log = Path(tmp) / "events.log"
    kinds = (
        "from __future__ import annotations\n\n\n"
        "class ModRes:\n"
        "    pass\n\n\n"
        "class PkgRes:\n"
        "    pass\n\n\n"
        "class SessRes:\n"
        "    pass\n"
    )
    fixtures_module = (
        "from __future__ import annotations\n"
        "import pathlib\n"
        "import oxitest as oxi\n"
        "from suite._kinds import ModRes, PkgRes, SessRes\n\n"
        f"LOG = pathlib.Path({str(log)!r})\n\n\n"
        "def _record(event: str) -> None:\n"
        "    with LOG.open('a', encoding='utf-8') as fh:\n"
        "        fh.write(f'{event}\\n')\n\n\n"
        '@oxi.fixture(lifetime="module")\n'
        "def mod_res() -> ModRes:\n"
        "    _record('MODULE-SETUP')\n"
        "    return ModRes()\n\n\n"
        '@oxi.fixture(lifetime="package")\n'
        "def pkg_res() -> PkgRes:\n"
        "    _record('PACKAGE-SETUP')\n"
        "    return PkgRes()\n\n\n"
        '@oxi.fixture(lifetime="process")\n'
        "def sess_res() -> SessRes:\n"
        "    _record('SESSION-SETUP')\n"
        "    return SessRes()\n"
    )

    def _test_module(mod: str) -> str:
        return (
            "from __future__ import annotations\n"
            "from oxitest import Fixture, Fixtures\n"
            "from suite._kinds import ModRes\n\n\n"
            f"def test_{mod}_one(mod_res: Fixture[ModRes], fx: Fixtures) -> None:\n"
            "    assert fx.suite.mod_res is mod_res, (\n"
            "        'module tier already unified both routes before #1775 — "
            "that must not change'\n"
            "    )\n"
            "    assert fx.suite.pkg_res is not None, 'package tier must inject'\n"
            "    assert fx.suite.sess_res is not None, 'session tier must inject'\n"
            "\n\n"
            f"def test_{mod}_two(fx: Fixtures) -> None:\n"
            "    assert fx.suite.mod_res is not None, 'module tier must inject'\n"
            "    assert fx.suite.pkg_res is not None, 'package tier must inject'\n"
            "    assert fx.suite.sess_res is not None, 'session tier must inject'\n"
        )

    _scaffold(
        root,
        {
            "pyproject.toml": _PYPROJECT,
            "suite/__init__.py": "",
            "suite/_kinds.py": kinds,
            "suite/__fixtures__.py": fixtures_module,
            "suite/test_alpha.py": _test_module("alpha"),
            "suite/test_beta.py": _test_module("beta"),
        },
    )

    # Act
    out, err, rc = helpers.run_oxitest(None, "--serial", cwd=str(root))
    events = _events(log)

    # Assert
    assert rc == 0, f"run failed (rc={rc})\nstdout:\n{out}\nstderr:\n{err}"
    counts = {
        "MODULE-SETUP": 2,  # one per module
        "PACKAGE-SETUP": 1,  # one per anchor subtree
        "SESSION-SETUP": 1,  # one per worker; --serial means one worker
    }
    for tag, expected in counts.items():
        actual = sum(1 for e in events if e == tag)
        assert actual == expected, (
            f"{tag}: expected {expected} builds, got {actual} "
            f"(events={events}) — the function-tier cache must live in "
            "_scope_for's final fallthrough only; a changed wider-tier count "
            "means one of the earlier branches was disturbed"
        )


# ── (f) async proxy routes share the per-test cache ──────────────────────────


def test_async_proxy_routes_build_once(tmp: TempDir) -> None:
    """``await fx.suite.channel`` and ``await fx.channel`` build once per test.

    The two spellings produce two distinct memoising handles, so the handle's
    own memoisation cannot unify them — only the per-test cache can. Teardown
    must still run exactly once, inside the test, on the test's own loop.
    """
    # Arrange
    root = Path(tmp) / "proj"
    log = Path(tmp) / "events.log"
    test_body = (
        "from __future__ import annotations\n"
        "from oxitest import Fixtures\n\n\n"
        "async def test_two_handles(fx: Fixtures) -> None:\n"
        "    qualified = await fx.suite.channel\n"
        "    shortcut = await fx.channel\n"
        "    assert shortcut is qualified, (\n"
        "        'the qualified and shortcut proxy routes must observe the "
        "same per-test instance — two distinct handles must converge on the "
        "per-test cache'\n"
        "    )\n"
    )
    _scaffold(
        root,
        {
            "pyproject.toml": _PYPROJECT,
            "suite/__init__.py": "",
            "suite/_kinds.py": _KINDS,
            "suite/__fixtures__.py": _async_counting_fixtures_module(log),
            "suite/test_async.py": test_body,
        },
    )

    # Act
    out, err, rc = helpers.run_oxitest(None, "--serial", cwd=str(root))
    events = _events(log)

    # Assert
    assert rc == 0, (
        f"the in-project identity assertion failed (rc={rc}) — the two "
        f"handles built separate instances\nstdout:\n{out}\nstderr:\n{err}"
    )
    setups = _tagged(events, "SETUP")
    teardowns = _tagged(events, "TEARDOWN")
    assert len(setups) == 1, (
        f"async fixture was built {len(setups)} times in one test "
        f"(events={events}) — the second handle must hit the per-test cache"
    )
    assert teardowns == setups, (
        f"teardowns {teardowns} do not match setups {setups} — the single "
        "build must be drained exactly once, on the loop that created it"
    )


# ── (g) async param + proxy routes still build twice (#1805) ─────────────────


def test_async_param_and_proxy_routes_still_build_twice(tmp: TempDir) -> None:
    """Mixing ``Fixture[T]`` and ``await fx.<name>`` on one async fixture builds twice.

    A deferred defect against ADR-0009, not a design decision: the param route
    hands an uncached coroutine to the execution middleware, so it cannot reach
    the per-test cache the proxy route uses. Convergence is gated on #1740.

    The scaffolded test asserts the **contract** and is marked
    ``xfail(strict=True)``, so the suite never states a rule ADR-0009 denies.
    When #1740 lands the inner test xpasses, a strict xpass fails the run, and
    the ``rc == 0`` assertion below is what reports it.
    """
    # Arrange
    root = Path(tmp) / "proj"
    log = Path(tmp) / "events.log"
    test_body = (
        "from __future__ import annotations\n"
        "import oxitest as oxi\n"
        "from oxitest import Fixture, Fixtures\n"
        "from suite._kinds import Token\n\n\n"
        "@oxi.mark.xfail(\n"
        "    reason='#1805 — the async param route hands an uncached "
        "coroutine to the execution middleware, so it cannot share the "
        "per-test cache the proxy route uses; convergence is gated on "
        "#1740',\n"
        "    strict=True,\n"
        ")\n"
        "async def test_param_and_proxy_converge(\n"
        "    channel: Fixture[Token], fx: Fixtures\n"
        ") -> None:\n"
        "    proxied = await fx.channel\n"
        "    assert channel is proxied, (\n"
        "        'ADR-0009: function lifetime is once per test in the "
        "fixture B1 scope, whatever route reaches it'\n"
        "    )\n"
    )
    _scaffold(
        root,
        {
            "pyproject.toml": _PYPROJECT,
            "suite/__init__.py": "",
            "suite/_kinds.py": _KINDS,
            "suite/__fixtures__.py": _async_counting_fixtures_module(log),
            "suite/test_async.py": test_body,
        },
    )

    # Act
    out, err, rc = helpers.run_oxitest(None, "--serial", cwd=str(root))
    events = _events(log)

    # Assert
    assert rc == 0, (
        f"rc={rc} means the inner test did not xfail. If it xpassed, the param "
        "and proxy routes have converged (#1740 landed) — drop the xfail "
        "marker and this test becomes the permanent regression test. Any "
        f"other failure is a real break.\nstdout:\n{out}\nstderr:\n{err}"
    )
    assert "xfailed" in out, (
        "the run must report xfailed specifically — 'passed' would mean the "
        "marker was dropped and the gap silently closed, or that the fixture "
        f"never ran\nstdout:\n{out}"
    )
    setups = _tagged(events, "SETUP")
    teardowns = _tagged(events, "TEARDOWN")
    assert len(setups) == 2, (
        f"the mixed-route access must build exactly twice, once per route "
        f"(events={events}) — a third build would be a new leak that this "
        "test's xfail alone would absorb silently"
    )
    assert sorted(teardowns) == sorted(setups), (
        f"teardowns {teardowns} do not match setups {setups} — both instances "
        "must be drained; a leaked async teardown is a real bug that the "
        "deferred convergence does not excuse"
    )
