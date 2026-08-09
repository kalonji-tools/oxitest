"""Execute Python doctests via stdlib doctest module."""

from __future__ import annotations

import doctest
import hashlib
import importlib
import importlib.util
import sys
from types import ModuleType
from typing import TYPE_CHECKING

from oxitest._bridge._loader import ModuleCache, already_imported
from oxitest._bridge.result import ErrorResult, FailedResult, PassedResult

if TYPE_CHECKING:
    from oxitest._bridge.result import TestResult

__all__ = ["run_doctest"]


def _resolve_object(module: ModuleType, dotted_name: str) -> object:
    """Resolve a dotted name like 'module.Class.method' to the actual object."""
    parts = dotted_name.split(".")
    # First part is the module name itself, skip it
    obj = module
    for part in parts[1:]:
        obj = getattr(obj, part)
    return obj


def _doctest_module_name(module_path: str) -> str:
    """Stable per-path sys.modules key, mirroring executor._exec_unique_name.

    A distinct prefix from the other load routes', so no two routes can share
    a sys.modules entry for one path (#1962).
    """
    digest = hashlib.md5(module_path.encode(), usedforsecurity=False)
    return f"_oxitest_doctest_{digest.hexdigest()[:12]}"


# oxitest namespaces every sys.modules key it synthesises under `_oxitest_`
# (exec / collect / doctest / conftest / fixture-module routes). Matching the
# namespace rather than listing the routes means a sixth route is covered the
# day it is added — an enumeration here was already missing two routes on the
# day it was written (#1962). The compiled extension is `oxitest._oxitest`,
# which does not start with the prefix and so is unaffected.
def _import_doctest_module(
    module_path: str, cache: ModuleCache
) -> ModuleType | ErrorResult:
    """Import a module by file path for doctest execution.

    Reused from *cache* when the module group already loaded it: executing the
    body again re-runs every class definition in it (#1962). Failing that,
    reused from ``sys.modules`` if the file is already imported under its
    real dotted name — see ``already_imported`` in ``_loader``.

    Returns the imported module on success, or an ErrorResult on failure.
    """
    cached = cache.get(module_path, kind="doctest")
    if cached is not None:
        return cached

    canonical = already_imported(module_path)
    if canonical is not None:
        cache.set(module_path, canonical, kind="doctest")
        return canonical

    unique_name = _doctest_module_name(module_path)
    spec = importlib.util.spec_from_file_location(unique_name, module_path)
    if spec is None or spec.loader is None:
        return ErrorResult(
            message=f"Cannot import {module_path}",
            file=module_path,
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 — module execution can raise anything
        # Mirror _load_module: a half-executed module must not stay visible.
        sys.modules.pop(unique_name, None)
        return ErrorResult(
            message=f"Import error: {exc}",
            file=module_path,
        )
    cache.set(module_path, module, kind="doctest")
    return module


def _find_doctests(
    module: ModuleType, module_path: str, name: str
) -> list[doctest.DocTest] | ErrorResult:
    """Resolve the target object and find its doctests.

    Returns a list of DocTest objects on success, or an ErrorResult on failure.
    """
    try:
        obj = _resolve_object(module, name)
    except AttributeError as exc:
        return ErrorResult(
            message=f"Cannot resolve {name}: {exc}",
            file=module_path,
        )
    finder = doctest.DocTestFinder()
    try:
        return finder.find(obj, name)
    except Exception as exc:  # noqa: BLE001 — doctest introspection can raise anything
        return ErrorResult(
            message=f"DocTestFinder error: {exc}",
            file=module_path,
        )


def _run_doctests(tests: list[doctest.DocTest], module_path: str) -> TestResult:
    """Run discovered doctests and return pass/fail result with diagnostics."""
    runner = doctest.DocTestRunner(verbose=False)
    failures: list[doctest.DocTestFailure] = []

    for test in tests:
        if not test.examples:
            continue
        try:
            runner.run(test, out=lambda _: None)
        except doctest.DocTestFailure as f:
            failures.append(f)

    if runner.summarize(verbose=False).failed == 0 and not failures:
        return PassedResult()

    # Re-run to capture output for diagnostics
    output_lines: list[str] = []
    capture_runner = doctest.DocTestRunner(verbose=False, optionflags=doctest.ELLIPSIS)
    for test in tests:
        if not test.examples:
            continue
        capture_runner.run(test, out=output_lines.append)

    message = "".join(output_lines).strip()
    if not message:
        message = "Doctest failed"

    lineno = tests[0].lineno or 0
    return FailedResult(
        message=message,
        file=module_path,
        lineno=lineno + 1,  # 0-indexed to 1-indexed
    )


def run_doctest(module_path: str, name: str, cache: ModuleCache) -> TestResult:
    """Run doctests for a single object identified by dotted name.

    Args:
        module_path: Filesystem path to the Python module.
        name: Dotted name of the object (e.g. "mymodule.Calculator.add").
        cache: The module group's cache. A module can carry several doctest
            items (one per documented function/method); routing them through
            the same cache entry means they share one module object instead
            of each re-executing the body and re-registering every class
            definition in it (#1962).

    Returns:
        TestResult with pass/fail status and diagnostic info.

    """
    result = _import_doctest_module(module_path, cache)
    if isinstance(result, ErrorResult):
        return result
    module = result

    found = _find_doctests(module, module_path, name)
    if isinstance(found, ErrorResult):
        return found
    tests = found

    if not tests:
        return PassedResult()

    return _run_doctests(tests, module_path)
