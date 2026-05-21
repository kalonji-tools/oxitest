from __future__ import annotations

import dataclasses
import importlib.util
import sys
import warnings
from pathlib import Path
from typing import Any

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


def load_fixtures_from_conftest(path: str) -> list[FixtureDef[Any]]:
    """Load conftest.py and return all fixtures registered via Fixtures instances.

    Also registers the module as sys.modules["conftest"] so test files can do
    `from conftest import my_fixture`. When multiple conftests exist, the last
    (most-local) one registered wins — test files see only that one.

    Returns an empty list if no Fixtures instance is found (no warning — caller
    decides).

    """
    unique_name = f"_oxitest_conftest_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(unique_name, path)
    if spec is None or spec.loader is None:
        return []
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    sys.modules["conftest"] = (
        module  # register so test files can: from conftest import ...
    )

    found: list[FixtureDef[Any]] = []
    for attr_name in vars(module):
        obj = getattr(module, attr_name)
        if not isinstance(obj, Fixtures):
            continue
        # Determine namespace name: explicit name= beats variable name
        namespace_name = obj._namespace_name or attr_name
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


def create_session(conftest_paths: list[str]) -> FixtureSession:
    """Build a FixtureRegistry from all conftest paths and return a FixtureSession."""
    registry = FixtureRegistry()
    for path in conftest_paths:
        defs = load_fixtures_from_conftest(path)
        if not defs:
            warnings.warn(
                f"{path}: conftest.py contains no Fixtures instance. "
                "Did you forget to create one? (e.g. `fixtures = oxitest.Fixtures()`)",
                UserWarning,
                stacklevel=2,
            )
        for defn in defs:
            registry.register(defn)
    return FixtureSession(registry)
