"""Contract tests for TestResult.to_wire() output shape.

Verifies that the JSON produced by Python's to_wire() matches what
Rust's WorkerResult expects: required fields always present, optional
fields omitted when falsy, correct types for all values.
"""

from __future__ import annotations

import json

from oxitest._bridge.result import Frame, StatusKind, TestResult

NODE_ID = "tests/test_foo.py::test_example"
DURATION_MS = 42.5


def _wire(result: TestResult) -> dict:
    """Serialize and re-parse to simulate the JSON round-trip."""
    raw = result.to_wire(NODE_ID, DURATION_MS)
    return json.loads(json.dumps(raw))


class TestRequiredFields:
    """Required wire fields always present regardless of outcome."""

    def test_passed_has_required_fields(self):
        wire = _wire(TestResult(status=StatusKind.PASSED, strict=False))
        assert "node_id" in wire, "node_id must be present"
        assert "outcome" in wire, "outcome must be present"
        assert "duration_ms" in wire, "duration_ms must be present"
        assert wire["node_id"] == NODE_ID, "wrong node_id"
        assert wire["duration_ms"] == DURATION_MS, "wrong duration"

    def test_failed_has_required_fields(self):
        wire = _wire(TestResult(status=StatusKind.FAILED, message="boom"))
        assert "node_id" in wire, "node_id must be present"
        assert "outcome" in wire, "outcome must be present"
        assert "duration_ms" in wire, "duration_ms must be present"
        assert wire["node_id"] == NODE_ID, "wrong node_id"
        assert wire["duration_ms"] == DURATION_MS, "wrong duration"


class TestCompactFormat:
    """Optional fields omitted when falsy for compact wire payload."""

    def test_passed_omits_all_optional_fields(self):
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

    def test_strict_true_is_included(self):
        wire = _wire(TestResult(status=StatusKind.XPASSED, strict=True))
        assert "strict" in wire, "strict=True must be present"
        assert wire["strict"] is True, "strict must be True"


class TestFailedShape:
    """Failed/error outcomes carry all diagnostic fields."""

    def test_failed_includes_diagnostic_fields(self):
        result = TestResult(
            status=StatusKind.FAILED,
            message="AssertionError: values differ",
            file="tests/test_foo.py",
            lineno=12,
            source_line="assert x == y",
            left="1",
            right="2",
            op="==",
            frames=[
                Frame(
                    file="tests/test_foo.py",
                    lineno=12,
                    name="test_example",
                    line="assert x == y",
                ),
            ],
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

    def test_error_includes_message_and_frames(self):
        result = TestResult(
            status=StatusKind.ERROR,
            message="ImportError: no module named foo",
            frames=[
                Frame(
                    file="tests/test_foo.py",
                    lineno=1,
                    name="<module>",
                    line="import foo",
                ),
            ],
        )
        wire = _wire(result)
        assert wire["outcome"] == "error", "wrong outcome"
        assert "message" in wire, "message must be present"
        expected_msg = "ImportError: no module named foo"
        assert wire["message"] == expected_msg, "message must round-trip"
        assert "frames" in wire, "frames must be present"


class TestEveryStatus:
    """Each StatusKind round-trips correctly through wire format."""

    def test_passed(self):
        wire = _wire(TestResult(status=StatusKind.PASSED, strict=False))
        assert wire["outcome"] == "passed", "wrong outcome"

    def test_failed(self):
        wire = _wire(TestResult(status=StatusKind.FAILED, message="oops"))
        assert wire["outcome"] == "failed", "wrong outcome"

    def test_error(self):
        wire = _wire(TestResult(status=StatusKind.ERROR, message="err"))
        assert wire["outcome"] == "error", "wrong outcome"

    def test_skipped(self):
        wire = _wire(
            TestResult(
                status=StatusKind.SKIPPED,
                message="reason",
                strict=False,
            )
        )
        assert wire["outcome"] == "skipped", "wrong outcome"

    def test_xfailed(self):
        wire = _wire(
            TestResult(
                status=StatusKind.XFAILED,
                message="expected",
                strict=False,
            )
        )
        assert wire["outcome"] == "xfailed", "wrong outcome"

    def test_xpassed(self):
        wire = _wire(TestResult(status=StatusKind.XPASSED, strict=False))
        assert wire["outcome"] == "xpassed", "wrong outcome"

    def test_warned(self):
        wire = _wire(
            TestResult(
                status=StatusKind.WARNED,
                message="DeprecationWarning",
                strict=False,
            )
        )
        assert wire["outcome"] == "warned", "wrong outcome"

    def test_timeout(self):
        wire = _wire(
            TestResult(
                status=StatusKind.TIMEOUT,
                message="timed out",
                strict=False,
            )
        )
        assert wire["outcome"] == "timeout", "wrong outcome"


class TestFrameSerialization:
    """Frame objects serialize to dicts with expected keys."""

    def test_frame_keys(self):
        result = TestResult(
            status=StatusKind.FAILED,
            message="err",
            frames=[
                Frame(
                    file="src/foo.py",
                    lineno=5,
                    name="test_bar",
                    line="assert val",
                ),
            ],
        )
        wire = _wire(result)
        assert "frames" in wire, "frames must be present"
        frame = wire["frames"][0]
        expected = {"file", "lineno", "name", "line"}
        assert set(frame.keys()) == expected, f"wrong frame keys: {set(frame.keys())}"

    def test_multiple_frames_preserved(self):
        result = TestResult(
            status=StatusKind.FAILED,
            message="err",
            frames=[
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
            ],
        )
        wire = _wire(result)
        assert "frames" in wire, "frames must be present"
        assert len(wire["frames"]) == 2, "both frames needed"
        assert wire["frames"][0]["file"] == "src/a.py", "frame[0] file"
        assert wire["frames"][1]["file"] == "tests/test_a.py", "frame[1] file"
