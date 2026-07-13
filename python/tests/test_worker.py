"""Tests for the parallel worker subprocess: JSON task protocol and result fields."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from oxitest import TempDir


def _make_task(path: str, fn_name: str) -> dict:
    """Build a minimal worker task for a single test function."""
    return {
        "module_path": path,
        "items": [
            {
                "fn_name": fn_name,
                "param_id": "",
                "node_id": f"{path}::{fn_name}",
                "markers": [],
            }
        ],
        "conftest_paths": [],
        "timeout_secs": None,
    }


def _run_worker(task: dict) -> list[dict]:
    """Spawn worker.py as a subprocess, feed it one task, collect result lines."""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    result = subprocess.run(
        [sys.executable, "-m", "oxitest._bridge.worker"],
        input=json.dumps(task),
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=30,
    )
    lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def test_worker_runs_passing_test(tmp: TempDir) -> None:
    """Worker should return outcome='passed' for a test with a passing assertion."""
    test_file = tmp / "test_pass.py"
    test_file.write_text("def test_pass():\n    assert 1 == 1\n")

    results = _run_worker(_make_task(str(test_file), "test_pass"))

    assert len(results) == 1, f"expected 1 result from worker, got {len(results)}"
    assert results[0]["node_id"].endswith("::test_pass"), (
        f"node_id should end with '::test_pass', got {results[0]['node_id']!r}"
    )
    assert results[0]["outcome"] == "passed", (
        f"expected outcome='passed', got {results[0]['outcome']!r}"
    )


def test_worker_runs_failing_test(tmp: TempDir) -> None:
    """Worker should return outcome='failed' for a test with a failing assertion."""
    test_file = tmp / "test_fail.py"
    test_file.write_text("def test_fail():\n    assert 1 == 2\n")

    results = _run_worker(_make_task(str(test_file), "test_fail"))

    assert len(results) == 1, f"expected 1 result from worker, got {len(results)}"
    assert results[0]["outcome"] == "failed", (
        f"expected outcome='failed' for assertion failure, got "
        f"{results[0]['outcome']!r}"
    )


def test_worker_result_has_required_fields(tmp: TempDir) -> None:
    """Worker result JSON must include node_id, outcome, and duration_ms fields."""
    test_file = tmp / "test_fields.py"
    test_file.write_text("def test_x():\n    pass\n")

    results = _run_worker(_make_task(str(test_file), "test_x"))

    r = results[0]
    assert "node_id" in r, f"worker result missing 'node_id' field, got keys: {list(r)}"
    assert "outcome" in r, f"worker result missing 'outcome' field, got keys: {list(r)}"
    assert "duration_ms" in r, (
        f"worker result missing 'duration_ms' field, got keys: {list(r)}"
    )
    assert "failure_repr" not in r, (
        f"passing test should not emit 'failure_repr', got keys: {list(r)}"
    )


def test_worker_emits_structured_failure_fields(tmp: TempDir) -> None:
    """Failed worker result includes file, lineno, left, right, op, and source_line."""
    test_file = tmp / "test_structured.py"
    test_file.write_text(
        "def test_simple_assert():\n    x = 1\n    y = 2\n    assert x == y\n"
    )

    results = _run_worker(_make_task(str(test_file), "test_simple_assert"))

    assert len(results) == 1, f"expected 1 result, got {len(results)}"
    r = results[0]
    assert r["outcome"] == "failed", (
        f"expected outcome='failed' for assertion failure, got {r['outcome']!r}"
    )
    assert r.get("file") not in (None, ""), f"file missing or empty: {r}"
    assert r.get("lineno") not in (None, 0), f"lineno missing or zero: {r}"
    assert r.get("left") not in (None, ""), f"left missing or empty: {r}"
    assert r.get("right") not in (None, ""), f"right missing or empty: {r}"
    assert r.get("op") not in (None, ""), f"op missing or empty: {r}"
    assert r.get("source_line") not in (None, ""), f"source_line missing or empty: {r}"
