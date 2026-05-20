"""Verify Python dataclasses in result.py stay in sync with Rust structs in bridge.rs.

PyO3's FromPyObject deserializes Python objects by field name. If the names
diverge between result.py and bridge.rs, the mismatch causes a runtime panic
with no compile-time protection. This test catches drift before it ships.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re

from oxitest._bridge.result import CollectedItem, TestResult

_BRIDGE_RS = pathlib.Path(__file__).parent.parent.parent / "src" / "bridge.rs"


def _rust_struct_fields(source: str, struct_name: str) -> frozenset[str]:
    """Extract field names from a named Rust struct in source text."""
    pattern = rf"struct\s+{re.escape(struct_name)}\s*\{{([^}}]*)}}"
    match = re.search(pattern, source, re.DOTALL)
    if not match:
        raise AssertionError(f"struct {struct_name!r} not found in src/bridge.rs")
    body = match.group(1)
    # Match lines like `    field_name: Type,` or `    pub field_name: Type,`
    return frozenset(re.findall(r"^\s+(?:pub\s+)?(\w+)\s*:", body, re.MULTILINE))


def _python_fields(cls: type) -> frozenset[str]:
    return frozenset(f.name for f in dataclasses.fields(cls))


def test_bridge_result_fields_match_rust_test_result():
    source = _BRIDGE_RS.read_text()
    rust_fields = _rust_struct_fields(source, "TestResult")
    python_fields = _python_fields(TestResult)
    assert rust_fields == python_fields, (
        "Field mismatch between TestResult (src/bridge.rs) and TestResult"
        " (python/oxitest/_bridge/result.py).\n"
        f"  Only in Rust:   {sorted(rust_fields - python_fields)}\n"
        f"  Only in Python: {sorted(python_fields - rust_fields)}"
    )


def test_collected_item_fields_match_rust_collected_item():
    source = _BRIDGE_RS.read_text()
    rust_fields = _rust_struct_fields(source, "CollectedItem")
    python_fields = _python_fields(CollectedItem)
    assert rust_fields == python_fields, (
        "Field mismatch between CollectedItem (src/bridge.rs) and CollectedItem"
        " (python/oxitest/_bridge/result.py).\n"
        f"  Only in Rust:   {sorted(rust_fields - python_fields)}\n"
        f"  Only in Python: {sorted(python_fields - rust_fields)}"
    )
