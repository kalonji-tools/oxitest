"""Contract tests for the Rust-Python bridge.

Verifies that:
- PyO3 FromPyObject struct field names match Python dataclass fields
- ViolationKind enum variants match between Python and Rust
- Wire format (to_wire / WorkerResult) round-trips correctly
- Cross-language constants (PROTOCOL_VERSION) stay in sync

These tests catch drift between result.py and bridge.rs / worker_result/wire.rs
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
    ErrorResult,
    FailedResult,
    Frame,
    PassedResult,
    SkippedResult,
    TestResult,
    TimeoutResult,
    ViolationKind,
    WarnedResult,
    XFailedResult,
    XPassedResult,
)

# ── Paths to Rust source files ────────────────────────────────────────────────

_SRC_DIR = pathlib.Path(__file__).parent.parent.parent / "src"
_BRIDGE_RS = _SRC_DIR / "bridge.rs"
_WORKER_RESULT_RS = _SRC_DIR / "worker_result" / "wire.rs"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _rust_struct_fields(source: str, struct_name: str) -> frozenset[str]:
    """Extract field names from a named Rust struct in source text."""
    pattern = rf"struct\s+{re.escape(struct_name)}\s*\{{([^}}]*)}}"
    match = re.search(pattern, source, re.DOTALL)
    if not match:
        msg = f"struct {struct_name!r} not found in Rust source"
        raise AssertionError(msg)
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


def test_frame_fields_match_rust():
    """RawFrame (wire.rs) fields must match Python Frame dataclass fields.

    Rust deserializes Frame via serde (worker JSON path) and PyO3 FromPyObject
    (bridge path). A field rename on either side causes silent data loss or a
    runtime panic with no compile-time protection.
    """
    source = _WORKER_RESULT_RS.read_text()
    rust_fields = _rust_struct_fields(source, "RawFrame")
    python_fields = _python_fields(Frame)
    assert rust_fields == python_fields, (
        "Field mismatch between RawFrame (src/worker_result/wire.rs) and"
        " Frame (python/oxitest/_bridge/result.py).\n"
        f"  Only in Rust:   {sorted(rust_fields - python_fields)}\n"
        f"  Only in Python: {sorted(python_fields - rust_fields)}"
    )


def test_local_var_tuple_contract():
    """Frame.locals preserves 2-element (name, repr) tuple pairs.

    Rust LocalVar is a 2-element tuple — if Python changes the shape,
    serde deserialization silently drops or misaligns values.
    """
    frame = Frame(
        file="tests/test_foo.py",
        lineno=5,
        name="test_example",
        line="assert x == y",
        locals=(("name", "repr"),),
    )
    assert len(frame.locals) == 1, (
        "Frame.locals must preserve exactly one entry — the tuple container"
        " is being flattened or dropped"
    )
    pair = frame.locals[0]
    assert len(pair) == 2, (
        "each local must be a (name, repr) pair — Rust LocalVar is a 2-tuple"
        " and serde will fail if the shape changes"
    )
    assert pair[0] == "name", (
        "first element is the variable name — positional mismatch breaks"
        " Rust serde deserialization"
    )
    assert pair[1] == "repr", (
        "second element is the repr string — positional mismatch breaks"
        " Rust serde deserialization"
    )


def test_field_diff_tuple_contract():
    """FailedResult.field_diffs serializes as list of 3-element lists.

    Rust FieldDiff is a 3-element tuple (field, left, right). The wire
    format must be [["field", "left", "right"]] — any shape change breaks
    serde deserialization on the coordinator side.
    """
    result = FailedResult(
        message="dataclass mismatch",
        field_diffs=(("age", "25", "30"),),
    )
    wire = result.to_wire("test::id", 1.0)
    assert "field_diffs" in wire, (
        "field_diffs must appear in wire output when non-empty — Rust"
        " coordinator expects this key for structured diff display"
    )
    diffs = wire["field_diffs"]
    assert diffs == [["age", "25", "30"]], (
        "each field_diff must serialize as a 3-element list [field, left,"
        " right] — Rust FieldDiff deserializes positionally via serde"
    )


# ── PyO3 manual construction (catch TypeError on rename) ─────────────────────


def test_failed_result_manual_construction():
    """Constructing FailedResult with all fields catches renames at import time."""
    result = FailedResult(
        message="",
        file="",
        lineno=0,
        source_line="",
        no_message_lines=(),
        left="",
        right="",
        op="",
        exc_type="",
        frames=(),
        field_diffs=(),
    )
    expected_fields = {
        "message",
        "file",
        "lineno",
        "source_line",
        "no_message_lines",
        "left",
        "right",
        "op",
        "exc_type",
        "frames",
        "field_diffs",
    }
    actual_fields = {f.name for f in dataclasses.fields(result)}
    assert actual_fields == expected_fields, (
        f"FailedResult fields mismatch.\n"
        f"  Missing: {expected_fields - actual_fields}\n"
        f"  Extra:   {actual_fields - expected_fields}"
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
        "fixture_deps",
        "fixref_deps",
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
    wire = _wire(PassedResult())
    assert "node_id" in wire, "node_id must be present"
    assert "outcome" in wire, "outcome must be present"
    assert "duration_ms" in wire, "duration_ms must be present"
    assert "protocol_version" in wire, "protocol_version must be present"
    assert wire["node_id"] == "tests/test_foo.py::test_example", "wrong node_id"
    assert wire["duration_ms"] == 42.5, "wrong duration_ms"


def test_required_fields_failed_has_required_fields():
    wire = _wire(FailedResult(message="boom"))
    assert "node_id" in wire, "node_id must be present"
    assert "outcome" in wire, "outcome must be present"
    assert "duration_ms" in wire, "duration_ms must be present"
    assert "protocol_version" in wire, "protocol_version must be present"
    assert wire["node_id"] == "tests/test_foo.py::test_example", "wrong node_id"
    assert wire["duration_ms"] == 42.5, "wrong duration_ms"


def test_compact_passed_omits_all_optional_fields():
    wire = _wire(PassedResult())
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
    wire = _wire(XPassedResult(strict=True))
    assert "strict" in wire, "strict=True must be present"
    assert wire["strict"] is True, "strict must be True"


def test_failed_shape_includes_diagnostic_fields():
    result = FailedResult(
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
    assert "frames" in wire, "frames must be present"


def test_failed_shape_error_includes_message_and_frames():
    result = ErrorResult(
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


@oxi.parametrize(
    passed=StatusCase(status="passed", expected="passed"),
    failed=StatusCase(status="failed", expected="failed"),
    error=StatusCase(status="error", expected="error"),
    skipped=StatusCase(status="skipped", expected="skipped"),
    xfailed=StatusCase(status="xfailed", expected="xfailed"),
    xpassed=StatusCase(status="xpassed", expected="xpassed"),
    warned=StatusCase(status="warned", expected="warned"),
    timeout=StatusCase(status="timeout", expected="timeout"),
)
def test_status_round_trip(status, expected):
    """Each per-outcome type maps to the correct outcome string in the wire payload."""
    _factories: dict[str, TestResult] = {
        "passed": PassedResult(),
        "failed": FailedResult(message="oops"),
        "error": ErrorResult(message="err"),
        "skipped": SkippedResult(message="reason"),
        "xfailed": XFailedResult(message="expected"),
        "xpassed": XPassedResult(strict=False),
        "warned": WarnedResult(message="DeprecationWarning"),
        "timeout": TimeoutResult(message="timed out"),
    }
    result = _factories[status]
    wire = _wire(result)
    got = wire["outcome"]
    assert got == expected, f"expected {expected!r}, got {got!r}"


# ── Frame serialization ──────────────────────────────────────────────────────


def test_frame_keys():
    result = FailedResult(
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
    result = FailedResult(
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
    result = PassedResult()
    wire = _wire(result, "t.py::test_a", 1.0)
    assert "protocol_version" in wire, "protocol_version must always be in wire output"
    assert wire["protocol_version"] == PROTOCOL_VERSION, (
        f"expected {PROTOCOL_VERSION}, got {wire['protocol_version']}"
    )


def test_protocol_version_matches_rust_constant():
    """Python PROTOCOL_VERSION must equal Rust PROTOCOL_VERSION."""
    source = _WORKER_RESULT_RS.read_text()
    match = re.search(r"PROTOCOL_VERSION:\s*u32\s*=\s*(\d+)", source)
    assert match, "PROTOCOL_VERSION not found in src/worker_result/wire.rs"
    rust_version = int(match.group(1))
    assert rust_version == PROTOCOL_VERSION, (
        f"Python PROTOCOL_VERSION={PROTOCOL_VERSION} != "
        f"Rust PROTOCOL_VERSION={rust_version}"
    )


# ── Fixture timing shape ─────────────────────────────────────────────────────


def test_get_fixture_timings_returns_expected_shape():
    """get_fixture_timings() returns list of FixtureTiming dataclasses."""
    from oxitest._bridge._fixture_registry import FixtureRegistry
    from oxitest._bridge._fixture_session import FixtureSession

    session = FixtureSession(FixtureRegistry())
    timings = session.get_fixture_timings()
    assert isinstance(timings, list), "timings must be a list"
    assert timings == [], "empty session should produce empty timings"


def test_get_fixture_timings_entry_has_required_attrs():
    """Each timing entry has the 5 required attributes with correct types."""
    from oxitest import helpers
    from oxitest._bridge.result import FixtureTiming

    session = helpers.common.make_session_with("timed_fx", lambda: 1)
    session.get_fixture("timed_fx", "mod.py", [])
    timings = session.get_fixture_timings()

    assert len(timings) == 1, "expected exactly one timing entry"
    entry = timings[0]
    assert isinstance(entry, FixtureTiming), (
        f"expected FixtureTiming, got {type(entry)}"
    )
    assert isinstance(entry.name, str), "name must be str"
    assert isinstance(entry.total_setup_ms, float), "total_setup_ms must be float"
    assert isinstance(entry.setup_count, int), "setup_count must be int"
    assert isinstance(entry.total_teardown_ms, float), "total_teardown_ms must be float"
    assert isinstance(entry.teardown_count, int), "teardown_count must be int"


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
