"""Unit tests for TestResult.failure_repr and TestResult.to_wire."""

from __future__ import annotations

from oxitest._bridge.result import Frame, StatusKind, TestResult

# ── failure_repr ─────────────────────────────────────────────────────────


def test_failure_repr_returns_none_for_all_non_failure_statuses():
    for status in (
        StatusKind.PASSED,
        StatusKind.SKIPPED,
        StatusKind.WARNED,
        StatusKind.XFAILED,
        StatusKind.XPASSED,
        StatusKind.TIMEOUT,
    ):
        r = TestResult(status=status)
        assert r.failure_repr is None, f"{status} should be None"


def test_failure_repr_includes_message():
    r = TestResult(status=StatusKind.FAILED, message="oops")
    assert r.failure_repr == "oops", "failure_repr should be the message"


def test_failure_repr_includes_file_and_lineno():
    r = TestResult(status=StatusKind.FAILED, file="test.py", lineno=7)
    repr_ = r.failure_repr
    assert repr_ is not None, "failed result must have failure_repr"
    assert "test.py:7" in repr_, "should contain file:lineno"


def test_failure_repr_includes_source_line():
    r = TestResult(
        status=StatusKind.FAILED, file="test.py", lineno=7, source_line="assert x"
    )
    repr_ = r.failure_repr
    assert repr_ is not None, "failed result must have failure_repr"
    assert "test.py:7  assert x" in repr_, "should contain file:lineno  source_line"


def test_failure_repr_includes_left_right_op():
    r = TestResult(status=StatusKind.FAILED, left="1", right="2", op="==")
    repr_ = r.failure_repr
    assert repr_ is not None, "failed result must have failure_repr"
    assert "assert 1 == 2" in repr_, "should contain assert left op right"


def test_failure_repr_left_only_without_right():
    r = TestResult(status=StatusKind.FAILED, left="False")
    repr_ = r.failure_repr
    assert repr_ is not None, "failed result must have failure_repr"
    assert "assert False" in repr_, "should contain assert left"


def test_failure_repr_all_fields():
    r = TestResult(
        status=StatusKind.FAILED,
        message="AssertionError",
        file="test.py",
        lineno=10,
        source_line="assert x == y",
        left="1",
        right="2",
        op="==",
    )
    repr_ = r.failure_repr
    assert repr_ is not None, "failed result must have failure_repr"
    assert "AssertionError" in repr_, "should contain message"
    assert "test.py:10  assert x == y" in repr_, "should contain location"
    assert "assert 1 == 2" in repr_, "should contain comparison"


def test_failure_repr_no_fields_falls_back_to_status():
    r = TestResult(status=StatusKind.ERROR)
    assert r.failure_repr == "Test error", "empty error should fall back to status"


# ── to_wire ──────────────────────────────────────────────────────────────


def test_to_wire_passing_test_is_compact():
    r = TestResult(status=StatusKind.PASSED)
    wire = r.to_wire("test.py::test_a", 1.5)
    assert wire["node_id"] == "test.py::test_a", "node_id mismatch"
    assert wire["outcome"] == "passed", "outcome mismatch"
    assert wire["duration_ms"] == 1.5, "duration_ms mismatch"
    # Falsy fields omitted
    assert "message" not in wire, "message should be omitted"
    assert "file" not in wire, "file should be omitted"
    assert "failure_repr" not in wire, "failure_repr should be omitted"
    assert "frames" not in wire, "frames should be omitted"


def test_to_wire_includes_non_falsy_fields():
    r = TestResult(
        status=StatusKind.FAILED,
        message="oops",
        file="test.py",
        lineno=5,
        source_line="assert x",
        left="1",
        right="2",
        op="==",
    )
    wire = r.to_wire("test.py::test_b", 2.0)
    assert wire["message"] == "oops", "message mismatch"
    assert wire["file"] == "test.py", "file mismatch"
    assert wire["lineno"] == 5, "lineno mismatch"
    assert wire["source_line"] == "assert x", "source_line mismatch"
    assert wire["left"] == "1", "left mismatch"
    assert wire["right"] == "2", "right mismatch"
    assert wire["op"] == "==", "op mismatch"
    assert "failure_repr" in wire, "failure_repr should be present"


def test_to_wire_includes_frames():
    r = TestResult(
        status=StatusKind.FAILED,
        message="err",
        frames=(Frame(file="t.py", lineno=3, name="test_f", line="assert x"),),
    )
    wire = r.to_wire("t.py::test_f", 0.5)
    assert len(wire["frames"]) == 1, "should have 1 frame"
    assert wire["frames"][0] == {
        "file": "t.py",
        "lineno": 3,
        "name": "test_f",
        "line": "assert x",
        "locals": (),
    }, "frame content mismatch"


def test_to_wire_omits_empty_frames():
    r = TestResult(status=StatusKind.PASSED)
    wire = r.to_wire("t.py::test_a", 1.0)
    assert "frames" not in wire, "empty frames should be omitted"


def test_to_wire_includes_strict_when_true():
    r = TestResult(status=StatusKind.FAILED, message="x", strict=True)
    wire = r.to_wire("t.py::test_a", 1.0)
    assert wire["strict"] is True, "strict=True should be included"


def test_to_wire_omits_strict_when_false():
    r = TestResult(status=StatusKind.PASSED, strict=False)
    wire = r.to_wire("t.py::test_a", 1.0)
    assert "strict" not in wire, "strict=False should be omitted"


def test_to_wire_includes_no_message_lines():
    r = TestResult(status=StatusKind.PASSED, no_message_lines=(5, 10))
    wire = r.to_wire("t.py::test_a", 1.0)
    assert wire["no_message_lines"] == (5, 10), "no_message_lines mismatch"


def test_to_wire_omits_empty_no_message_lines():
    r = TestResult(status=StatusKind.PASSED)
    wire = r.to_wire("t.py::test_a", 1.0)
    assert "no_message_lines" not in wire, "empty no_message_lines should be omitted"
