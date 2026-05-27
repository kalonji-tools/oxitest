"""Enable coverage collection in worker subprocesses.

When COVERAGE_PROCESS_START is set, this module (auto-imported by Python
via PYTHONPATH) calls coverage.process_startup() so that worker processes
spawned by oxitest's parallel executor also record coverage data.

The .coverage.* files are merged by `coverage combine` in CI.
"""

try:
    import coverage  # ty: ignore[unresolved-import]

    coverage.process_startup()
except ImportError:
    pass
