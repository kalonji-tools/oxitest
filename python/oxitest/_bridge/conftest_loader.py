from __future__ import annotations

__all__ = [
    "_extract_depends_on",
    "_extract_fixture_type",
    "_extract_helpers",
    "create_conftest_fixtures",
    "create_session",
    "find_conftest_paths",
    "load_fixtures_from_conftest",
]

import collections.abc
import dataclasses
import importlib.util
import logging
import sys
import warnings
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, get_type_hints

from oxitest._bridge._boundary import safe_type_hints
from oxitest._bridge._fixture_registry import (
    ConftestSource,
    FixtureDef,
    FixtureRegistry,
    _fixture_inner_type,
)
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._fixtures import Fixtures
from oxitest._bridge._helper_registry import HelperDef, HelperRegistry
from oxitest._bridge._namespace_validation import validate_namespace_name
from oxitest._bridge.result import CollectedViolation

logger = logging.getLogger(__name__)


def _extract_fixture_type(func: Callable[..., Any]) -> type:
    """Extract the binding type from a fixture function's return annotation.

    ``Yields[T]`` (which resolves to ``Generator[T, None, None]``) is unwrapped
    to ``T`` so callers receive the actual yielded type, not the generator type.

    Raises ``ValueError`` if the function has no return annotation.
    """
    hints = get_type_hints(func)
    ret = hints.get("return")
    if ret is None:
        raise ValueError(
            f"fixture '{getattr(func, '__name__', repr(func))}' has no return type "
            f"annotation. All fixtures must declare their return type for "
            f"type-based resolution."
        )
    # Yields[T] resolves to Generator[T, None, None] via get_type_hints.
    # Unwrap to the yielded type T (first type argument).
    origin = getattr(ret, "__origin__", None)
    if origin is collections.abc.Generator:
        args = getattr(ret, "__args__", ())
        if args:
            return args[0]  # type: ignore[return-value]
    return ret  # type: ignore[return-value]


def _extract_depends_on(func: Callable[..., Any]) -> tuple[tuple[str, type], ...]:
    """Extract dependency declarations from a fixture function.

    Returns a tuple of ``(qualifier, binding_type)`` pairs for parameters
    annotated with ``Fixture[T]``.

    Unannotated parameters are NOT included — they are caught at resolve
    time by ``UnannotatedFixtureParamError`` in ``resolve_for_test``.
    Parameters with plain (non-fixture) type annotations are also ignored.
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


def find_conftest_paths(test_path: str, rootdir: str) -> list[str]:
    """Return conftest.py paths from rootdir down to test file's directory.

    Returns conftest paths in root-first order.
    """
    test_dir = Path(test_path).parent.resolve()
    root = Path(rootdir).resolve()
    try:
        relative = test_dir.relative_to(root)
    except ValueError:
        return []
    dirs = [root] + [
        root.joinpath(*relative.parts[: i + 1]) for i in range(len(relative.parts))
    ]
    return [str(d / "conftest.py") for d in dirs if (d / "conftest.py").exists()]


def _load_conftest_module(path: str) -> ModuleType | None:
    """Load a conftest.py and register it as sys.modules['conftest']."""
    unique_name = f"_oxitest_conftest_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(unique_name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    sys.modules["conftest"] = module
    return module


def _extract_fixtures(module: ModuleType, path: str) -> list[FixtureDef[Any]]:
    """Extract fixture definitions from Fixtures instances in a module."""
    found: list[FixtureDef[Any]] = []
    for attr_name, obj in vars(module).items():
        if not isinstance(obj, Fixtures):
            continue
        namespace_name = obj._namespace_name or attr_name
        validate_namespace_name(namespace_name, path)
        if namespace_name == "oxi":
            raise ValueError(
                f"'oxi' is a reserved namespace name in oxitest. "
                f"Rename your Fixtures() instance in {path}."
            )
        obj._namespace_name = namespace_name
        for defn in obj._defs:
            try:
                ft = _extract_fixture_type(defn.source.func)
            except ValueError:
                ft = object
            deps = _extract_depends_on(defn.source.func)
            stamped = dataclasses.replace(
                defn,
                fixture_type=ft,
                depends_on=deps,
                source=ConftestSource(func=defn.source.func, conftest_path=path),
                namespace=namespace_name,
            )
            found.append(stamped)
    return found


def _extract_helpers(module: ModuleType, path: str) -> list[HelperDef]:
    """Extract helper definitions from Helpers instances in a module."""
    from oxitest._bridge._helpers import Helpers

    found: list[HelperDef] = []
    for attr_name, obj in vars(module).items():
        if not isinstance(obj, Helpers):
            continue
        namespace_name = obj._namespace_name or attr_name
        validate_namespace_name(namespace_name, path)
        if namespace_name == "oxi":
            raise ValueError(
                f"'oxi' is a reserved namespace name in oxitest. "
                f"Rename your Helpers() instance in {path}."
            )
        obj._namespace_name = namespace_name
        for defn in obj._defs:
            stamped = dataclasses.replace(
                defn,
                source=ConftestSource(func=defn.func, conftest_path=path),
                namespace=namespace_name,
            )
            found.append(stamped)
    return found


def _has_helpers(module: ModuleType) -> bool:
    """Return True if module has any Helpers() instances."""
    from oxitest._bridge._helpers import Helpers

    return any(isinstance(obj, Helpers) for obj in vars(module).values())


def load_fixtures_from_conftest(
    path: str,
) -> list[FixtureDef[Any]]:
    """Load conftest.py and return all fixtures registered via Fixtures instances.

    Also registers the module as sys.modules["conftest"] so test files can do
    ``from conftest import my_fixture``. When multiple conftests exist, the last
    (most-local) one registered wins --- test files see only that one.

    Returns an empty list if no Fixtures instance is found (no warning --- caller
    decides).
    """
    module = _load_conftest_module(path)
    if module is None:
        return []
    return _extract_fixtures(module, path)


def create_conftest_fixtures(
    conftest_paths: Sequence[str],
) -> tuple[list[FixtureDef], list[CollectedViolation], HelperRegistry]:
    """Load conftest files and return all fixture defs with any violations.

    Returns a tuple of (fixture_defs, violations, helper_registry) where
    violations contains any strict-mode issues detected during fixture
    registration and helper_registry contains all helpers extracted from
    ``Helpers()`` instances in the conftest files.
    """
    all_defs: list[FixtureDef] = []
    all_violations: list[CollectedViolation] = []
    helper_registry = HelperRegistry()

    # Intentional: _tmp_registry detects registration violations (e.g.
    # missing_return_annotation) here so they can be surfaced to the Rust
    # bridge via create_session(). FixtureSession.__init__ re-registers
    # the same defs but discards the violations returned by register().
    _tmp_registry = FixtureRegistry()

    for path in conftest_paths:
        module = _load_conftest_module(path)
        if module is None:
            continue

        fixtures = _extract_fixtures(module, path)
        has_fixtures = bool(fixtures)
        has_helper_fns = _has_helpers(module)

        if not has_fixtures and not has_helper_fns:
            warnings.warn(
                f"{path}: conftest.py contains no Fixtures instance. "
                "Did you forget to create one? (e.g. `fixtures = oxitest.Fixtures()`)",
                UserWarning,
                stacklevel=2,
            )

        for defn in fixtures:
            all_violations.extend(_tmp_registry.register(defn))
            all_defs.append(defn)

        helper_defs = _extract_helpers(module, path)
        for hdef in helper_defs:
            helper_registry.register(hdef)

    return all_defs, all_violations, helper_registry


def create_session(
    conftest_paths: Sequence[str],
) -> tuple[FixtureSession, list[CollectedViolation]]:
    """Build a FixtureSession from all conftest paths.

    Convenience wrapper around ``create_conftest_fixtures`` that constructs
    the ``FixtureSession`` for callers that still expect one.
    """
    defs, violations, helper_registry = create_conftest_fixtures(conftest_paths)
    session = FixtureSession(defs)
    session.set_helper_registry(helper_registry)
    return session, violations
