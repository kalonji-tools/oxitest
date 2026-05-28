"""Contract tests for TestResult.to_wire() output shape.

Verifies that the JSON produced by Python's to_wire() matches what
Rust's WorkerResult expects: required fields always present, optional
fields omitted when falsy, correct types for all values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import oxitest as oxi
from oxitest._bridge.result import PROTOCOL_VERSION, Frame, StatusKind, TestResult

NODE_ID = "tests/test_foo.py::test_example"
DURATION_MS = 42.5


def _wire(
    result: TestResult, node_id: str = NODE_ID, duration_ms: float = DURATION_MS
) -> dict:
    """Serialize and re-parse to simulate the JSON round-trip."""
    raw = result.to_wire(node_id, duration_ms)
    return json.loads(json.dumps(raw))


# Required wire fields always present regardless of outcome.


def test_required_fields_passed_has_required_fields():
    wire = _wire(TestResult(status=StatusKind.PASSED, strict=False))
    assert "node_id" in wire, "node_id must be present"
    assert "outcome" in wire, "outcome must be present"
    assert "duration_ms" in wire, "duration_ms must be present"
    assert "protocol_version" in wire, "protocol_version must be present"
    assert wire["node_id"] == NODE_ID, "wrong node_id"
    assert wire["duration_ms"] == DURATION_MS, "wrong duration"


def test_required_fields_failed_has_required_fields():
    wire = _wire(TestResult(status=StatusKind.FAILED, message="boom"))
    assert "node_id" in wire, "node_id must be present"
    assert "outcome" in wire, "outcome must be present"
    assert "duration_ms" in wire, "duration_ms must be present"
    assert "protocol_version" in wire, "protocol_version must be present"
    assert wire["node_id"] == NODE_ID, "wrong node_id"
    assert wire["duration_ms"] == DURATION_MS, "wrong duration"


# Optional fields omitted when falsy for compact wire payload.


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


# Failed/error outcomes carry all diagnostic fields.


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
    expected_msg = "AssertionError: values differ"
    assert wire["message"] == expected_msg, "message must round-trip"
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
                file="tests/test_foo.py",
                lineno=1,
                name="<module>",
                line="import foo",
            ),
        ),
    )
    wire = _wire(result)
    assert wire["outcome"] == "error", "wrong outcome"
    assert "message" in wire, "message must be present"
    expected_msg = "ImportError: no module named foo"
    assert wire["message"] == expected_msg, "message must round-trip"
    assert "frames" in wire, "frames must be present"


# Each StatusKind round-trips correctly through wire format.


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
        status=StatusKind.SKIPPED, expected="skipped", message="reason", strict=False
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


# Frame objects serialize to dicts with expected keys.


def test_frame_keys():
    result = TestResult(
        status=StatusKind.FAILED,
        message="err",
        frames=(
            Frame(
                file="src/foo.py",
                lineno=5,
                name="test_bar",
                line="assert val",
            ),
        ),
    )
    wire = _wire(result)
    assert "frames" in wire, "frames must be present"
    frame = wire["frames"][0]
    expected = {"file", "lineno", "name", "line"}
    assert set(frame.keys()) == expected, f"wrong frame keys: {set(frame.keys())}"


def test_frame_multiple_frames_preserved():
    result = TestResult(
        status=StatusKind.FAILED,
        message="err",
        frames=(
            Frame(
                file="src/a.py",
                lineno=1,
                name="helper",
                line="raise ValueError",
            ),
            Frame(
                file="tests/test_a.py",
                lineno=9,
                name="test_thing",
                line="helper()",
            ),
        ),
    )
    wire = _wire(result)
    assert "frames" in wire, "frames must be present"
    assert len(wire["frames"]) == 2, "both frames needed"
    assert wire["frames"][0]["file"] == "src/a.py", "frame[0] file"
    assert wire["frames"][1]["file"] == "tests/test_a.py", "frame[1] file"


def test_protocol_version_always_present():
    result = TestResult(status=StatusKind.PASSED)
    wire = _wire(result, "t.py::test_a", 1.0)
    assert "protocol_version" in wire, "protocol_version must always be in wire output"
    assert wire["protocol_version"] == PROTOCOL_VERSION, (
        f"expected {PROTOCOL_VERSION}, got {wire['protocol_version']}"
    )
