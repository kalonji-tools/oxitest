"""Constructors for fixture defs, registries, sessions, metadata and exceptions.

Split out of conftest.py by #1787. These are plain functions — there is no
helper registry (#1700). Import them via ``from tests import helpers``.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from typing import TypeVar

from oxitest._bridge._boundary import safe_type_hints
from oxitest._bridge._fixture_registry import (
    ConftestSource,
    FixtureDef,
    FixtureRegistry,
    FixtureScope,
)
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._module_source_registrar import register_module_source_fixtures
from oxitest._bridge._test_kind import from_wire
from oxitest._bridge._test_meta import TestMeta
from oxitest._bridge.plugin_loader import PluginRegistry


def make_fixture_def(  # noqa: PLR0913 — test helper, all kwargs have defaults
    name: str,
    factory: Callable[..., object] | None = None,
    *,
    namespace: str = "",
    shared: bool = False,
    autouse: bool = False,
    is_async: bool = False,
    conftest_path: str = "",
    doc: str = "",
    depends_on: tuple[tuple[str, type], ...] = (),
    fixture_type: type | None = None,
) -> FixtureDef:
    """Create a ``FixtureDef`` with sensible defaults.

    When *factory* is ``None`` a no-op ``lambda: None`` is used and
    its ``__name__`` / ``__doc__`` / ``__module__`` are set so that
    the fixture lister can group by origin.

    *depends_on* is a tuple of ``(qualifier, binding_type)`` pairs that
    explicitly declare fixture dependencies for type-based graph construction.

    *fixture_type* overrides the type extracted from the factory's return
    annotation. Useful when the factory is defined in a module with
    ``from __future__ import annotations`` (which turns annotations into
    strings that ``get_type_hints`` cannot resolve for locally-defined types).
    """
    if factory is None:

        def _fn() -> None:
            pass

        _fn.__name__ = name
        _fn.__doc__ = doc or None
        _fn.__module__ = "conftest" if conftest_path else "oxitest._bridge._builtins"
        factory = _fn
    # Explicit fixture_type overrides annotation extraction
    if fixture_type is not None:
        ft: type = fixture_type
    else:
        # Try to extract return type; fall back to object
        hints = safe_type_hints(factory)
        ft = hints.get("return", object) if hints is not None else object
    return FixtureDef(
        name=name,
        fixture_type=ft,
        scope=FixtureScope.SHARED if shared else FixtureScope.EACH,
        source=ConftestSource(func=factory, conftest_path=conftest_path),
        autouse=autouse,
        namespace=namespace,
        is_async=is_async,
        depends_on=depends_on,
    )


def make_registry(*defs: FixtureDef) -> FixtureRegistry:
    """Create a ``FixtureRegistry`` from one or more ``FixtureDef``s."""
    reg = FixtureRegistry()
    for d in defs:
        reg.register(d)
    return reg


def make_session(*defs: FixtureDef) -> FixtureSession:
    """Create a ``FixtureSession`` from one or more ``FixtureDef``s."""
    return FixtureSession(list(defs), PluginRegistry())


def make_session_with(name: str, factory: Callable[..., object]) -> FixtureSession:
    """Shortcut: single-fixture session for quick tests."""
    return make_session(make_fixture_def(name, factory, conftest_path="/conftest.py"))


def session_from_declarations(
    declarations: object, *, anchor_package_path: str
) -> FixtureSession:
    """Build a session from a written ``__fixtures__.py``, as the pipeline does.

    Imports the declaration file, registers it against its anchor, and hands the
    resulting defs to a session. This is the ``@oxi.fixture`` counterpart of
    ``conftest_loader.create_session``, which takes conftest paths and goes with
    ``conftest.py`` (#1720).

    Args:
        declarations: Path to the written declaration file.
        anchor_package_path: The directory the declarations are scoped to.
    """
    spec = importlib.util.spec_from_file_location("__fixtures__", str(declarations))
    if spec is None or spec.loader is None:
        msg = f"declaration file is not loadable: {declarations}"
        raise AssertionError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registry = FixtureRegistry()
    register_module_source_fixtures(
        registry, module, anchor_package_path=anchor_package_path
    )
    return FixtureSession(list(registry.all()), PluginRegistry())


def make_meta(
    module_path: str = "t.py",
    fn_name: str = "test_fn",
    param_id: str | None = None,
) -> TestMeta:
    """Create a ``TestMeta`` with sensible defaults for tests."""
    node_id = f"{module_path}::{fn_name}"
    if param_id:
        node_id += f"[{param_id}]"
    return TestMeta(
        module_path=module_path,
        fn_name=fn_name,
        node_id=node_id,
        kind=from_wire(param_id),
    )


_E = TypeVar("_E", bound=BaseException)


def make_exc(exc_type: type[_E], msg: str = "") -> _E:
    """Create an exception with a real traceback attached."""
    try:
        raise exc_type(msg)  # noqa: TRY301
    except exc_type as exc:
        return exc
