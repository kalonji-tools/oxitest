"""Execute Python doctests via stdlib doctest module."""

from __future__ import annotations

import doctest
import importlib
import importlib.util
import sys
from typing import TYPE_CHECKING

from oxitest._bridge.result import StatusKind, TestResult

if TYPE_CHECKING:
    pass

__all__ = ["run_doctest"]


def _resolve_object(module, dotted_name: str) -> object:  # type: ignore[type-arg]
    """Resolve a dotted name like 'module.Class.method' to the actual object."""
    parts = dotted_name.split(".")
    # First part is the module name itself, skip it
    obj = module
    for part in parts[1:]:
        obj = getattr(obj, part)
    return obj


def run_doctest(module_path: str, name: str) -> TestResult:
    """Run doctests for a single object identified by dotted name.

    Args:
        module_path: Filesystem path to the Python module.
        name: Dotted name of the object (e.g. "mymodule.Calculator.add").

    Returns:
        TestResult with pass/fail status and diagnostic info.
    """
    # Import the module
    unique_name = f"_oxitest_doctest_{id(module_path)}"
    spec = importlib.util.spec_from_file_location(unique_name, module_path)
    if spec is None or spec.loader is None:
        return TestResult(
            status=StatusKind.ERROR,
            message=f"Cannot import {module_path}",
            file=module_path,
        )

    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        return TestResult(
            status=StatusKind.ERROR,
            message=f"Import error: {exc}",
            file=module_path,
        )

    # Resolve the target object
    try:
        obj = _resolve_object(module, name)
    except AttributeError as exc:
        return TestResult(
            status=StatusKind.ERROR,
            message=f"Cannot resolve {name}: {exc}",
            file=module_path,
        )

    # Find and run doctests
    finder = doctest.DocTestFinder()
    try:
        tests = finder.find(obj, name)
    except Exception as exc:
        return TestResult(
            status=StatusKind.ERROR,
            message=f"DocTestFinder error: {exc}",
            file=module_path,
        )

    if not tests:
        return TestResult(status=StatusKind.PASSED)

    # Run the first test (there's typically one per object)
    runner = doctest.DocTestRunner(verbose=False)
    failures: list[doctest.DocTestFailure] = []

    for test in tests:
        if not test.examples:
            continue
        try:
            runner.run(test, out=lambda x: None)
        except doctest.DocTestFailure as f:
            failures.append(f)

    if runner.summarize(verbose=False).failed > 0 or failures:
        # Re-run to capture output for diagnostics
        output_lines: list[str] = []
        capture_runner = doctest.DocTestRunner(
            verbose=False, optionflags=doctest.ELLIPSIS
        )
        for test in tests:
            if not test.examples:
                continue
            capture_runner.run(test, out=output_lines.append)

        message = "".join(output_lines).strip()
        if not message:
            message = "Doctest failed"

        lineno = tests[0].lineno or 0
        return TestResult(
            status=StatusKind.FAILED,
            message=message,
            file=module_path,
            lineno=lineno + 1,  # 0-indexed to 1-indexed
        )

    return TestResult(status=StatusKind.PASSED)
