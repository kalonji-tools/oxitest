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

# Rust struct name -> Python class name (or list of class names for union types)
#
# Note: TestResult was removed in #1206 — the runtime contract is enforced by
# getattr() calls in extract_outcome(), and the unified conversion logic lives
# in RawOutcome::into_test_outcome().  The wire-format check below still
# validates the JSON worker ↔ Python to_wire() contract.
PAIRS: dict[str, str | list[str]] = {
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
            or (
                isinstance(d, ast.Call)
                and (
                    (isinstance(d.func, ast.Name) and d.func.id == "dataclass")
                    or (
                        isinstance(d.func, ast.Attribute) and d.func.attr == "dataclass"
                    )
                )
            )
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


REPORTER_RUST_PATH = ROOT / "src" / "reporter" / "bridge.rs"

REPORTER_PAIRS = {
    "BridgeCacheEntry": "CacheEntry",
    "BridgeCacheStats": "CacheStats",
    "BridgeFixtureTiming": "FixtureTiming",
}

WIRE_RUST_PATH = ROOT / "src" / "worker_result" / "wire.rs"
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
    """Extract field names from the WireResult serde enum (or struct fallback).

    With internally-tagged enums, fields are distributed across variants.
    We collect the union of all variant fields, plus the tag field ("outcome").
    """
    text = path.read_text()
    # Try enum first (internally-tagged enum)
    enum_pattern = re.compile(
        r"enum\s+WireResult\s*\{(.+?)^}",
        re.DOTALL | re.MULTILINE,
    )
    m = enum_pattern.search(text)
    if m:
        body = m.group(1)
        field_pattern = re.compile(r"^\s+(\w+)\s*:", re.MULTILINE)
        fields = set(field_pattern.findall(body))
        # The tag field "outcome" drives variant selection but is not a Rust field
        fields.add("outcome")
        # Remove serde rename attributes that aren't actual field names
        fields.discard("serde")
        return fields
    # Fall back to struct-based extraction
    return _parse_serde_struct_fields(text, "WireResult")


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
    """Extract wire field names from per-outcome to_wire() methods and helpers.

    Collects:
    - Dict keys from _wire_base (required fields)
    - Keyword argument names from _wire_optional calls (optional fields)
    - Direct output[...] assignments (frames, field_diffs)
    """
    text = path.read_text()
    fields: set[str] = set()
    # Required fields from _wire_base dict literal
    for m in re.finditer(r'"(\w+)"\s*:', text):
        key = m.group(1)
        if key in ("node_id", "outcome", "duration_ms", "protocol_version"):
            fields.add(key)
    # Optional fields from _wire_optional keyword args
    for m in re.finditer(r"_wire_optional\([^)]+\)", text, re.DOTALL):
        call = m.group(0)
        for kw in re.finditer(r"(\w+)\s*=\s*self\.", call):
            fields.add(kw.group(1))
    # Direct output[...] assignments (e.g., output["frames"] = ...)
    for m in re.finditer(r'output\["(\w+)"\]', text):
        fields.add(m.group(1))
    return fields


def _resolve_py_fields(
    py_name: str | list[str], python: dict[str, set[str]]
) -> tuple[set[str] | None, list[str]]:
    """Resolve Python field set for a single or union py_name.

    Returns (fields, missing_classes). fields is None on error (single class missing).
    """
    if isinstance(py_name, list):
        py_fields: set[str] = {"status"}
        missing: list[str] = []
        for cls_name in py_name:
            cls_fields = python.get(cls_name)
            if cls_fields is None:
                missing.append(cls_name)
            else:
                py_fields |= cls_fields
        return (None if missing else py_fields), missing
    cls_fields = python.get(py_name)
    return cls_fields, []


def _check_main_pairs(rust: dict[str, set[str]], python: dict[str, set[str]]) -> int:
    """Check main bridge pairs (CollectedItem, RawViolation)."""
    errors = 0
    for rust_name, py_name in PAIRS.items():
        rust_fields = rust.get(rust_name)
        if rust_fields is None:
            print(f"ERROR: Rust struct '{rust_name}' not found in {RUST_PATH}")
            errors += 1
            continue
        py_fields, missing_classes = _resolve_py_fields(py_name, python)
        if missing_classes:
            print(f"ERROR: Python classes {missing_classes} not found in {PYTHON_PATH}")
            errors += 1
            continue
        if py_fields is None:
            print(f"ERROR: Python class '{py_name}' not found in {PYTHON_PATH}")
            errors += 1
            continue
        rust_only = rust_fields - py_fields
        py_only = py_fields - rust_fields
        # CollectedItem.param_id (Rust) reads a @property backed by
        # ``kind: TestKind`` on the Python side (#1564). PyO3 FromPyObject
        # resolves via attribute lookup, so the property satisfies the Rust
        # field. If we grow a second such bridge, promote this to a table.
        if (
            rust_name == "CollectedItem"
            and rust_only == {"param_id"}
            and "kind" in py_only
        ):
            rust_only = set()
            py_only = py_only - {"kind"}
        label = py_name if isinstance(py_name, str) else "per-outcome union"
        if rust_only or py_only:
            print(f"MISMATCH: {rust_name} (Rust) vs {label} (Python)")
            if rust_only:
                print(f"  Rust-only fields: {sorted(rust_only)}")
            if py_only:
                print(f"  Python-only fields: {sorted(py_only)}")
            errors += 1
    return errors


def _check_reporter_pairs(python: dict[str, set[str]]) -> int:
    """Check reporter bridge pairs."""
    errors = 0
    reporter_rust = parse_rust_structs(REPORTER_RUST_PATH)
    for rust_name, py_name in REPORTER_PAIRS.items():
        rust_fields = reporter_rust.get(rust_name)
        py_fields = python.get(py_name)
        if rust_fields is None:
            print(f"ERROR: Rust struct '{rust_name}' not found in {REPORTER_RUST_PATH}")
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
    return errors


def _check_raw_frame(python: dict[str, set[str]]) -> int:
    """Check RawFrame (Rust) vs Frame (Python)."""
    raw_frame_fields = _parse_serde_struct_fields(
        WIRE_RUST_PATH.read_text(), "RawFrame"
    )
    py_frame_fields = python.get("Frame")
    if not raw_frame_fields:
        print(f"ERROR: RawFrame not found in {WIRE_RUST_PATH}")
        return 1
    if py_frame_fields is None:
        print(f"ERROR: Python class 'Frame' not found in {PYTHON_PATH}")
        return 1
    rust_only = raw_frame_fields - py_frame_fields
    py_only = py_frame_fields - raw_frame_fields
    if rust_only or py_only:
        print("MISMATCH: RawFrame (Rust) vs Frame (Python)")
        if rust_only:
            print(f"  Rust-only fields: {sorted(rust_only)}")
        if py_only:
            print(f"  Python-only fields: {sorted(py_only)}")
        return 1
    return 0


def _check_wire_format() -> int:
    """Check WireResult (Rust) vs to_wire() (Python)."""
    rust_wire = parse_worker_result_fields(WIRE_RUST_PATH)
    py_wire = parse_to_wire_fields(PYTHON_PATH)
    if not rust_wire:
        print(f"ERROR: WireResult not found in {WIRE_RUST_PATH}")
        return 1
    if not py_wire:
        print(f"ERROR: to_wire() fields not found in {PYTHON_PATH}")
        return 1
    rust_only = rust_wire - py_wire
    py_only = py_wire - rust_wire
    if rust_only or py_only:
        print("MISMATCH: WireResult (Rust wire) vs to_wire() (Python wire)")
        if rust_only:
            print(f"  Rust-only wire fields: {sorted(rust_only)}")
        if py_only:
            print(f"  Python-only wire fields: {sorted(py_only)}")
        return 1
    return 0


def _check_task_format() -> int:
    """Check WorkerTaskItem (Rust) vs worker.py item reads."""
    rust_task_fields = parse_worker_task_item_fields(WIRE_RUST_PATH)
    py_task_fields = parse_worker_item_reads(WORKER_PY_PATH)
    if not rust_task_fields:
        print(f"ERROR: WorkerTaskItem not found in {WIRE_RUST_PATH}")
        return 1
    if not py_task_fields:
        print(f"ERROR: item field reads not found in {WORKER_PY_PATH}")
        return 1
    rust_only = rust_task_fields - py_task_fields
    py_only = py_task_fields - rust_task_fields
    if rust_only or py_only:
        print("MISMATCH: WorkerTaskItem (Rust) vs worker.py item reads (Python)")
        if rust_only:
            print(f"  Rust-only task fields: {sorted(rust_only)}")
        if py_only:
            print(f"  Python-only task fields: {sorted(py_only)}")
        return 1
    return 0


def main() -> int:
    """Parse Rust/Python structs, compare for sync, and return mismatch count."""
    rust = parse_rust_structs(RUST_PATH)
    python = parse_python_classes(PYTHON_PATH)
    errors = 0
    errors += _check_main_pairs(rust, python)
    errors += _check_reporter_pairs(python)
    errors += _check_raw_frame(python)
    errors += _check_wire_format()
    errors += _check_task_format()
    if errors == 0:
        total = len(PAIRS) + len(REPORTER_PAIRS) + 1  # +1 for RawFrame
        print(f"OK: all {total} bridge contracts + wire format + task format in sync")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
