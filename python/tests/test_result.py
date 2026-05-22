"""Unit tests for TestResult.failure_repr and TestResult.to_wire."""

from __future__ import annotations

from oxitest._bridge.result import Frame, StatusKind, TestResult

# ── failure_repr ─────────────────────────────────────────────────────────


def test_failure_repr_returns_none_for_passed():
    r = TestResult(status=StatusKind.PASSED)
    assert r.failure_repr is None


def test_failure_repr_returns_none_for_skipped():
    r = TestResult(status=StatusKind.SKIPPED, message="reason")
    assert r.failure_repr is None


def test_failure_repr_returns_none_for_all_non_failure_statuses():
    for status in (
        StatusKind.PASSED,
        StatusKind.SKIPPED,
        StatusKind.WARNED,
        StatusKind.XFAILED,
        StatusKind.XPASSED,
        StatusKind.TIMEOUT,
    ):
        assert TestResult(status=status).failure_repr is None


def test_failure_repr_includes_message():
    r = TestResult(status=StatusKind.FAILED, message="oops")
    assert r.failure_repr == "oops"


def test_failure_repr_includes_file_and_lineno():
    r = TestResult(status=StatusKind.FAILED, file="test.py", lineno=7)
    assert "test.py:7" in r.failure_repr


def test_failure_repr_includes_source_line():
    r = TestResult(
        status=StatusKind.FAILED, file="test.py", lineno=7, source_line="assert x"
    )
    assert "test.py:7  assert x" in r.failure_repr


def test_failure_repr_includes_left_right_op():
    r = TestResult(status=StatusKind.FAILED, left="1", right="2", op="==")
    assert "assert 1 == 2" in r.failure_repr


def test_failure_repr_left_only_without_right():
    r = TestResult(status=StatusKind.FAILED, left="False")
    assert "assert False" in r.failure_repr


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
    assert "AssertionError" in repr_
    assert "test.py:10  assert x == y" in repr_
    assert "assert 1 == 2" in repr_


def test_failure_repr_no_fields_falls_back_to_status():
    r = TestResult(status=StatusKind.ERROR)
    assert r.failure_repr == "Test error"


# ── to_wire ──────────────────────────────────────────────────────────────


def test_to_wire_passing_test_is_compact():
    r = TestResult(status=StatusKind.PASSED)
    wire = r.to_wire("test.py::test_a", 1.5)
    assert wire["node_id"] == "test.py::test_a"
    assert wire["outcome"] == "passed"
    assert wire["duration_ms"] == 1.5
    # Falsy fields omitted
    assert "message" not in wire
    assert "file" not in wire
    assert "failure_repr" not in wire
    assert "frames" not in wire


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
    assert wire["message"] == "oops"
    assert wire["file"] == "test.py"
    assert wire["lineno"] == 5
    assert wire["source_line"] == "assert x"
    assert wire["left"] == "1"
    assert wire["right"] == "2"
    assert wire["op"] == "=="
    assert "failure_repr" in wire


def test_to_wire_includes_frames():
    r = TestResult(
        status=StatusKind.FAILED,
        message="err",
        frames=[Frame(file="t.py", lineno=3, name="test_f", line="assert x")],
    )
    wire = r.to_wire("t.py::test_f", 0.5)
    assert len(wire["frames"]) == 1
    assert wire["frames"][0] == {
        "file": "t.py",
        "lineno": 3,
        "name": "test_f",
        "line": "assert x",
    }


def test_to_wire_omits_empty_frames():
    r = TestResult(status=StatusKind.PASSED)
    wire = r.to_wire("t.py::test_a", 1.0)
    assert "frames" not in wire


def test_to_wire_includes_strict_when_true():
    r = TestResult(status=StatusKind.FAILED, message="x", strict=True)
    wire = r.to_wire("t.py::test_a", 1.0)
    assert wire["strict"] is True


def test_to_wire_omits_strict_when_false():
    r = TestResult(status=StatusKind.PASSED, strict=False)
    wire = r.to_wire("t.py::test_a", 1.0)
    assert "strict" not in wire


def test_to_wire_includes_no_message_lines():
    r = TestResult(status=StatusKind.PASSED, no_message_lines=[5, 10])
    wire = r.to_wire("t.py::test_a", 1.0)
    assert wire["no_message_lines"] == [5, 10]


def test_to_wire_omits_empty_no_message_lines():
    r = TestResult(status=StatusKind.PASSED)
    wire = r.to_wire("t.py::test_a", 1.0)
    assert "no_message_lines" not in wire
