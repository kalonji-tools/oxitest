from __future__ import annotations

__all__ = ["find_conftest_paths", "load_fixtures_from_conftest", "create_session"]

import dataclasses
import importlib.util
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from oxitest._bridge._helper_namespace import build_helpers
from oxitest._bridge._namespace_validation import validate_namespace_name
from oxitest._bridge.fixtures import (
    FixtureDef,
    FixtureRegistry,
    Fixtures,
    FixtureSession,
)


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
    for attr_name in vars(module):
        obj = getattr(module, attr_name)
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
            stamped = dataclasses.replace(
                defn, conftest_path=path, namespace=namespace_name
            )
            found.append(stamped)
    return found


def _has_helpers(module: ModuleType) -> bool:
    """Return True if module has any public callables that aren't Fixtures."""
    return any(
        callable(getattr(module, name))
        and not name.startswith("_")
        and not isinstance(getattr(module, name), Fixtures)
        for name in vars(module)
    )


def load_fixtures_from_conftest(path: str) -> list[FixtureDef[Any]]:
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


def create_session(conftest_paths: Sequence[str]) -> FixtureSession:
    """Build a FixtureRegistry from all conftest paths and return a FixtureSession.

    Also assembles a HelperNamespace from public callables in each conftest
    and attaches it as ``sys.modules["conftest"].helpers``.
    """
    registry = FixtureRegistry()
    conftest_chain: list[tuple[ModuleType, Path]] = []

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
            registry.register(defn)

        conftest_chain.append((module, Path(path).parent))

    helpers = build_helpers(conftest_chain)

    # Attach helpers to the last-registered conftest module (the one test files see)
    conftest_mod = sys.modules.get("conftest")
    if conftest_mod is not None:
        conftest_mod.helpers = helpers  # ty: ignore[unresolved-attribute]

    return FixtureSession(registry)
