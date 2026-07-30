"""Module-source fixture registrar (ADR-0009 slices 1 and 6).

Scans a Python module for @oxi.fixture-decorated functions and writes
FixtureDef entries into the FixtureRegistry using the ModuleSource variant.

Duplicate detection has two rules, because a namespace is an anchor-directory
basename and basenames are not unique in a tree:

- against a conftest.py Fixtures() instance (the slice-1 Q3 rule) — always a
  collision. A ConftestSource has no anchor and resolves run-wide, so it is
  reachable from wherever the other declaration is.
- against another __fixtures__.py (#1713) — a collision only when one anchor's
  subtree contains the other's. ``tests/api/v1`` and ``tests/admin/v1`` both
  derive the namespace ``v1``, but no test can see both, so rejecting that pair
  would kill the run over an ambiguity that cannot arise.

Either way the UsageError names both source paths.
"""

from __future__ import annotations

__all__ = ["register_module_source_fixtures"]

import inspect
from pathlib import Path
from types import ModuleType
from typing import Any, get_type_hints

from oxitest._bridge._errors import UsageError
from oxitest._bridge._fixture_decorator import MARKER_ATTR, _FixtureMarker
from oxitest._bridge._fixture_registry import (
    LIFETIME_SCOPES,
    FixtureDef,
    FixtureRegistry,
    ModuleSource,
)
from oxitest._bridge._visibility import anchors_overlap


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

    Collision scope: only (namespace, name) collisions between ConftestSource
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
    module_path = fixture_module.__file__ or ""

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

        existing = _clashing_declaration(
            registry.defs_in_namespace(attr_name, namespace), anchor_package_path
        )
        if existing is not None:
            msg = (
                f"fixture '{namespace}.{attr_name}' declared twice in "
                f"overlapping packages:\n"
                f"  {existing.conftest_path}\n"
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
                namespace=namespace,
                is_async=_is_async(obj),
            )
        )


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
        anchor = defn.anchor
        if anchor is None or anchors_overlap(anchor, anchor_package_path):
            return defn
    return None


def _is_async(obj: Any) -> bool:
    """Whether *obj* is a coroutine function or an async-generator function.

    Mirrors the ConftestSource path (``_fixtures.py``) exactly. Without this
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
