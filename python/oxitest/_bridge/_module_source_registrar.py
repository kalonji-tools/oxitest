"""Module-source fixture registrar (ADR-0009 slices 1 and 6).

Scans a Python module for @oxi.fixture-decorated functions and writes
FixtureDef entries into the FixtureRegistry using the ModuleSource variant.

Duplicate detection has two rules, because a namespace is an anchor-directory
basename and basenames are not unique in a tree:

- against a conftest.py Fixtures() instance (the slice-1 Q3 rule) — always a
  collision. A FrameworkSource has no anchor and resolves run-wide, so it is
  reachable from wherever the other declaration is.
- against another __fixtures__.py (#1713) — a collision only when one anchor's
  subtree contains the other's. ``tests/api/v1`` and ``tests/admin/v1`` both
  derive the namespace ``v1``, but no test can see both, so rejecting that pair
  would kill the run over an ambiguity that cannot arise.

Either way the UsageError names both source paths.
"""

from __future__ import annotations

__all__ = ["register_module_source_fixtures", "register_plugin_source_fixtures"]

import inspect
from pathlib import Path
from types import ModuleType
from typing import Any, get_type_hints

from oxitest._bridge._boundary import safe_type_hints
from oxitest._bridge._diagnostic_collector import emit_diagnostic
from oxitest._bridge._errors import UsageError
from oxitest._bridge._fixture_decorator import MARKER_ATTR, _FixtureMarker
from oxitest._bridge._fixture_registry import (
    LIFETIME_SCOPES,
    FixtureDef,
    FixtureRegistry,
    ModuleSource,
    PluginModuleSource,
    _fixture_inner_type,
)
from oxitest._bridge._lifetime import Lifetime
from oxitest._bridge._visibility import anchors_overlap
from oxitest._bridge.result import DiagnosticSeverity


def register_module_source_fixtures(
    registry: FixtureRegistry,
    fixture_module: ModuleType,
    *,
    anchor_package_path: str,
) -> None:
    """Scan a module for @oxi.fixture-decorated functions and register them.

    Namespace = anchor-package segment name (ADR-0009 Rule 5).
    Collision with an existing FixtureDef in the same (namespace, name)
    raises UsageError naming both source paths.

    Collision scope: only (namespace, name) collisions between FrameworkSource
    and ModuleSource are detected. Cross-namespace name shadowing (e.g., a
    PluginSource in a different namespace with the same short name) is
    intentional per ADR-0009 Rule 5 (namespace derivation) — the namespace
    prefix makes the full qualified name unambiguous.
    """
    # An inline declaration's anchor is its own module (ADR-0009 Rule 1), so the
    # anchor is a file rather than a directory and its suffix has to come off.
    #
    # Not a blanket `.stem`: Path("tests/api.v1").stem is "api" while .name is
    # "api.v1", so that would silently re-namespace package-level fixtures in any
    # directory containing a dot.
    anchor = Path(anchor_package_path)
    namespace = anchor.stem if anchor.suffix == ".py" else anchor.name
    module_path = _canonical_module_path(fixture_module.__file__)

    # The same test that derives the namespace also decides the home kind: an
    # inline declaration anchors to its own module, so a `.py` suffix *is* the
    # inline case. ADR-0009 Rule 2's home-kind cap is enforced here rather than
    # from the prescan AST because registration is marker-attribute based — any
    # import spelling declares a real fixture, and a static scan sees only three
    # of them (#1859).
    is_inline = anchor.suffix == ".py"
    violations: list[str] = []

    for attr_name, obj in vars(fixture_module).items():
        # isinstance, not a truthiness or None check. `getattr` is a probe, and
        # any object defining __getattr__ answers every name: `_Mark`
        # (`_mark_api.py`) returns a fresh `_Mark` for `__oxitest_fixture__`, so a
        # `None` guard lets it through and `marker.lifetime` raises AttributeError.
        #
        # Harmless while this only ran on __fixtures__.py / __init__.py, which hold
        # no module-level mark objects. #1712 began calling it on every test
        # module, where `oxi.mark.skip` at module level is ordinary — and that took
        # main red (#1757). Narrowing to the type @oxi.fixture actually writes is
        # the contract, and protects against the next __getattr__-happy object too.
        marker = getattr(obj, MARKER_ATTR, None)
        if not isinstance(marker, _FixtureMarker):
            continue

        # PACKAGE and PROCESS are the complete set above the cap: `Lifetime` has
        # exactly four members since #1777 renamed SESSION to PROCESS.
        if is_inline and marker.lifetime in (Lifetime.PACKAGE, Lifetime.PROCESS):
            # Accumulated, not raised here. Someone whose aliased declarations
            # were silently ignored until now likely has several, and a
            # run-fix cycle each is a poor trade for failing one line earlier.
            violations.append(
                _inline_cap_message(attr_name, module_path, marker.lifetime)
            )
            continue

        # An autouse function-lifetime async fixture fires for every test in
        # its B1 boundary, sync ones included, so it manufactures the ADR-0006
        # illegal cell for tests that never asked for it. One error at the
        # declaration beats one on every sync test in scope (#1716).
        #
        # Enforced here rather than in the prescan AST for the same reason the
        # cap above is: registration is marker-attribute based and sees every
        # import spelling, while a static scan sees three (#1859). The wider
        # tiers stay legal — the survey on #1739 found no framework restricting
        # autouse for being async, and a per-module transaction is the
        # canonical use.
        if marker.autouse and marker.lifetime is Lifetime.FUNCTION and _is_async(obj):
            violations.append(_async_autouse_message(attr_name, module_path))
            continue

        existing = _clashing_declaration(
            registry.defs_in_namespace(attr_name, namespace), anchor_package_path
        )
        if existing is not None:
            msg = (
                f"fixture '{namespace}.{attr_name}' declared twice in "
                f"overlapping packages:\n"
                f"  {existing.declaration_path}\n"
                f"  {module_path}\n"
                f"→ delete one declaration (ADR-0009 slice-1 coexistence)"
            )
            raise UsageError(msg)

        registry.register(
            FixtureDef(
                name=attr_name,
                fixture_type=_infer_return_type(obj),
                scope=LIFETIME_SCOPES[marker.lifetime],
                source=ModuleSource(
                    func=obj,
                    defining_module_path=module_path,
                    anchor_package_path=anchor_package_path,
                    lifetime=marker.lifetime,
                ),
                autouse=marker.autouse,
                namespace=namespace,
                is_async=_is_async(obj),
                depends_on=_extract_depends_on(obj),
            )
        )

    if violations:
        raise UsageError("\n\n".join(violations))


def register_plugin_source_fixtures(  # noqa: PLR0913 — five are the declaration's identity, the sixth is the caller's role
    registry: FixtureRegistry,
    fixture_module: ModuleType,
    *,
    plugin_module: str,
    namespace: str,
    autouse_names: tuple[str, ...],
    emit_notices: bool = True,
) -> None:
    """Register an activated plugin's ``__fixtures__.py`` declarations (#1717).

    Ambient and B1-exempt, and deliberately **without** the clash check the
    user path runs: a plugin fixture must never hard-fail a suite. A colliding
    user declaration shadows this one through the registry's ordering rule,
    which is what keeps ``pip install`` from turning a green suite red.

    Args:
        registry: The live registry for this run.
        fixture_module: The imported ``__fixtures__.py``.
        plugin_module: The plugin's module path, for diagnostics.
        namespace: The plugin's namespace — its module name unless overridden.
        autouse_names: Fixtures the **user** enabled for autouse. A plugin
            declaring ``autouse=True`` fires nothing until named here.
        emit_notices: Whether to emit user-facing notices. Workers pass
            ``False`` — the coordinator emits them once, before any worker
            spawns, and repeating them multiplies each by the worker count.

    Raises:
        UsageError: one or more declarations are illegal for a plugin. All of
            them are reported together.
    """
    module_path = _canonical_module_path(fixture_module.__file__)
    violations: list[str] = []

    for attr_name, obj in vars(fixture_module).items():
        # isinstance, not truthiness — see register_module_source_fixtures for
        # why a __getattr__-happy object breaks a None guard here.
        marker = getattr(obj, MARKER_ATTR, None)
        if not isinstance(marker, _FixtureMarker):
            continue

        # Package lifetime keys on an anchor *directory in the user's test
        # tree*. A plugin has none, and without this refusal the declaration
        # reaches _anchor_of at resolution time, which reports it as an
        # oxitest bug rather than as the plugin author's typo.
        #
        # Accumulated rather than raised, like the inline cap above: a plugin
        # with three bad declarations should report three, not cost three
        # run-fix cycles.
        if marker.lifetime is Lifetime.PACKAGE:
            violations.append(_plugin_package_cap_message(attr_name, plugin_module))
            continue

        # Autouse is the user's call. The plugin declares the capability; the
        # user's pyproject decides whether it applies to their suite.
        enabled = marker.autouse and attr_name in autouse_names
        if marker.autouse and not enabled and emit_notices:
            emit_diagnostic(
                DiagnosticSeverity.NOTICE,
                "plugin fixtures",
                f"'{namespace}.{attr_name}' declares autouse=True but is not "
                f'enabled. Add it to autouse = ["{attr_name}"] under '
                f"[tool.oxitest.plugin_settings.{plugin_module}] to apply it "
                f"to every test.",
            )

        # #1716's guard, ported: this path does not go through the registrar
        # that owns it, and without it a plugin could do run-wide what a user
        # is refused locally. Gated on `enabled` because a fixture that cannot
        # fire cannot manufacture the illegal cell.
        if enabled and marker.lifetime is Lifetime.FUNCTION and _is_async(obj):
            violations.append(_async_autouse_message(attr_name, plugin_module))
            continue

        registry.register(
            FixtureDef(
                name=attr_name,
                fixture_type=_infer_return_type(obj),
                scope=LIFETIME_SCOPES[marker.lifetime],
                source=PluginModuleSource(
                    func=obj,
                    defining_module_path=module_path,
                    plugin_module=plugin_module,
                    lifetime=marker.lifetime,
                ),
                autouse=enabled,
                namespace=namespace,
                is_async=_is_async(obj),
                depends_on=_extract_depends_on(obj),
            )
        )

    if violations:
        raise UsageError("\n\n".join(violations))


def _plugin_package_cap_message(fn_name: str, plugin_module: str) -> str:
    """Why a plugin fixture cannot hold package lifetime, and what to use instead.

    Naming the alternatives is load-bearing for the same reason as
    :func:`_inline_cap_message`: the destination cannot be derived from the
    refusal alone.
    """
    return (
        f'{fn_name} in plugin "{plugin_module}" declares lifetime="package", '
        f"but package lifetime binds a fixture to a directory in your test "
        f"tree, and a plugin has none.\n"
        f'Hint: use lifetime="process" for one instance per worker, or '
        f'lifetime="module" for one per test module.'
    )


def _inline_cap_message(fn_name: str, module_path: str, lifetime: Lifetime) -> str:
    """Why an inline declaration cannot hold *lifetime*, and where to move it.

    Naming the sibling ``__fixtures__.py`` is load-bearing, not decoration: a
    hint that only says "move it elsewhere" is unusable because the user cannot
    derive the destination (#1711's review).
    """
    home = Path(module_path).parent / "__fixtures__.py"
    return (
        f'{fn_name} in {module_path} declares lifetime="{lifetime}", but a '
        f"fixture declared inline in a test file is capped at "
        f'lifetime="module".\n'
        f"An inline fixture is anchored to its own module, so a lifetime wider "
        f"than the module would outlive the only scope that can see it.\n"
        f'Hint: drop to lifetime="module", or move the declaration to {home} '
        f'to keep lifetime="{lifetime}".'
    )


def _async_autouse_message(fn_name: str, module_path: str) -> str:
    """Why an async fixture cannot be function-lifetime autouse, and what to do.

    Two exits rather than one, because they are genuinely different fixes: which
    is right depends on whether the fixture is wanted on *some* tests or on
    *every* test in the boundary. Offering only the widen path would push users
    into a lifetime they did not want to get a fixture they did.
    """
    return (
        f"{fn_name} in {module_path} is an async fixture declared "
        f'autouse=True with lifetime="function".\n'
        f"An autouse function-lifetime fixture fires for every test in its "
        f"boundary, and the sync tests among them cannot await it.\n"
        f'Hint: drop autouse=True and use @oxi.arrange("{fn_name}") on the '
        f'tests that need it, or widen to lifetime="module" or wider, which '
        f"applies to sync and async tests alike."
    )


def _canonical_module_path(module_file: str | None) -> str:
    """*module_file* in the canonical form the collector uses for anchors.

    The two halves of a ``ModuleSource`` come from different layers.
    ``anchor_package_path`` is handed over by ``collector.rs``, which runs every
    collected path through ``std::fs::canonicalize``. ``__file__`` is whatever
    the import machinery recorded — CPython only ``abspath``-es a spec origin,
    so ``.``, ``..`` and symlinks survive it.

    ``is_visible`` tells an inline declaration from a package one by comparing
    those two for equality (ADR-0009 Rules 1 and 3), and it may not reach for
    the filesystem: it runs on every fixture resolution and is deliberately
    pure path arithmetic. Reconciling the provenances therefore happens here,
    once per fixture module.

    An empty ``__file__`` stays empty. ``Path("").resolve()`` is the working
    directory, which would silently anchor a synthetic module to the project
    root instead of leaving it obviously path-less.
    """
    if not module_file:
        return ""
    return str(Path(module_file).resolve())


def _clashing_declaration(
    existing: tuple[FixtureDef[Any], ...], anchor_package_path: str
) -> FixtureDef[Any] | None:
    """The declaration *anchor_package_path* really clashes with, if any.

    Sharing a ``(namespace, name)`` pair is not enough. A namespace is a
    directory basename, so ``tests/api/v1`` and ``tests/admin/v1`` both derive
    ``v1`` — and no test can see both, so nothing is ambiguous. The clash is
    real only when one anchor's subtree contains the other's, because then some
    test does see two fixtures under one qualified name.

    An unanchored declaration — a conftest ``Fixtures()`` instance — is run-wide
    and therefore clashes with everything. That is the conftest-versus-
    ``__fixtures__.py`` case this check was written for (slice-1 Q3), and it
    keeps working unchanged.
    """
    for defn in existing:
        # A plugin fixture is unanchored, so the `anchor is None` arm below
        # would treat it as clashing with everything — and plugin defs are
        # registered before collection, so they are always the `existing`
        # side. The user would be told to delete one of two declarations, one
        # of which lives inside an installed package they cannot edit.
        #
        # Shadowing handles this instead: the anchored declaration wins and
        # register() emits the notice. `pip install` must not be able to turn
        # a green suite red (#1717).
        if isinstance(defn.source, PluginModuleSource):
            continue
        anchor = defn.anchor
        if anchor is None or anchors_overlap(anchor, anchor_package_path):
            return defn
    return None


def _extract_depends_on(func: Any) -> tuple[tuple[str, type], ...]:
    """Extract dependency declarations from a fixture function.

    Returns ``(qualifier, binding_type)`` pairs for parameters annotated with
    ``Fixture[T]``. Unannotated parameters are NOT included — they are caught at
    resolve time by ``UnannotatedFixtureParamError``.

    Mirrors ``conftest_loader._extract_depends_on``. Without it a declaration's
    ``FixtureDef`` carries no dependencies at all, so the fixture dependency
    graph is empty for every ``@oxi.fixture``: ``query fixtures -E uses(x)``
    answers "no results" and ``--tree`` draws no edges, while the same project
    runs green (#1720). The duplication is deliberate and short-lived — the
    conftest copy goes with ``conftest_loader.py``, and this is the survivor.
    """
    hints = safe_type_hints(func, include_extras=True)
    if hints is None:
        return ()
    deps: list[tuple[str, type]] = []
    for param_name, hint in hints.items():
        if param_name == "return":
            continue
        is_fx, inner = _fixture_inner_type(hint)
        if is_fx:
            deps.append((param_name, inner))
    return tuple(deps)


def _is_async(obj: Any) -> bool:
    """Whether *obj* is a coroutine function or an async-generator function.

    Mirrors the FrameworkSource path (``_fixtures.py``) exactly. Without this
    the whole async surface is invisible to the resolver: an ``async def``
    fixture is treated as an ordinary callable and its coroutine is injected
    un-awaited (kalonji-tools/oxitest#1733).
    """
    return inspect.iscoroutinefunction(obj) or inspect.isasyncgenfunction(obj)


def _infer_return_type(obj: Any) -> type:
    """Best-effort return type inference for FixtureDef.fixture_type.

    Reads the return annotation via get_type_hints; falls back to object if
    unavailable. ADR-0002 primary key is type-based, but slice-1 acceptance
    only exercises namespace-based resolution — object is sufficient here.
    """
    try:
        hints = get_type_hints(obj)
    except Exception:  # noqa: BLE001 — get_type_hints may raise many things
        return object
    return hints.get("return", object)
