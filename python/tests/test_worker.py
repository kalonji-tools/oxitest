from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile


def _run_worker(task: dict) -> list[dict]:
    """Spawn worker.py as a subprocess, feed it one task, collect result lines."""
    # Pass the current sys.path as PYTHONPATH so the subprocess can import oxitest.
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


def test_worker_runs_passing_test():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def test_pass():\n    assert 1 == 1\n")
        path = f.name

    results = _run_worker(
        {
            "module_path": path,
            "items": [{"fn_name": "test_pass", "param_id": None}],
            "conftest_paths": [],
            "timeout_secs": None,
        }
    )
    assert len(results) == 1, f"expected 1 result from worker, got {len(results)}"
    assert results[0]["node_id"].endswith("::test_pass"), (
        f"node_id should end with '::test_pass', got {results[0]['node_id']!r}"
    )
    assert results[0]["outcome"] == "passed", (
        f"expected outcome='passed', got {results[0]['outcome']!r}"
    )


def test_worker_runs_failing_test():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def test_fail():\n    assert 1 == 2\n")
        path = f.name

    results = _run_worker(
        {
            "module_path": path,
            "items": [{"fn_name": "test_fail", "param_id": None}],
            "conftest_paths": [],
            "timeout_secs": None,
        }
    )
    assert len(results) == 1, f"expected 1 result from worker, got {len(results)}"
    assert results[0]["outcome"] == "failed", (
        f"expected outcome='failed' for assertion failure, got "
        f"{results[0]['outcome']!r}"
    )


def test_worker_result_has_required_fields():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def test_x():\n    pass\n")
        path = f.name

    results = _run_worker(
        {
            "module_path": path,
            "items": [{"fn_name": "test_x", "param_id": None}],
            "conftest_paths": [],
            "timeout_secs": None,
        }
    )
    r = results[0]
    assert "node_id" in r, f"worker result missing 'node_id' field, got keys: {list(r)}"
    assert "outcome" in r, f"worker result missing 'outcome' field, got keys: {list(r)}"
    assert "duration_ms" in r, (
        f"worker result missing 'duration_ms' field, got keys: {list(r)}"
    )
    assert "failure_repr" in r, (
        f"worker result missing 'failure_repr' field, got keys: {list(r)}"
    )


def test_worker_emits_structured_failure_fields():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def test_simple_assert():\n    x = 1\n    y = 2\n    assert x == y\n")
        path = f.name

    results = _run_worker(
        {
            "module_path": path,
            "items": [{"fn_name": "test_simple_assert", "param_id": None}],
            "conftest_paths": [],
            "timeout_secs": None,
        }
    )
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
