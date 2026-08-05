"""Integration: static plugin fixtures declared in a plugin's __fixtures__.py (#1717).

No pip install anywhere — the plugin package is written into the tmp dir and
activated through `plugins = [...]`, relying on rootdir being importable
(#1780). Same trick as ``test_plugins.py``, one directory deeper.
"""

from __future__ import annotations

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ

_PLUGIN_ENTRY = """from oxitest.plugin import Plugin


def oxitest_plugin(config=None):
    return Plugin()
"""

_CONN_FIXTURE = """import oxitest as oxi


class Conn:
    def __init__(self) -> None:
        self.dsn = "from-plugin"


@oxi.fixture(lifetime="module")
def conn() -> Conn:
    return Conn()
"""


def _write_plugin_project(
    tmp: TempDir,
    *,
    fixtures: str = _CONN_FIXTURE,
    tests: dict[str, str],
    settings: str = "",
    testpaths: str = '["tests"]',
) -> None:
    """Scaffold a project whose plugin package declares fixtures."""
    integ.write_project(
        tmp,
        tests={},
        pyproject=(
            f"[tool.oxitest]\n"
            f"testpaths = {testpaths}\n"
            f"plugins = ['my_plugin']\n"
            f"{settings}"
        ),
        extra_files={
            "my_plugin/__init__.py": _PLUGIN_ENTRY,
            "my_plugin/__fixtures__.py": fixtures,
            **{f"tests/{name}": code for name, code in tests.items()},
        },
    )


# ── access routes ─────────────────────────────────────────────────────────────


def test_plugin_fixture_resolves_via_qualified_path(tmp: TempDir) -> None:
    """fx.<plugin>.<fixture> reaches a fixture declared in an installed package."""
    _write_plugin_project(
        tmp,
        tests={
            "test_q.py": (
                "from oxitest import Fixtures\n\n\n"
                "def test_qualified(fx: Fixtures) -> None:\n"
                "    assert fx.my_plugin.conn.dsn == 'from-plugin', (\n"
                "        'the qualified route is the documented way to reach a "
                "plugin fixture'\n"
                "    )\n"
            )
        },
    )

    out, _, rc = helpers.run_oxitest(tmp, cwd=".")

    integ.assert_passed(out, rc, count=1)


def test_plugin_fixture_resolves_via_shortcut(tmp: TempDir) -> None:
    """fx.<fixture> is unconditionally legal — #1714 retracted the strict dial."""
    _write_plugin_project(
        tmp,
        tests={
            "test_s.py": (
                "from oxitest import Fixtures\n\n\n"
                "def test_shortcut(fx: Fixtures) -> None:\n"
                "    assert fx.conn.dsn == 'from-plugin', (\n"
                "        'the shortcut needs no opt-in; a strict dial gating it "
                "was never a StrictMode value'\n"
                "    )\n"
            )
        },
    )

    out, _, rc = helpers.run_oxitest(tmp, cwd=".")

    integ.assert_passed(out, rc, count=1)


def test_plugin_fixture_resolves_via_type_injection(tmp: TempDir) -> None:
    """A ``Fixture[T]`` parameter named after the fixture resolves it.

    The parameter name matters. A declaration-based fixture — ``ModuleSource``
    or ``PluginModuleSource`` — resolves by *name*, and the collection-time
    validator rejects a parameter whose name is not a known fixture. Verified
    against a plain user ``__fixtures__.py``: renaming the parameter fails
    there too, so this is oxitest's rule for declared fixtures rather than
    anything specific to plugins.

    That is also why the instantiator's two arms cannot be told apart by any
    ordinary test — see the note on ``_fixture_instantiator.py``.
    """
    _write_plugin_project(
        tmp,
        tests={
            "test_t.py": (
                "from oxitest import Fixture\n"
                "from my_plugin.__fixtures__ import Conn\n\n\n"
                "def test_injected(conn: Fixture[Conn]) -> None:\n"
                "    assert conn.dsn == 'from-plugin', (\n"
                "        'Fixture[T] injection must build a plugin fixture the "
                "same way it builds a user one'\n"
                "    )\n"
            )
        },
    )

    out, _, rc = helpers.run_oxitest(tmp, cwd=".")

    integ.assert_passed(out, rc, count=1)


def test_plugin_fixture_is_ambient_in_a_deep_subdirectory(tmp: TempDir) -> None:
    """Ambient means every test in the run, at any depth — no anchor filters it."""
    _write_plugin_project(
        tmp,
        tests={
            "deep/nested/test_far.py": (
                "from oxitest import Fixtures\n\n\n"
                "def test_deep(fx: Fixtures) -> None:\n"
                "    assert fx.conn.dsn == 'from-plugin', (\n"
                "        'a plugin fixture carries no anchor, so B1 never "
                "filters it out of a subtree'\n"
                "    )\n"
            )
        },
    )

    out, _, rc = helpers.run_oxitest(tmp, cwd=".")

    integ.assert_passed(out, rc, count=1)


def test_plugin_fixture_resolves_under_parallel_workers(tmp: TempDir) -> None:
    """Workers rebuild their own session, so they must activate plugins too.

    Before #1717 nothing loaded plugins in a worker: this passed serially and
    errored under -n, and so did the shipped FixtureProvider path.
    """
    _write_plugin_project(
        tmp,
        tests={
            "test_one.py": (
                "from oxitest import Fixtures\n\n\n"
                "def test_first(fx: Fixtures) -> None:\n"
                "    assert fx.conn.dsn == 'from-plugin', 'worker one'\n"
            ),
            "test_two.py": (
                "from oxitest import Fixtures\n\n\n"
                "def test_second(fx: Fixtures) -> None:\n"
                "    assert fx.conn.dsn == 'from-plugin', 'worker two'\n"
            ),
        },
    )

    out, _, rc = helpers.run_oxitest(tmp, "-n", "2", cwd=".")

    integ.assert_passed(out, rc, count=2)


def test_declared_namespace_replaces_the_module_name(tmp: TempDir) -> None:
    """`namespace = "pg"` moves the segment; the module name stops working."""
    _write_plugin_project(
        tmp,
        settings="[tool.oxitest.plugin_settings.my_plugin]\nnamespace = 'pg'\n",
        tests={
            "test_ns.py": (
                "from oxitest import Fixtures\n\n\n"
                "def test_declared(fx: Fixtures) -> None:\n"
                "    assert fx.pg.conn.dsn == 'from-plugin', (\n"
                "        'the declared namespace is what the user typed in "
                "their own pyproject'\n"
                "    )\n"
            )
        },
    )

    out, _, rc = helpers.run_oxitest(tmp, cwd=".")

    integ.assert_passed(out, rc, count=1)


def test_plugin_fixture_appears_in_the_fixture_listing(tmp: TempDir) -> None:
    """A plugin fixture is discoverable through `query fixtures`.

    Attribution to the owning plugin renders only at verbosity 2 and is pinned
    at unit level instead — see ``test_fixtures_redesign_slice10.py``.
    """
    _write_plugin_project(
        tmp,
        tests={"test_l.py": "def test_noop() -> None:\n    pass\n"},
    )

    out, _, rc = helpers.run_oxitest_subcmd(tmp, "query", "fixtures", cwd=".")

    integ.assert_passed(out, rc)
    integ.assert_contains(out, "conn")


# ── lifetimes ─────────────────────────────────────────────────────────────────


def test_package_lifetime_is_refused_naming_the_plugin(tmp: TempDir) -> None:
    """A plugin has no anchor directory, so package lifetime cannot bind."""
    _write_plugin_project(
        tmp,
        fixtures=(
            "import oxitest as oxi\n\n\n"
            "@oxi.fixture(lifetime='package')\n"
            "def conn() -> int:\n"
            "    return 1\n"
        ),
        tests={"test_p.py": "def test_noop() -> None:\n    pass\n"},
    )

    out, _, rc = helpers.run_oxitest(tmp, cwd=".")

    assert rc != 0, f"a package-lifetime plugin fixture must refuse the run\n{out}"
    integ.assert_contains(out, "my_plugin", "package")
    integ.assert_excludes(out, "please report")


def test_process_lifetime_is_accepted_off_tree(tmp: TempDir) -> None:
    """ADR-0009 Rule 4's rootdir restriction does not reach a plugin package."""
    _write_plugin_project(
        tmp,
        fixtures=(
            "import oxitest as oxi\n\n\n"
            "@oxi.fixture(lifetime='process')\n"
            "def pool() -> int:\n"
            "    return 7\n"
        ),
        tests={
            "test_proc.py": (
                "from oxitest import Fixtures\n\n\n"
                "def test_process_tier(fx: Fixtures) -> None:\n"
                "    assert fx.pool == 7, (\n"
                "        'process binds to the process, so a plugin declaring "
                "it needs no rootdir package'\n"
                "    )\n"
            )
        },
    )

    out, _, rc = helpers.run_oxitest(tmp, cwd=".")

    integ.assert_passed(out, rc, count=1)


# ── autouse ───────────────────────────────────────────────────────────────────

# The marker path is derived from the fixture module's own location, not from
# the process cwd: oxitest runs from the caller's directory, so a relative
# path lands outside the project and the assertion reads as "did not fire"
# when it did.
_AUTOUSE_FIXTURE = """import pathlib

import oxitest as oxi


@oxi.fixture(lifetime="module", autouse=True)
def tx() -> int:
    (pathlib.Path(__file__).parent.parent / "fired.txt").write_text("yes")
    return 1
"""


def test_autouse_not_enabled_does_not_fire(tmp: TempDir) -> None:
    """Installing a plugin is not consent to add setup to every test."""
    _write_plugin_project(
        tmp,
        fixtures=_AUTOUSE_FIXTURE,
        tests={"test_inert.py": "def test_noop() -> None:\n    pass\n"},
    )

    out, _, rc = helpers.run_oxitest(tmp, "--warnings", cwd=".")

    integ.assert_passed(out, rc, count=1)
    assert not (tmp / "fired.txt").exists(), (
        "the fixture must not run until the user names it in autouse = [...]; "
        "pip install is not consent to mutate every test in a suite"
    )
    integ.assert_contains(out, "declares autouse=True but is not enabled")


def test_autouse_enabled_fires_for_every_test(tmp: TempDir) -> None:
    """Naming the fixture in the user's own pyproject turns it on."""
    _write_plugin_project(
        tmp,
        fixtures=_AUTOUSE_FIXTURE,
        settings="[tool.oxitest.plugin_settings.my_plugin]\nautouse = ['tx']\n",
        tests={"test_fires.py": "def test_noop() -> None:\n    pass\n"},
    )

    out, _, rc = helpers.run_oxitest(tmp, cwd=".")

    integ.assert_passed(out, rc, count=1)
    assert (tmp / "fired.txt").exists(), (
        "the gate is an enable, not a disable — a fixture the user explicitly "
        "listed has to actually fire"
    )


def test_enabled_async_autouse_function_fixture_is_refused(tmp: TempDir) -> None:
    """#1716's guard, ported: a plugin cannot do run-wide what a user cannot."""
    _write_plugin_project(
        tmp,
        fixtures=(
            "import oxitest as oxi\n\n\n"
            "@oxi.fixture(lifetime='function', autouse=True)\n"
            "async def probe() -> int:\n"
            "    return 1\n"
        ),
        settings="[tool.oxitest.plugin_settings.my_plugin]\nautouse = ['probe']\n",
        tests={"test_async.py": "def test_noop() -> None:\n    pass\n"},
    )

    out, _, rc = helpers.run_oxitest(tmp, cwd=".")

    assert rc != 0, (
        "an autouse function-lifetime async fixture manufactures the ADR-0006 "
        f"illegal cell for every sync test in its boundary\n{out}"
    )
    integ.assert_contains(out, "probe")


# ── collisions ────────────────────────────────────────────────────────────────


def test_user_declaration_shadows_the_plugin_fixture(tmp: TempDir) -> None:
    """A colliding user declaration wins, with a notice — and the run stays green.

    The exit code is the assertion that matters. `pip install` must never be
    able to turn a green suite red: without the _clashing_declaration carve-out
    this aborts with "declared twice ... delete one declaration", naming a file
    inside a package the user cannot edit.
    """
    _write_plugin_project(
        tmp,
        settings="[tool.oxitest.plugin_settings.my_plugin]\nnamespace = 'tests'\n",
        tests={
            "__fixtures__.py": (
                "import oxitest as oxi\n\n\n"
                "@oxi.fixture(lifetime='module')\n"
                "def conn() -> str:\n"
                "    return 'from-user'\n"
            ),
            "test_shadow.py": (
                "from oxitest import Fixtures\n\n\n"
                "def test_user_wins(fx: Fixtures) -> None:\n"
                "    assert fx.conn == 'from-user', (\n"
                "        'a local declaration outranks a plugin fixture, the "
                "same way it outranks a conftest one'\n"
                "    )\n"
            ),
        },
    )

    out, _, rc = helpers.run_oxitest(tmp, "--warnings", cwd=".")

    integ.assert_passed(out, rc, count=1)
    integ.assert_contains(out, "<plugin:my_plugin>")


def test_reserved_oxi_namespace_is_refused(tmp: TempDir) -> None:
    """A plugin claiming `oxi` would be unreachable, so it is refused loudly."""
    _write_plugin_project(
        tmp,
        settings="[tool.oxitest.plugin_settings.my_plugin]\nnamespace = 'oxi'\n",
        tests={"test_res.py": "def test_noop() -> None:\n    pass\n"},
    )

    out, _, rc = helpers.run_oxitest(tmp, cwd=".")

    assert rc != 0, (
        "fx.oxi resolves to the built-in namespace before any plugin is "
        f"consulted, so these fixtures would be silently unreachable\n{out}"
    )
    integ.assert_contains(out, "my_plugin", "oxi")


def test_plugin_inside_testpaths_registers_once(tmp: TempDir) -> None:
    """A vendored plugin is registered as a plugin, not also as a user package.

    `testpaths = ["."]` walks the plugin package itself. Its __fixtures__.py
    would then be registered twice — once ambient, once anchored — under the
    same derived namespace, giving one fixture two scope buckets.
    """
    _write_plugin_project(
        tmp,
        testpaths='["."]',
        fixtures=(
            "import oxitest as oxi\n\n\n"
            "@oxi.fixture(lifetime='module')\n"
            "def counter() -> list[int]:\n"
            "    return [1]\n"
        ),
        tests={
            "test_once.py": (
                "from oxitest import Fixtures\n\n\n"
                "def test_single_registration(fx: Fixtures) -> None:\n"
                "    assert fx.counter == [1], (\n"
                "        'a second registration would give the same fixture "
                "two scope buckets'\n"
                "    )\n"
            )
        },
    )

    out, _, rc = helpers.run_oxitest(tmp, cwd=".")

    integ.assert_passed(out, rc, count=1)
