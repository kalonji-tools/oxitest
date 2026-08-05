"""Fixture redesign slice 10 — static plugin fixtures (#1717).

Unit-level coverage for resolving a plugin's ``__fixtures__.py`` home and for
the registrar that turns its declarations into ``PluginModuleSource`` defs.
End-to-end behaviour lives in ``tests/integration/test_plugin_fixtures.py``.
"""

from __future__ import annotations

from types import ModuleType

from oxitest import Fixture, TempDir, raises
from oxitest._bridge._errors import UsageError
from oxitest._bridge._fixture_registry import (
    FixtureDef,
    FixtureRegistry,
    FixtureScope,
    PluginModuleSource,
)
from oxitest._bridge._lifetime import Lifetime
from oxitest._bridge._module_source_registrar import (
    register_module_source_fixtures,
    register_plugin_source_fixtures,
)
from oxitest._bridge.fixture_lister import _origin_header, _origin_key
from oxitest._bridge.plugin_loader import plugin_fixture_homes
from oxitest._bridge.result import Diagnostic

# ── plugin_fixture_homes: resolution ──────────────────────────────────────────


def _package_module(
    tmp: TempDir, name: str, *, with_fixtures: bool = True
) -> ModuleType:
    """A module object shaped like an imported plugin package on disk."""
    pkg = tmp / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    if with_fixtures:
        (pkg / "__fixtures__.py").write_text("")
    module = ModuleType(name)
    module.__file__ = str(pkg / "__init__.py")
    module.__path__ = [str(pkg)]
    return module


def test_namespace_defaults_to_the_module_name(tmp: TempDir) -> None:
    """A plugin that declares no namespace is addressed by its module name."""
    module = _package_module(tmp, "oxi_pg")

    homes = plugin_fixture_homes(
        activated_modules=("oxi_pg",),
        plugin_settings={},
        modules={"oxi_pg": module},
    )

    assert len(homes) == 1, (
        f"a package with a __fixtures__.py is a fixture home; got {homes!r}"
    )
    assert homes[0].namespace == "oxi_pg", (
        "defaulting to the module name is what lets a plugin ship fixtures "
        "without the user configuring anything — inventing a second identity "
        f"would force every plugin to declare one; got {homes[0].namespace!r}"
    )


def test_declared_namespace_overrides_the_module_name(tmp: TempDir) -> None:
    """`namespace = "..."` shortens the fx. segment users type."""
    module = _package_module(tmp, "oxitest_postgres")

    homes = plugin_fixture_homes(
        activated_modules=("oxitest_postgres",),
        plugin_settings={"oxitest_postgres": {"namespace": "postgres"}},
        modules={"oxitest_postgres": module},
    )

    assert homes[0].namespace == "postgres", (
        "without the override users type the distribution's module name at "
        f"every call site; got {homes[0].namespace!r}"
    )


def test_declared_autouse_names_are_carried(tmp: TempDir) -> None:
    """The user's autouse list reaches the registrar that gates on it."""
    module = _package_module(tmp, "oxi_pg")

    homes = plugin_fixture_homes(
        activated_modules=("oxi_pg",),
        plugin_settings={"oxi_pg": {"autouse": ["tx"]}},
        modules={"oxi_pg": module},
    )

    assert homes[0].autouse == ("tx",), (
        "this list is the only thing that turns a plugin's autouse fixture on; "
        f"dropping it silently disables a feature the user asked for; got "
        f"{homes[0].autouse!r}"
    )


def test_package_without_a_fixtures_file_is_not_a_home(tmp: TempDir) -> None:
    """A plugin that ships no fixtures contributes no home."""
    module = _package_module(tmp, "plain_plugin", with_fixtures=False)

    homes = plugin_fixture_homes(
        activated_modules=("plain_plugin",),
        plugin_settings={},
        modules={"plain_plugin": module},
    )

    assert homes == (), (
        "most plugins ship no fixtures at all; returning a home for them would "
        f"make the bridge prescan a file that does not exist; got {homes!r}"
    )


def test_single_module_plugin_declaring_namespace_emits_a_notice(
    tmp: TempDir,
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """Fixture keys on a non-package plugin do nothing, and say so."""
    single = ModuleType("flat_plugin")
    single.__file__ = str(tmp / "flat_plugin.py")

    homes = plugin_fixture_homes(
        activated_modules=("flat_plugin",),
        plugin_settings={"flat_plugin": {"namespace": "flat"}},
        modules={"flat_plugin": single},
    )

    assert homes == (), "a single module has no directory to hold __fixtures__.py"
    notices = [d for d in diag_collector if d.context == "plugin fixtures"]
    assert len(notices) == 1, (
        "silently ignoring the keys leaves the user believing their fixtures "
        f"are active when nothing was ever scanned; got {notices!r}"
    )


# ── plugin_fixture_homes: namespace validation ────────────────────────────────


def test_reserved_oxi_namespace_is_refused(tmp: TempDir) -> None:
    """A plugin claiming `oxi` is refused rather than made unreachable."""
    module = _package_module(tmp, "my_plugin")

    with raises(UsageError) as exc:
        plugin_fixture_homes(
            activated_modules=("my_plugin",),
            plugin_settings={"my_plugin": {"namespace": "oxi"}},
            modules={"my_plugin": module},
        )

    msg = str(exc.value)
    assert "oxi" in msg and "my_plugin" in msg, (
        "fx.oxi is intercepted before the namespace lookup, so a plugin "
        "claiming it would be silently unreachable — the message has to name "
        f"both the reserved word and the plugin that claimed it; got {msg!r}"
    )


def test_keyword_namespace_is_refused(tmp: TempDir) -> None:
    """A namespace that is a Python keyword cannot be an attribute segment."""
    module = _package_module(tmp, "my_plugin")

    with raises(ValueError) as exc:
        plugin_fixture_homes(
            activated_modules=("my_plugin",),
            plugin_settings={"my_plugin": {"namespace": "class"}},
            modules={"my_plugin": module},
        )

    assert "class" in str(exc.value), (
        "fx.class is a syntax error, so the fixtures would be unreachable by "
        f"the documented access route; got {str(exc.value)!r}"
    )


def test_two_plugins_claiming_one_namespace_are_refused(tmp: TempDir) -> None:
    """Two distributions cannot silently merge under one fx. segment."""
    first = _package_module(tmp, "plugin_a")
    second = _package_module(tmp, "plugin_b")

    with raises(UsageError) as exc:
        plugin_fixture_homes(
            activated_modules=("plugin_a", "plugin_b"),
            plugin_settings={
                "plugin_a": {"namespace": "db"},
                "plugin_b": {"namespace": "db"},
            },
            modules={"plugin_a": first, "plugin_b": second},
        )

    msg = str(exc.value)
    assert "plugin_a" in msg and "plugin_b" in msg, (
        "merging two plugins into one namespace makes fx.db.x resolve to "
        "whichever registered last — the message must name both so the user "
        f"knows which to re-namespace; got {msg!r}"
    )


# ── register_plugin_source_fixtures ───────────────────────────────────────────


def _make_plugin_def() -> FixtureDef[int]:
    """A FixtureDef as the plugin registrar builds it, for listing assertions."""
    return FixtureDef(
        name="conn",
        fixture_type=int,
        scope=FixtureScope.MODULE,
        source=PluginModuleSource(
            func=_plugin_conn,
            defining_module_path="/site-packages/my_plugin/__fixtures__.py",
            plugin_module="my_plugin",
            lifetime=Lifetime.MODULE,
        ),
        namespace="my_plugin",
    )


def _plugin_conn() -> int:
    """Stand-in plugin fixture factory."""
    return 1


def _plugin_fixture_module(source: str) -> ModuleType:
    """A ModuleType carrying decorated declarations, as the registrar sees one."""
    module = ModuleType("_oxitest_test_plugin_fixtures")
    module.__file__ = "/site-packages/my_plugin/__fixtures__.py"
    exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102
    return module


def _register(
    module: ModuleType,
    *,
    autouse_names: tuple[str, ...] = (),
) -> FixtureRegistry:
    """Register *module* as plugin `my_plugin` and return the registry."""
    registry = FixtureRegistry()
    register_plugin_source_fixtures(
        registry,
        module,
        plugin_module="my_plugin",
        namespace="my_plugin",
        autouse_names=autouse_names,
    )
    return registry


def test_plugin_package_lifetime_is_refused() -> None:
    """A plugin cannot declare lifetime="package" — it has no anchor directory."""
    module = _plugin_fixture_module(
        "import oxitest as oxi\n\n"
        "@oxi.fixture(lifetime='package')\n"
        "def conn() -> int:\n"
        "    return 1\n"
    )

    with raises(UsageError) as exc:
        _register(module)

    msg = str(exc.value)
    assert "package" in msg and "my_plugin" in msg, (
        "without this refusal the declaration reaches _anchor_of at resolution "
        "time, where a non-ModuleSource with package scope is treated as a "
        f"framework bug; got {msg!r}"
    )
    assert "report" not in msg, (
        "the framework-bug message tells the reader to file an oxitest issue; "
        f"a plugin author's own typo must not be routed there; got {msg!r}"
    )


def test_plugin_module_lifetime_is_accepted() -> None:
    """The tiers that need no anchor still work."""
    module = _plugin_fixture_module(
        "import oxitest as oxi\n\n"
        "@oxi.fixture(lifetime='module')\n"
        "def conn() -> int:\n"
        "    return 1\n"
    )

    registry = _register(module)

    defn = registry.get("conn")
    assert defn is not None, (
        "module lifetime keys on the consuming test module, which a plugin "
        "fixture has just as much as a user fixture does"
    )
    assert defn.namespace == "my_plugin", (
        f"the def must carry its namespace or fx.my_plugin.conn cannot resolve; "
        f"got {defn.namespace!r}"
    )


def test_plugin_process_lifetime_is_accepted() -> None:
    """The process tier is legal for a plugin, though it sits off the test tree."""
    module = _plugin_fixture_module(
        "import oxitest as oxi\n\n"
        "@oxi.fixture(lifetime='process')\n"
        "def pool() -> int:\n"
        "    return 1\n"
    )

    registry = _register(module)

    assert registry.get("pool") is not None, (
        "ADR-0009 Rule 4 restricts process to a rootdir package because a "
        "process fixture anchored below the root binds to no boundary — a "
        "plugin's binds to the process regardless, so the restriction does "
        "not apply and one-connection-pool-per-worker must stay expressible"
    )


def test_plugin_autouse_is_inert_until_the_user_enables_it() -> None:
    """autouse=True in a plugin fires nothing unless named in pyproject."""
    module = _plugin_fixture_module(
        "import oxitest as oxi\n\n"
        "@oxi.fixture(lifetime='module', autouse=True)\n"
        "def tx() -> int:\n"
        "    return 1\n"
    )

    registry = _register(module)

    defn = registry.get("tx")
    assert defn is not None, "the fixture registers either way — only autouse is gated"
    assert defn.autouse is False, (
        "installing a plugin must not silently add setup to every test in the "
        "suite; the consent lives in the user's pyproject, not the plugin's "
        "source"
    )


def test_plugin_autouse_fires_once_the_user_enables_it() -> None:
    """Naming the fixture in `autouse = [...]` turns it on."""
    module = _plugin_fixture_module(
        "import oxitest as oxi\n\n"
        "@oxi.fixture(lifetime='module', autouse=True)\n"
        "def tx() -> int:\n"
        "    return 1\n"
    )

    registry = _register(module, autouse_names=("tx",))

    defn = registry.get("tx")
    assert defn is not None and defn.autouse is True, (
        "the gate is an enable, not a disable — a plugin fixture the user "
        "explicitly asked for has to actually fire"
    )


def test_declared_but_disabled_autouse_emits_a_notice(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """The capability stays discoverable rather than silently off."""
    module = _plugin_fixture_module(
        "import oxitest as oxi\n\n"
        "@oxi.fixture(lifetime='module', autouse=True)\n"
        "def tx() -> int:\n"
        "    return 1\n"
    )

    _register(module)

    notices = [d for d in diag_collector if d.context == "plugin fixtures"]
    assert len(notices) == 1, (
        "without the notice a plugin author's autouse fixture is off with no "
        f"signal anywhere that it exists or how to enable it; got {notices!r}"
    )
    assert "autouse" in notices[0].message and "tx" in notices[0].message, (
        f"the notice must name the fixture and the key that enables it; got "
        f"{notices[0].message!r}"
    )


def test_enabled_async_autouse_function_fixture_is_refused() -> None:
    """#1716's guard, ported: a plugin cannot do run-wide what a user cannot do."""
    module = _plugin_fixture_module(
        "import oxitest as oxi\n\n"
        "@oxi.fixture(lifetime='function', autouse=True)\n"
        "async def probe() -> int:\n"
        "    return 1\n"
    )

    with raises(UsageError) as exc:
        _register(module, autouse_names=("probe",))

    assert "probe" in str(exc.value), (
        "an autouse function-lifetime async fixture fires for the sync tests "
        "in its boundary too, manufacturing the ADR-0006 illegal cell for "
        f"tests that never asked for it; got {str(exc.value)!r}"
    )


def test_disabled_async_autouse_function_fixture_is_allowed() -> None:
    """The async guard follows the gate: a fixture that cannot fire cannot harm."""
    module = _plugin_fixture_module(
        "import oxitest as oxi\n\n"
        "@oxi.fixture(lifetime='function', autouse=True)\n"
        "async def probe() -> int:\n"
        "    return 1\n"
    )

    registry = _register(module)

    assert registry.get("probe") is not None, (
        "refusing here would reject a plugin the user has not enabled anything "
        "from — the illegal cell only exists once the fixture actually fires"
    )


def test_a_user_declaration_colliding_with_a_plugin_does_not_raise(
    tmp: TempDir,
) -> None:
    """A plugin fixture never turns a user's own declaration into an error."""
    registry = FixtureRegistry()
    plugin_module = _plugin_fixture_module(
        "import oxitest as oxi\n\n"
        "@oxi.fixture(lifetime='module')\n"
        "def conn() -> int:\n"
        "    return 1\n"
    )
    register_plugin_source_fixtures(
        registry,
        plugin_module,
        plugin_module="my_plugin",
        namespace="api",
        autouse_names=(),
    )
    anchor = tmp / "api"
    anchor.mkdir()
    user_home = anchor / "__fixtures__.py"
    user_home.write_text(
        "import oxitest as oxi\n\n"
        "@oxi.fixture(lifetime='module')\n"
        "def conn() -> int:\n"
        "    return 2\n"
    )
    user_module = ModuleType("_oxitest_user_fixtures")
    user_module.__file__ = str(user_home)
    exec(  # noqa: S102 - mirrors how the bridge loads a declaration home
        compile(user_home.read_text(), str(user_home), "exec"),
        user_module.__dict__,
    )

    register_module_source_fixtures(
        registry, user_module, anchor_package_path=str(anchor)
    )

    defs = registry.defs_in_namespace("conn", "api")
    assert len(defs) == 2, (
        "both declarations must coexist so the shadow order can pick a winner; "
        f"raising instead would tell the user to delete a declaration inside an "
        f"installed package they cannot edit, got {defs!r}"
    )


def test_plugin_fixture_is_attributed_to_its_plugin_in_the_listing() -> None:
    """The fixture listing names the owning plugin, not a site-packages path.

    Verbosity 2 is the only place origins render, and driving that through the
    CLI proved brittle, so the two helpers behind it are pinned directly.
    """
    defn = _make_plugin_def()

    assert _origin_header(defn) == "plugin (my_plugin)", (
        "falling through to the raw defining path prints a long site-packages "
        "string that tells a user nothing about which installed package owns "
        f"the fixture; got {_origin_header(defn)!r}"
    )
    assert _origin_key(defn) == (1, "my_plugin"), (
        "the sort key groups a plugin's fixtures together and beside the "
        f"FixtureProvider plugins rather than under a path; got "
        f"{_origin_key(defn)!r}"
    )
