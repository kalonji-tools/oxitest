"""Worker entry point for the parallel test runner.

Reads a single JSON task from stdin, runs each test item using executor.run_test,
and writes one JSON result line per test to stdout.

Task schema (stdin):
    {
        "module_path": str,
        "items": [{"fn_name": str, "param_id": str | null}],
        "conftest_paths": [str],
        "timeout_secs": int | null
    }

Result schema (stdout, one line per test):
    {
        "node_id": str,
        "outcome": "passed" | "failed" | "error" | "skipped" | ...,
        "duration_ms": float,
        "failure_repr": str | null,
        "message": str | null,
        "file": str | null,
        "lineno": int | null,
        "source_line": str | null,
        "no_message_lines": [int],
        "left": str | null,
        "right": str | null,
        "op": str | null,
        "strict": bool
    }
"""

from __future__ import annotations

__all__ = ["main", "run"]

import json
import sys
import time
from typing import Any

from oxitest._bridge.result import StatusKind


def _build_node_id(module_path: str, fn_name: str, param_id: str | None) -> str:
    node_id = f"{module_path}::{fn_name}"
    if param_id is not None:
        node_id += f"[{param_id}]"
    return node_id


NON_FAILURE_STATUSES = frozenset(
    {
        StatusKind.PASSED,
        StatusKind.SKIPPED,
        StatusKind.WARNED,
        StatusKind.XFAILED,
        StatusKind.XPASSED,
        StatusKind.TIMEOUT,
    }
)


def _build_failure_repr(result: object) -> str | None:
    """Build a human-readable failure string from a TestResult."""
    status = getattr(result, "status", "")
    if status in NON_FAILURE_STATUSES:
        return None

    parts: list[str] = []

    message = getattr(result, "message", "")
    if message:
        parts.append(message)

    file_ = getattr(result, "file", "")
    lineno = getattr(result, "lineno", 0)
    source_line = getattr(result, "source_line", "")
    if file_:
        location = f"{file_}:{lineno}"
        if source_line:
            location += f"  {source_line}"
        parts.append(location)

    left = getattr(result, "left", "")
    right = getattr(result, "right", "")
    op = getattr(result, "op", "")
    if left:
        if right and op:
            parts.append(f"assert {left} {op} {right}")
        else:
            parts.append(f"assert {left}")

    return "\n".join(parts) if parts else f"Test {status}"


def run(task: dict) -> None:
    from oxitest._bridge.conftest_loader import create_session
    from oxitest._bridge.executor import run_test
    from oxitest._bridge.importer import collect_module

    module_path: str = task["module_path"]
    items: list[dict] = task["items"]
    conftest_paths: list[str] = task.get("conftest_paths") or []
    timeout_secs: int | None = task.get("timeout_secs")

    session = create_session(conftest_paths)

    # Register fixtures declared in the test module itself (e.g. a Fixtures()
    # instance at module level). This mirrors what the serial runner does via
    # collect_module during collection, so self-contained test files that define
    # their own fixtures work correctly in parallel mode too.
    collect_module(module_path, session)

    for item in items:
        fn_name: str = item["fn_name"]
        param_id: str | None = item.get("param_id")

        node_id = _build_node_id(module_path, fn_name, param_id)

        start = time.monotonic()
        result = run_test(
            module_path=module_path,
            fn_name=fn_name,
            session=session,
            param_id=param_id,
            default_timeout=timeout_secs,
        )
        duration_ms = (time.monotonic() - start) * 1000.0

        output = {
            "node_id": node_id,
            "outcome": result.status,
            "duration_ms": duration_ms,
            "failure_repr": _build_failure_repr(result),
            # Structured diagnostic fields (populated by executor.py)
            "message": result.message or None,
            "file": result.file or None,
            "lineno": result.lineno if result.lineno != 0 else None,
            "source_line": result.source_line or None,
            "no_message_lines": result.no_message_lines,
            "left": result.left or None,
            "right": result.right or None,
            "op": result.op or None,
            "strict": result.strict,
            "frames": [
                {"file": f.file, "lineno": f.lineno, "name": f.name, "line": f.line}
                for f in getattr(result, "frames", [])
            ],
        }
        print(json.dumps(output))


def main() -> None:
    """Persistent worker: read newline-delimited JSON tasks from stdin until EOF."""
    # Force line buffering on stdout so each print() flushes on newline.
    # Piped stdout defaults to block buffering (8KB), which starves the
    # Rust watchdog — it expects one result line per test.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]  # ty: ignore[unresolved-attribute]
    for raw in sys.stdin:
        raw = raw.strip()
        if raw:
            task: dict[str, Any] = json.loads(raw)
            run(task)


if __name__ == "__main__":
    main()
