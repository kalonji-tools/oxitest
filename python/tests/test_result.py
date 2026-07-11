"""Unit tests for per-outcome result types: failure_repr and to_wire."""

from __future__ import annotations

from oxitest._bridge.result import (
    ErrorResult,
    FailedResult,
    Frame,
    PassedResult,
    SkippedResult,
    TimeoutResult,
    WarnedResult,
    XFailedResult,
    XPassedResult,
)

# ── failure_repr ─────────────────────────────────────────────────────────


def test_failure_repr_not_present_on_non_failure_types() -> None:
    """Non-failure result types do not expose a failure_repr attribute."""
    for r in (
        PassedResult(),
        SkippedResult(),
        WarnedResult(),
        XFailedResult(),
        XPassedResult(),
        TimeoutResult(),
    ):
        assert not hasattr(r, "failure_repr"), (
            f"{type(r).__name__} must not expose failure_repr because only"
            f" failure/error outcomes carry diagnostics — leaking it on non-failure"
            f" types would cause the Rust reporter to emit spurious diagnostic blocks"
        )


def test_failure_repr_includes_message() -> None:
    """failure_repr equals the message when no location or comparison fields are set."""
    r = FailedResult(message="oops")
    assert r.failure_repr == "oops", (
        "when no location or comparison fields are set, the message is the only"
        " diagnostic — failure_repr must surface it verbatim so the Rust reporter still"
        " has something to display"
    )


def test_failure_repr_includes_file_and_lineno() -> None:
    """failure_repr includes 'file:lineno' when both location fields are set."""
    r = FailedResult(file="test.py", lineno=7)
    repr_ = r.failure_repr
    assert repr_ is not None, (
        "FailedResult always produces a failure_repr so the Rust reporter can display"
        " diagnostics"
    )
    assert "test.py:7" in repr_, (
        "file:lineno is essential for editor-clickable location links — without it"
        " developers cannot jump to the failure site"
    )


def test_failure_repr_includes_source_line() -> None:
    """failure_repr includes the source line alongside file:lineno when available."""
    r = FailedResult(file="test.py", lineno=7, source_line="assert x")
    repr_ = r.failure_repr
    assert repr_ is not None, (
        "FailedResult always produces a failure_repr so the Rust reporter can display"
        " diagnostics"
    )
    assert "test.py:7  assert x" in repr_, (
        "including the source line alongside file:lineno lets developers see the"
        " failing statement without opening the file, speeding up triage"
    )


def test_failure_repr_includes_left_right_op() -> None:
    """failure_repr renders 'assert left op right' when comparison fields are set."""
    r = FailedResult(left="1", right="2", op="==")
    repr_ = r.failure_repr
    assert repr_ is not None, (
        "FailedResult always produces a failure_repr so the Rust reporter can display"
        " diagnostics"
    )
    assert "assert 1 == 2" in repr_, (
        "rendering the concrete left/right values with the operator shows developers"
        " what was actually compared, making failures debuggable without re-running the"
        " test"
    )


def test_failure_repr_left_only_without_right() -> None:
    """failure_repr renders 'assert left' when only left is set and right is absent."""
    r = FailedResult(left="False")
    repr_ = r.failure_repr
    assert repr_ is not None, (
        "FailedResult always produces a failure_repr so the Rust reporter can display"
        " diagnostics"
    )
    assert "assert False" in repr_, (
        "when only left is present, rendering 'assert left' still gives the developer"
        " the evaluated value — omitting it would leave the failure unexplained"
    )


def test_failure_repr_all_fields() -> None:
    """failure_repr includes message, location, and comparison when all fields set."""
    r = FailedResult(
        message="AssertionError",
        file="test.py",
        lineno=10,
        source_line="assert x == y",
        left="1",
        right="2",
        op="==",
    )
    repr_ = r.failure_repr
    assert repr_ is not None, (
        "FailedResult always produces a failure_repr so the Rust reporter can display"
        " diagnostics"
    )
    assert "AssertionError" in repr_, (
        "the exception message identifies the error class — without it the developer"
        " cannot distinguish assertion failures from other error types"
    )
    assert "test.py:10  assert x == y" in repr_, (
        "location plus source line gives editor-clickable context so the developer can"
        " jump straight to the failing statement"
    )
    assert "assert 1 == 2" in repr_, (
        "the concrete comparison values reveal the runtime mismatch — this is the"
        " primary debugging signal for assertion failures"
    )


def test_failure_repr_no_fields_falls_back_to_status() -> None:
    """ErrorResult with no fields falls back to a generic 'Test error' failure_repr."""
    r = ErrorResult()
    assert r.failure_repr == "Test error", (
        "when no diagnostic fields are set, a generic fallback ensures the Rust"
        " reporter always has a non-empty string to render — a None or empty repr would"
        " crash the reporter or produce blank output"
    )


# ── to_wire ──────────────────────────────────────────────────────────────


def test_to_wire_passing_test_is_compact() -> None:
    """to_wire for a passing result omits all falsy fields, keeping payload minimal."""
    r = PassedResult()
    wire = r.to_wire("test.py::test_a", 1.5)
    assert wire["node_id"] == "test.py::test_a", (
        "node_id is the primary key the Rust scheduler uses to correlate results back"
        " to collected items — a wrong value would orphan the result"
    )
    assert wire["outcome"] == "passed", (
        "outcome must match the result type so the Rust reporter tallies pass/fail"
        " counts correctly"
    )
    assert wire["duration_ms"] == 1.5, (
        "duration_ms feeds the timing cache for parallel scheduling — an incorrect"
        " value would skew future worker load balancing"
    )
    # Falsy fields omitted
    assert "message" not in wire, (
        "omitting falsy fields keeps the wire payload compact, reducing IPC overhead"
        " for parallel workers"
    )
    assert "file" not in wire, (
        "omitting falsy fields keeps the wire payload compact, reducing IPC overhead"
        " for parallel workers"
    )
    assert "failure_repr" not in wire, (
        "omitting falsy fields keeps the wire payload compact, reducing IPC overhead"
        " for parallel workers"
    )
    assert "frames" not in wire, (
        "omitting falsy fields keeps the wire payload compact, reducing IPC overhead"
        " for parallel workers"
    )


def test_to_wire_includes_non_falsy_fields() -> None:
    """to_wire includes all non-falsy diagnostic fields for a failed result."""
    r = FailedResult(
        message="oops",
        file="test.py",
        lineno=5,
        source_line="assert x",
        left="1",
        right="2",
        op="==",
    )
    wire = r.to_wire("test.py::test_b", 2.0)
    assert wire["message"] == "oops", (
        "the message is the primary user-facing diagnostic — if it is lost or corrupted"
        " in serialization, the Rust reporter displays a misleading failure summary"
    )
    assert wire["file"] == "test.py", (
        "the file path must survive serialization so the Rust reporter can emit"
        " editor-clickable location links"
    )
    assert wire["lineno"] == 5, (
        "the line number must survive serialization so the Rust reporter can pinpoint"
        " the exact failure location"
    )
    assert wire["source_line"] == "assert x", (
        "the source line must survive serialization so the Rust reporter can show the"
        " failing statement inline"
    )
    assert wire["left"] == "1", (
        "the left operand must survive serialization so the Rust reporter can render"
        " the concrete comparison values"
    )
    assert wire["right"] == "2", (
        "the right operand must survive serialization so the Rust reporter can render"
        " the concrete comparison values"
    )
    assert wire["op"] == "==", (
        "the operator must survive serialization so the Rust reporter can reconstruct"
        " the full 'assert left op right' expression"
    )


def test_to_wire_includes_frames() -> None:
    """to_wire serialises Frame objects into dicts under the 'frames' key."""
    r = FailedResult(
        message="err",
        frames=(Frame(file="t.py", lineno=3, name="test_f", line="assert x"),),
    )
    wire = r.to_wire("t.py::test_f", 0.5)
    assert len(wire["frames"]) == 1, (
        "Frame objects must serialize 1:1 into the wire dict — dropping or duplicating"
        " frames would give the Rust reporter an incorrect traceback"
    )
    assert wire["frames"][0] == {
        "file": "t.py",
        "lineno": 3,
        "name": "test_f",
        "line": "assert x",
        "locals": (),
    }, (
        "every Frame field must round-trip exactly through to_wire so the Rust reporter"
        " can reconstruct a faithful traceback for the user"
    )


def test_to_wire_omits_empty_frames() -> None:
    """to_wire omits the 'frames' key when there are no stack frames."""
    r = PassedResult()
    wire = r.to_wire("t.py::test_a", 1.0)
    assert "frames" not in wire, (
        "omitting empty frames keeps the wire payload compact — sending an empty list"
        " would waste IPC bandwidth and force the Rust side to handle a no-op array"
    )


def test_to_wire_includes_strict_when_true() -> None:
    """to_wire includes strict=True in the payload so the Rust side can enforce it."""
    r = XPassedResult(strict=True)
    wire = r.to_wire("t.py::test_a", 1.0)
    assert wire["strict"] is True, (
        "strict=True must propagate to Rust so xpass is treated as a failure per"
        " strict-mode semantics"
    )


def test_to_wire_omits_strict_when_false() -> None:
    """to_wire omits strict when it is False to keep the wire payload compact."""
    r = XPassedResult(strict=False)
    wire = r.to_wire("t.py::test_a", 1.0)
    assert "strict" not in wire, (
        "strict=False is the default — omitting it keeps the wire payload compact and"
        " lets the Rust side use its own default without ambiguity"
    )


def test_to_wire_includes_no_message_lines() -> None:
    """to_wire includes no_message_lines when non-empty so Rust can suppress them."""
    r = PassedResult(no_message_lines=(5, 10))
    wire = r.to_wire("t.py::test_a", 1.0)
    assert wire["no_message_lines"] == (5, 10), (
        "no_message_lines must survive serialization so the Rust reporter knows which"
        " output lines to suppress — losing them would leak internal framework noise"
        " into user-facing output"
    )


def test_to_wire_omits_empty_no_message_lines() -> None:
    """to_wire omits no_message_lines when empty to avoid sending empty tuples."""
    r = PassedResult()
    wire = r.to_wire("t.py::test_a", 1.0)
    assert "no_message_lines" not in wire, (
        "omitting an empty tuple avoids sending a no-op field over the wire — the Rust"
        " side treats absence as 'suppress nothing', so including it would waste IPC"
        " bandwidth"
    )
