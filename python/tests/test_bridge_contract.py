"""Contract tests for the Rust-Python bridge.

Verifies that:
- PyO3 FromPyObject struct field names match Python dataclass fields
- ViolationKind enum variants match between Python and Rust
- Wire format (to_wire / WorkerResult) round-trips correctly
- Cross-language constants (PROTOCOL_VERSION) stay in sync

These tests catch drift between result.py and bridge.rs / worker_result.rs
before it ships. PyO3's FromPyObject deserializes by field name — mismatches
cause runtime panics with no compile-time protection.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
from dataclasses import dataclass

import oxitest as oxi
from oxitest._bridge.result import (
    PROTOCOL_VERSION,
    CollectedItem,
    CollectedViolation,
    Frame,
    StatusKind,
    TestResult,
    ViolationKind,
)

# ── Paths to Rust source files ────────────────────────────────────────────────

_SRC_DIR = pathlib.Path(__file__).parent.parent.parent / "src"
_BRIDGE_RS = _SRC_DIR / "bridge.rs"
_WORKER_RESULT_RS = _SRC_DIR / "worker_result.rs"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _rust_struct_fields(source: str, struct_name: str) -> frozenset[str]:
    """Extract field names from a named Rust struct in source text."""
    pattern = rf"struct\s+{re.escape(struct_name)}\s*\{{([^}}]*)}}"
    match = re.search(pattern, source, re.DOTALL)
    if not match:
        raise AssertionError(f"struct {struct_name!r} not found in Rust source")
    body = match.group(1)
    return frozenset(re.findall(r"^\s+(?:pub\s+)?(\w+)\s*:", body, re.MULTILINE))


def _python_fields(cls: type) -> frozenset[str]:
    return frozenset(f.name for f in dataclasses.fields(cls))


def _rust_violation_kind_values(source: str) -> frozenset[str]:
    """Extract string literals from the ViolationKind match arms in bridge.rs.

    The ``_ => Unknown`` catch-all arm has no string literal and is excluded
    by design — ``Unknown`` has no corresponding Python enum value.
    """
    return frozenset(re.findall(r'"(\w+)"\s*=>\s*ViolationKind::', source))


def _wire(
    result: TestResult,
    node_id: str = "tests/test_foo.py::test_example",
    duration_ms: float = 42.5,
) -> dict:
    """Serialize and re-parse to simulate the JSON round-trip."""
    raw = result.to_wire(node_id, duration_ms)
    return json.loads(json.dumps(raw))


# ── PyO3 field parity (regex-parse bridge.rs) ─────────────────────────────────


def test_test_result_fields_match_rust():
    source = _BRIDGE_RS.read_text()
    rust_fields = _rust_struct_fields(source, "TestResult")
    python_fields = _python_fields(TestResult)
    assert rust_fields == python_fields, (
        "Field mismatch between TestResult (src/bridge.rs) and TestResult"
        " (python/oxitest/_bridge/result.py).\n"
        f"  Only in Rust:   {sorted(rust_fields - python_fields)}\n"
        f"  Only in Python: {sorted(python_fields - rust_fields)}"
    )


def test_collected_item_fields_match_rust():
    source = _BRIDGE_RS.read_text()
    rust_fields = _rust_struct_fields(source, "CollectedItem")
    python_fields = _python_fields(CollectedItem)
    assert rust_fields == python_fields, (
        "Field mismatch between CollectedItem (src/bridge.rs) and CollectedItem"
        " (python/oxitest/_bridge/result.py).\n"
        f"  Only in Rust:   {sorted(rust_fields - python_fields)}\n"
        f"  Only in Python: {sorted(python_fields - rust_fields)}"
    )


def test_raw_violation_fields_match_rust():
    source = _BRIDGE_RS.read_text()
    rust_fields = _rust_struct_fields(source, "RawViolation")
    python_fields = _python_fields(CollectedViolation)
    assert rust_fields == python_fields, (
        "Field mismatch between RawViolation (src/bridge.rs) and"
        " CollectedViolation (python/oxitest/_bridge/result.py).\n"
        f"  Only in Rust:   {sorted(rust_fields - python_fields)}\n"
        f"  Only in Python: {sorted(python_fields - rust_fields)}"
    )


# ── PyO3 manual construction (catch TypeError on rename) ─────────────────────


def test_test_result_manual_construction():
    """Constructing TestResult with all fields catches renames at import time."""
    result = TestResult(
        status=StatusKind.PASSED,
        message="",
        file="",
        lineno=0,
        source_line="",
        no_message_lines=(),
        left="",
        right="",
        op="",
        strict=True,
        exc_type="",
        frames=(),
        field_diffs=(),
    )
    expected_fields = {
        "status",
        "message",
        "file",
        "lineno",
        "source_line",
        "no_message_lines",
        "left",
        "right",
        "op",
        "strict",
        "exc_type",
        "frames",
        "field_diffs",
    }
    actual_fields = {f.name for f in dataclasses.fields(result)}
    assert actual_fields == expected_fields, (
        f"TestResult fields differ from Rust TestResult.\n"
        f"  Missing from Python: {expected_fields - actual_fields}\n"
        f"  Extra in Python:     {actual_fields - expected_fields}\n"
        f"  Update src/bridge.rs TestResult to match, or update result.py."
    )


def test_collected_item_manual_construction():
    """Constructing CollectedItem with all fields catches renames at import time."""
    item = CollectedItem(
        fn_name="test_foo",
        lineno=1,
        markers=(),
        param_id=None,
        param_values=(),
        is_async=False,
    )
    expected_fields = {
        "fn_name",
        "lineno",
        "markers",
        "param_id",
        "param_values",
        "is_async",
        "fixture_names",
        "fixref_names",
    }
    actual_fields = {f.name for f in dataclasses.fields(item)}
    assert actual_fields == expected_fields, (
        f"CollectedItem fields differ from Rust CollectedItem.\n"
        f"  Missing from Python: {expected_fields - actual_fields}\n"
        f"  Extra in Python:     {actual_fields - expected_fields}\n"
        f"  Update src/bridge.rs CollectedItem to match, or update result.py."
    )


# ── PyO3 enum parity ─────────────────────────────────────────────────────────


def test_violation_kind_variants_match_rust():
    """Every Python ViolationKind value has a Rust match arm (not Unknown)."""
    source = _BRIDGE_RS.read_text()
    rust_values = _rust_violation_kind_values(source)
    python_values = frozenset(v.value for v in ViolationKind)
    assert rust_values == python_values, (
        "ViolationKind variant mismatch between bridge.rs and result.py.\n"
        f"  Only in Rust:   {sorted(rust_values - python_values)}\n"
        f"  Only in Python: {sorted(python_values - rust_values)}"
    )


# ── Wire shape (to_wire round-trip) ──────────────────────────────────────────


def test_required_fields_passed_has_required_fields():
    wire = _wire(TestResult(status=StatusKind.PASSED, strict=False))
    assert "node_id" in wire, "node_id must be present"
    assert "outcome" in wire, "outcome must be present"
    assert "duration_ms" in wire, "duration_ms must be present"
    assert "protocol_version" in wire, "protocol_version must be present"
    assert wire["node_id"] == "tests/test_foo.py::test_example", "wrong node_id"
    assert wire["duration_ms"] == 42.5, "wrong duration_ms"


def test_required_fields_failed_has_required_fields():
    wire = _wire(TestResult(status=StatusKind.FAILED, message="boom"))
    assert "node_id" in wire, "node_id must be present"
    assert "outcome" in wire, "outcome must be present"
    assert "duration_ms" in wire, "duration_ms must be present"
    assert "protocol_version" in wire, "protocol_version must be present"
    assert wire["node_id"] == "tests/test_foo.py::test_example", "wrong node_id"
    assert wire["duration_ms"] == 42.5, "wrong duration_ms"


def test_compact_passed_omits_all_optional_fields():
    wire = _wire(TestResult(status=StatusKind.PASSED, strict=False))
    optional_keys = {
        "failure_repr",
        "message",
        "file",
        "lineno",
        "source_line",
        "no_message_lines",
        "left",
        "right",
        "op",
        "strict",
        "frames",
    }
    present = optional_keys & wire.keys()
    assert not present, f"optional fields present: {present}"


def test_compact_strict_true_is_included():
    wire = _wire(TestResult(status=StatusKind.XPASSED, strict=True))
    assert "strict" in wire, "strict=True must be present"
    assert wire["strict"] is True, "strict must be True"


def test_failed_shape_includes_diagnostic_fields():
    result = TestResult(
        status=StatusKind.FAILED,
        message="AssertionError: values differ",
        file="tests/test_foo.py",
        lineno=12,
        source_line="assert x == y",
        left="1",
        right="2",
        op="==",
        frames=(
            Frame(
                file="tests/test_foo.py",
                lineno=12,
                name="test_example",
                line="assert x == y",
            ),
        ),
    )
    wire = _wire(result)
    assert wire["message"] == "AssertionError: values differ", "message must round-trip"
    assert wire["file"] == "tests/test_foo.py", "file must round-trip"
    assert wire["lineno"] == 12, "lineno must round-trip"
    assert wire["source_line"] == "assert x == y", "source_line must round-trip"
    assert wire["left"] == "1", "left must round-trip"
    assert wire["right"] == "2", "right must round-trip"
    assert wire["op"] == "==", "op must round-trip"
    assert "failure_repr" in wire, "failure_repr must be present"
    assert "frames" in wire, "frames must be present"


def test_failed_shape_error_includes_message_and_frames():
    result = TestResult(
        status=StatusKind.ERROR,
        message="ImportError: no module named foo",
        frames=(
            Frame(
                file="tests/test_foo.py", lineno=1, name="<module>", line="import foo"
            ),
        ),
    )
    wire = _wire(result)
    assert wire["outcome"] == "error", "wrong outcome"
    assert "message" in wire, "message must be present"
    assert wire["message"] == "ImportError: no module named foo", (
        "message must round-trip"
    )
    assert "frames" in wire, "frames must be present"


@dataclass(frozen=True)
class StatusCase:
    """Parameters for a single status round-trip check."""

    status: str
    expected: str
    message: str = ""
    strict: bool = True


@oxi.parametrize(
    passed=StatusCase(status=StatusKind.PASSED, expected="passed", strict=False),
    failed=StatusCase(status=StatusKind.FAILED, expected="failed", message="oops"),
    error=StatusCase(status=StatusKind.ERROR, expected="error", message="err"),
    skipped=StatusCase(
        status=StatusKind.SKIPPED,
        expected="skipped",
        message="reason",
        strict=False,
    ),
    xfailed=StatusCase(
        status=StatusKind.XFAILED,
        expected="xfailed",
        message="expected",
        strict=False,
    ),
    xpassed=StatusCase(status=StatusKind.XPASSED, expected="xpassed", strict=False),
    warned=StatusCase(
        status=StatusKind.WARNED,
        expected="warned",
        message="DeprecationWarning",
        strict=False,
    ),
    timeout=StatusCase(
        status=StatusKind.TIMEOUT,
        expected="timeout",
        message="timed out",
        strict=False,
    ),
)
def test_status_round_trip(status, expected, message, strict):
    """Each StatusKind maps to the correct outcome string in the wire payload."""
    wire = _wire(TestResult(status=status, message=message, strict=strict))
    got = wire["outcome"]
    assert got == expected, f"expected {expected!r}, got {got!r}"


# ── Frame serialization ──────────────────────────────────────────────────────


def test_frame_keys():
    result = TestResult(
        status=StatusKind.FAILED,
        message="err",
        frames=(
            Frame(file="src/foo.py", lineno=5, name="test_bar", line="assert val"),
        ),
    )
    wire = _wire(result)
    assert "frames" in wire, "frames must be present"
    frame = wire["frames"][0]
    expected = {"file", "lineno", "name", "line", "locals"}
    assert set(frame.keys()) == expected, f"wrong frame keys: {set(frame.keys())}"


def test_frame_multiple_frames_preserved():
    result = TestResult(
        status=StatusKind.FAILED,
        message="err",
        frames=(
            Frame(file="src/a.py", lineno=1, name="helper", line="raise ValueError"),
            Frame(file="tests/test_a.py", lineno=9, name="test_thing", line="helper()"),
        ),
    )
    wire = _wire(result)
    assert "frames" in wire, "frames must be present"
    assert len(wire["frames"]) == 2, "both frames needed"
    assert wire["frames"][0]["file"] == "src/a.py", "frame[0] file"
    assert wire["frames"][1]["file"] == "tests/test_a.py", "frame[1] file"


# ── Cross-language constants ─────────────────────────────────────────────────


def test_protocol_version_always_present():
    result = TestResult(status=StatusKind.PASSED)
    wire = _wire(result, "t.py::test_a", 1.0)
    assert "protocol_version" in wire, "protocol_version must always be in wire output"
    assert wire["protocol_version"] == PROTOCOL_VERSION, (
        f"expected {PROTOCOL_VERSION}, got {wire['protocol_version']}"
    )


def test_protocol_version_matches_rust_constant():
    """Python PROTOCOL_VERSION must equal Rust PROTOCOL_VERSION."""
    source = _WORKER_RESULT_RS.read_text()
    match = re.search(r"PROTOCOL_VERSION:\s*u32\s*=\s*(\d+)", source)
    assert match, "PROTOCOL_VERSION not found in src/worker_result.rs"
    rust_version = int(match.group(1))
    assert rust_version == PROTOCOL_VERSION, (
        f"Python PROTOCOL_VERSION={PROTOCOL_VERSION} != "
        f"Rust PROTOCOL_VERSION={rust_version}"
    )


# ── Fixture timing shape ─────────────────────────────────────────────────────


def test_get_fixture_timings_returns_expected_shape():
    """get_fixture_timings() returns list of dicts with required keys."""
    from oxitest._bridge._fixture_registry import FixtureRegistry
    from oxitest._bridge._fixture_session import FixtureSession

    session = FixtureSession(FixtureRegistry())
    timings = session.get_fixture_timings()
    assert isinstance(timings, list), "timings must be a list"
    assert timings == [], "empty session should produce empty timings"


def test_get_fixture_timings_entry_has_required_keys():
    """Each timing entry has the 5 required keys with correct types."""
    from conftest import helpers

    session = helpers.common.make_session_with("timed_fx", lambda: 1)
    session.get_fixture("timed_fx", "mod.py", [])
    timings = session.get_fixture_timings()

    assert len(timings) == 1, "expected exactly one timing entry"
    entry = timings[0]
    required_keys = {
        "name",
        "total_setup_ms",
        "setup_count",
        "total_teardown_ms",
        "teardown_count",
    }
    assert set(entry.keys()) == required_keys, f"wrong keys: {set(entry.keys())}"
    assert isinstance(entry["name"], str), "name must be str"
    assert isinstance(entry["total_setup_ms"], float), "total_setup_ms must be float"
    assert isinstance(entry["setup_count"], int), "setup_count must be int"
    assert isinstance(entry["total_teardown_ms"], float), (
        "total_teardown_ms must be float"
    )
    assert isinstance(entry["teardown_count"], int), "teardown_count must be int"


# ── FixtureSession bridge contract ────────────────────────────────────────────


def test_fixture_session_has_bridge_methods():
    """FixtureSession exposes the methods called by the Rust bridge."""
    from oxitest._bridge._fixture_registry import FixtureRegistry
    from oxitest._bridge._fixture_session import FixtureSession

    session = FixtureSession(FixtureRegistry())
    bridge_methods = {
        "end_module",
        "end_session",
        "get_fixture",
        "resolve_for_test",
        "has_shared_fixtures",
        "shared_fixture_names",
        "validate_fixture_names",
        "find_unused_fixtures",
    }
    for method in bridge_methods:
        assert hasattr(session, method), (
            f"FixtureSession missing bridge method: {method!r}"
        )


def test_fixture_session_end_module_does_not_raise():
    """end_module accepts a module path without raising."""
    from oxitest._bridge._fixture_registry import FixtureRegistry
    from oxitest._bridge._fixture_session import FixtureSession

    session = FixtureSession(FixtureRegistry())
    session.end_module("mod.py")
