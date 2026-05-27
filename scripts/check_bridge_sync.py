#!/usr/bin/env python3
"""Check that Rust FromPyObject structs stay in sync with Python dataclasses.

Compares field names in src/bridge.rs against python/oxitest/_bridge/result.py.
Exits 0 if all pairs match, 1 with a diff if any mismatch is found.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# Repo root is two levels up from scripts/
ROOT = Path(__file__).resolve().parent.parent
RUST_PATH = ROOT / "src" / "bridge.rs"
PYTHON_PATH = ROOT / "python" / "oxitest" / "_bridge" / "result.py"

# Rust struct name -> Python class name
PAIRS = {
    "TestResult": "TestResult",
    "BridgeFrame": "Frame",
    "CollectedItem": "CollectedItem",
    "RawViolation": "CollectedViolation",
}


def parse_rust_structs(path: Path) -> dict[str, set[str]]:
    """Extract field names from #[derive(FromPyObject)] structs."""
    text = path.read_text()
    structs: dict[str, set[str]] = {}
    # Match struct blocks preceded by a FromPyObject derive
    pattern = re.compile(
        r"#\[derive\([^)]*FromPyObject[^)]*\)\]\s*"
        r"(?:pub(?:\(crate\))?\s+)?struct\s+(\w+)\s*\{([^}]+)\}",
        re.DOTALL,
    )
    field_pattern = re.compile(r"^\s*(?:pub(?:\(crate\))?\s+)?(\w+)\s*:", re.MULTILINE)
    for m in pattern.finditer(text):
        name = m.group(1)
        body = m.group(2)
        fields = set(field_pattern.findall(body))
        structs[name] = fields
    return structs


def parse_python_classes(path: Path) -> dict[str, set[str]]:
    """Extract field names from @dataclass classes."""
    tree = ast.parse(path.read_text())
    classes: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        is_dataclass = any(
            (isinstance(d, ast.Name) and d.id == "dataclass")
            or (isinstance(d, ast.Attribute) and d.attr == "dataclass")
            for d in node.decorator_list
        )
        if not is_dataclass:
            continue
        fields: set[str] = set()
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                fields.add(item.target.id)
        classes[node.name] = fields
    return classes


WIRE_RUST_PATH = ROOT / "src" / "worker_result.rs"
WORKER_PY_PATH = ROOT / "python" / "oxitest" / "_bridge" / "worker.py"


def _parse_serde_struct_fields(text: str, struct_name: str) -> set[str]:
    """Extract field names from a named serde struct."""
    pattern = re.compile(
        rf"struct\s+{struct_name}\s*(?:<[^>]+>)?\s*\{{([^}}]+)\}}",
        re.DOTALL,
    )
    field_pattern = re.compile(r"^\s*pub\s+(\w+)\s*:", re.MULTILINE)
    m = pattern.search(text)
    if m is None:
        return set()
    return set(field_pattern.findall(m.group(1)))


def parse_worker_result_fields(path: Path) -> set[str]:
    """Extract field names from the WorkerResult serde struct."""
    return _parse_serde_struct_fields(path.read_text(), "WorkerResult")


def parse_worker_task_item_fields(path: Path) -> set[str]:
    """Extract field names from the WorkerTaskItem serde struct."""
    return _parse_serde_struct_fields(path.read_text(), "WorkerTaskItem")


def parse_worker_item_reads(path: Path) -> set[str]:
    """Extract field names that worker.py reads from task item dicts.

    Matches patterns like item["fn_name"] and item.get("param_id").
    """
    text = path.read_text()
    fields: set[str] = set()
    for m in re.finditer(r'item\["(\w+)"\]', text):
        fields.add(m.group(1))
    for m in re.finditer(r'item\.get\("(\w+)"', text):
        fields.add(m.group(1))
    return fields


def parse_to_wire_fields(path: Path) -> set[str]:
    """Extract field names emitted by TestResult.to_wire().

    Parses the output dict literal and optional dict in to_wire().
    """
    text = path.read_text()
    fields: set[str] = set()
    # Match quoted keys in dict literals within to_wire method
    # Required: "node_id", "outcome", "duration_ms"
    # Optional: "failure_repr", "message", "file", etc.
    in_to_wire = False
    for line in text.splitlines():
        if "def to_wire" in line:
            in_to_wire = True
            continue
        if in_to_wire:
            if line and not line[0].isspace() and not line.strip().startswith("#"):
                break  # next top-level def/class
            for m in re.finditer(r'"(\w+)":', line):
                fields.add(m.group(1))
    return fields


def main() -> int:
    rust = parse_rust_structs(RUST_PATH)
    python = parse_python_classes(PYTHON_PATH)
    errors = 0

    for rust_name, py_name in PAIRS.items():
        rust_fields = rust.get(rust_name)
        py_fields = python.get(py_name)
        if rust_fields is None:
            print(f"ERROR: Rust struct '{rust_name}' not found in {RUST_PATH}")
            errors += 1
            continue
        if py_fields is None:
            print(f"ERROR: Python class '{py_name}' not found in {PYTHON_PATH}")
            errors += 1
            continue
        rust_only = rust_fields - py_fields
        py_only = py_fields - rust_fields
        if rust_only or py_only:
            print(f"MISMATCH: {rust_name} (Rust) vs {py_name} (Python)")
            if rust_only:
                print(f"  Rust-only fields: {sorted(rust_only)}")
            if py_only:
                print(f"  Python-only fields: {sorted(py_only)}")
            errors += 1

    # ── Wire format check ─────────────────────────────────────────────
    rust_wire = parse_worker_result_fields(WIRE_RUST_PATH)
    py_wire = parse_to_wire_fields(PYTHON_PATH)

    if not rust_wire:
        print(f"ERROR: WorkerResult not found in {WIRE_RUST_PATH}")
        errors += 1
    elif not py_wire:
        print(f"ERROR: to_wire() fields not found in {PYTHON_PATH}")
        errors += 1
    else:
        rust_only = rust_wire - py_wire
        py_only = py_wire - rust_wire
        if rust_only or py_only:
            print("MISMATCH: WorkerResult (Rust wire) vs to_wire() (Python wire)")
            if rust_only:
                print(f"  Rust-only wire fields: {sorted(rust_only)}")
            if py_only:
                print(f"  Python-only wire fields: {sorted(py_only)}")
            errors += 1

    # ── Task input format check ────────────────────────────────────────
    rust_task_fields = parse_worker_task_item_fields(WIRE_RUST_PATH)
    py_task_fields = parse_worker_item_reads(WORKER_PY_PATH)

    if not rust_task_fields:
        print(f"ERROR: WorkerTaskItem not found in {WIRE_RUST_PATH}")
        errors += 1
    elif not py_task_fields:
        print(f"ERROR: item field reads not found in {WORKER_PY_PATH}")
        errors += 1
    else:
        rust_only = rust_task_fields - py_task_fields
        py_only = py_task_fields - rust_task_fields
        if rust_only or py_only:
            print("MISMATCH: WorkerTaskItem (Rust) vs worker.py item reads (Python)")
            if rust_only:
                print(f"  Rust-only task fields: {sorted(rust_only)}")
            if py_only:
                print(f"  Python-only task fields: {sorted(py_only)}")
            errors += 1

    if errors == 0:
        print(
            f"OK: all {len(PAIRS)} bridge contracts + wire format + task format in sync"
        )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
