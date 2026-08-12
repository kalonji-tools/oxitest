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
from oxitest._bridge._debugger import _NULL_DEBUGGER
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._runners import NO_DEBUG, DebugContext, DebugMode
from oxitest._bridge._test_kind import Solitary
from oxitest._bridge.result import (
    PROTOCOL_VERSION,
    CollectedItem,
    ErrorResult,
    FailedResult,
    FixtureTiming,
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
from tests import helpers

# ── Paths to Rust source files ────────────────────────────────────────────────

_SRC_DIR = pathlib.Path(__file__).parent.parent.parent / "src"
_BRIDGE_RS = _SRC_DIR / "bridge.rs"
_BRIDGE_RS_FILES = [_SRC_DIR / "bridge.rs", _SRC_DIR / "reporter" / "bridge.rs"]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _rust_violation_kind_values(source: str) -> frozenset[str]:
    """Extract string literals from the ViolationKind match arms in bridge.rs.

    The ``_ => Unknown`` catch-all arm has no string literal and is excluded
    by design — ``Unknown`` has no corresponding Python enum value.
    """
    return frozenset(re.findall(r'"(\w+)"\s*=>\s*ViolationKind::', source))


def _rust_bridge_method_calls(source: str) -> frozenset[str]:
    r"""Extract FixtureSession method names called via PyO3 call_method in bridge files.

    Matches ``call_method0/1`` calls on the Python-side FixtureSession object,
    which appears in Rust as either::

        self.0.bind(py).call_method0("name", ...)   # inside impl FixtureSession
        session.0.bind(py).call_method1("name", ...)  # standalone fn, &FixtureSession
        session_obj.call_method0("name", ...)  # via session.as_py_object(py)
        obj.call_method0("name", ...)  # via session.as_py_object(py), local name "obj"

    Uses ``re.VERBOSE`` (``\\s`` includes newlines) to handle Rust's chained-method
    style where each ``.method()`` is on its own indented line.
    """
    pattern = re.compile(
        r"""
        (?:
            # self.0.bind(py) or session.0.bind(py) — the raw PyObject field
            (?:self|session) \s*\.\s* 0 \s*\.\s* bind \s*\(\s* py \s*\)
            |
            # session_obj or obj — already bound via session.as_py_object(py)
            (?:session_obj|obj)
        )
        \s*\.\s*            # chained dot (newlines allowed by \s)
        call_method \d?     # call_method0, call_method1, or call_method
        \s*\(\s*            # opening paren
        " ([a-z_][a-z0-9_]*) "  # the Python method name as a string literal
        """,
        re.VERBOSE,
    )
    return frozenset(pattern.findall(source))


def _wire(
    result: TestResult,
    node_id: str = "tests/test_foo.py::test_example",
    duration_ms: float = 42.5,
) -> dict:
    """Serialize and re-parse to simulate the JSON round-trip."""
    raw = result.to_wire(node_id, duration_ms)
    return json.loads(json.dumps(raw))


# ── PyO3 tuple contracts ─────────────────────────────────────────────────────


def test_local_var_tuple_contract() -> None:
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


def test_field_diff_tuple_contract() -> None:
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


def test_failed_result_manual_construction() -> None:
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


def test_collected_item_manual_construction() -> None:
    """Constructing CollectedItem with all fields catches renames at import time."""
    item = CollectedItem(
        fn_name="test_foo",
        lineno=1,
        markers=(),
        kind=Solitary(),
        param_values=(),
        is_async=False,
    )
    expected_fields = {
        "fn_name",
        "lineno",
        "markers",
        "kind",
        "param_values",
        "is_async",
        "fixture_deps",
        "fixref_deps",
        "arranged",
    }
    actual_fields = {f.name for f in dataclasses.fields(item)}
    assert actual_fields == expected_fields, (
        f"CollectedItem fields differ from Rust CollectedItem.\n"
        f"  Missing from Python: {expected_fields - actual_fields}\n"
        f"  Extra in Python:     {actual_fields - expected_fields}\n"
        f"  Update src/bridge.rs CollectedItem to match, or update result.py."
    )


# ── PyO3 enum parity ─────────────────────────────────────────────────────────


def test_violation_kind_variants_match_rust() -> None:
    """Every Python ViolationKind value has a Rust match arm (not Unknown)."""
    source = _BRIDGE_RS.read_text(encoding="utf-8")
    rust_values = _rust_violation_kind_values(source)
    python_values = frozenset(v.value for v in ViolationKind)
    assert rust_values == python_values, (
        "ViolationKind variant mismatch between bridge.rs and result.py.\n"
        f"  Only in Rust:   {sorted(rust_values - python_values)}\n"
        f"  Only in Python: {sorted(python_values - rust_values)}"
    )


# ── Wire shape (to_wire round-trip) ──────────────────────────────────────────


def test_required_fields_passed_has_required_fields() -> None:
    """PassedResult wire payload includes node_id, outcome, duration_ms, and version."""
    wire = _wire(PassedResult())
    assert "node_id" in wire, (
        "wire payload must include node_id so the Rust coordinator can route results to"
        " the correct test node"
    )
    assert "outcome" in wire, (
        "wire payload must include outcome so the Rust reporter can classify the result"
        " without re-parsing"
    )
    assert "duration_ms" in wire, (
        "wire payload must include duration_ms so the Rust scheduler can make"
        " timing-based parallelism decisions"
    )
    assert "protocol_version" in wire, (
        "wire payload must include protocol_version so the Rust coordinator can detect"
        " version drift between Python and Rust"
    )
    assert wire["node_id"] == "tests/test_foo.py::test_example", (
        "node_id must round-trip unchanged -- Rust uses the exact string as a lookup"
        " key for test-node routing"
    )
    assert wire["duration_ms"] == 42.5, (
        "duration_ms must round-trip unchanged -- Rust uses the exact float for"
        " scheduling and cache decisions"
    )


def test_required_fields_failed_has_required_fields() -> None:
    """FailedResult wire payload has node_id, outcome, duration_ms, protocol_version."""
    wire = _wire(FailedResult(message="boom"))
    assert "node_id" in wire, (
        "wire payload must include node_id so the Rust coordinator can route results to"
        " the correct test node"
    )
    assert "outcome" in wire, (
        "wire payload must include outcome so the Rust reporter can classify the result"
        " without re-parsing"
    )
    assert "duration_ms" in wire, (
        "wire payload must include duration_ms so the Rust scheduler can make"
        " timing-based parallelism decisions"
    )
    assert "protocol_version" in wire, (
        "wire payload must include protocol_version so the Rust coordinator can detect"
        " version drift between Python and Rust"
    )
    assert wire["node_id"] == "tests/test_foo.py::test_example", (
        "node_id must round-trip unchanged -- Rust uses the exact string as a lookup"
        " key for test-node routing"
    )
    assert wire["duration_ms"] == 42.5, (
        "duration_ms must round-trip unchanged -- Rust uses the exact float for"
        " scheduling and cache decisions"
    )


def test_compact_passed_omits_all_optional_fields() -> None:
    """PassedResult omits diagnostic fields from wire payload (keeps output compact)."""
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


def test_compact_strict_true_is_included() -> None:
    """XPassedResult strict=True includes strict so Rust enforces xpass-as-failure."""
    wire = _wire(XPassedResult(strict=True))
    assert "strict" in wire, (
        "strict=True must appear in wire so Rust enforces xpass-as-failure semantics"
    )
    assert wire["strict"] is True, (
        "strict must be exactly True (not truthy) -- Rust deserializes a bool and"
        " treats xpass as failure only when strict is true"
    )


def test_failed_shape_includes_diagnostic_fields() -> None:
    """FailedResult with all diagnostic fields round-trips via wire format."""
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
    assert wire["message"] == "AssertionError: values differ", (
        "message must round-trip unchanged -- Rust displays it verbatim in reporter"
        " output"
    )
    assert wire["file"] == "tests/test_foo.py", (
        "file must round-trip unchanged -- Rust uses it to build source-location links"
        " in the reporter"
    )
    assert wire["lineno"] == 12, (
        "lineno must round-trip unchanged -- Rust pairs it with file to pinpoint the"
        " failure location"
    )
    assert wire["source_line"] == "assert x == y", (
        "source_line must round-trip unchanged -- Rust displays it inline in failure"
        " reports for immediate context"
    )
    assert wire["left"] == "1", (
        "left must round-trip unchanged -- Rust renders the left operand in diff-style"
        " failure output"
    )
    assert wire["right"] == "2", (
        "right must round-trip unchanged -- Rust renders the right operand in"
        " diff-style failure output"
    )
    assert wire["op"] == "==", (
        "op must round-trip unchanged -- Rust uses the operator string to format"
        " comparison diagnostics"
    )
    assert "frames" in wire, (
        "frames must be present so Rust can render the full traceback in failure"
        " reports"
    )


def test_failed_shape_error_includes_message_and_frames() -> None:
    """ErrorResult wire payload includes message and frames for traceback display."""
    result = ErrorResult(
        message="ImportError: no module named foo",
        frames=(
            Frame(
                file="tests/test_foo.py", lineno=1, name="<module>", line="import foo"
            ),
        ),
    )
    wire = _wire(result)
    assert wire["outcome"] == "error", (
        "ErrorResult must serialize as outcome='error' so Rust distinguishes test"
        " errors from test failures in reporting"
    )
    assert "message" in wire, (
        "wire payload must include message so Rust can display the error description"
        " without re-importing the exception"
    )
    assert wire["message"] == "ImportError: no module named foo", (
        "message must round-trip unchanged -- Rust displays the error string verbatim"
        " in reporter output"
    )
    assert "frames" in wire, (
        "frames must be present so Rust can render the full traceback for import and"
        " setup errors"
    )


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
def test_status_round_trip(status: str, expected: str) -> None:
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


def test_frame_keys() -> None:
    """Frame wire serialization has exactly the keys that Rust's RawFrame expects."""
    result = FailedResult(
        message="err",
        frames=(
            Frame(file="src/foo.py", lineno=5, name="test_bar", line="assert val"),
        ),
    )
    wire = _wire(result)
    assert "frames" in wire, (
        "frames must be present so Rust can render the traceback in failure reports"
    )
    frame = wire["frames"][0]
    expected = {"file", "lineno", "name", "line", "locals"}
    assert set(frame.keys()) == expected, (
        f"frame keys must exactly match Rust RawFrame fields -- extra or missing keys"
        f" cause serde deserialization failures; got {set(frame.keys())}"
    )


def test_frame_multiple_frames_preserved() -> None:
    """All frames in a result are preserved in order through the wire serialization."""
    result = FailedResult(
        message="err",
        frames=(
            Frame(file="src/a.py", lineno=1, name="helper", line="raise ValueError"),
            Frame(file="tests/test_a.py", lineno=9, name="test_thing", line="helper()"),
        ),
    )
    wire = _wire(result)
    assert "frames" in wire, (
        "frames must be present so Rust can render the full call chain in failure"
        " reports"
    )
    assert len(wire["frames"]) == 2, (
        "all frames must survive wire serialization -- Rust needs the complete call"
        " chain to render accurate tracebacks"
    )
    assert wire["frames"][0]["file"] == "src/a.py", (
        "frame ordering must be preserved -- Rust renders frames top-to-bottom and"
        " wrong order produces misleading tracebacks"
    )
    assert wire["frames"][1]["file"] == "tests/test_a.py", (
        "frame ordering must be preserved -- Rust renders frames top-to-bottom and"
        " wrong order produces misleading tracebacks"
    )


# ── Cross-language constants ─────────────────────────────────────────────────


def test_protocol_version_always_present() -> None:
    """Every wire payload carries protocol_version so the coordinator spots drift."""
    result = PassedResult()
    wire = _wire(result, "t.py::test_a", 1.0)
    assert "protocol_version" in wire, (
        "every wire payload must carry protocol_version so the Rust coordinator can"
        " reject incompatible workers"
    )
    assert wire["protocol_version"] == PROTOCOL_VERSION, (
        f"expected {PROTOCOL_VERSION}, got {wire['protocol_version']}"
    )


# ── Fixture timing shape ─────────────────────────────────────────────────────


def test_get_fixture_timings_returns_expected_shape() -> None:
    """get_fixture_timings() returns list of FixtureTiming dataclasses."""
    session = FixtureSession(FixtureRegistry())
    timings = session.get_fixture_timings()
    assert isinstance(timings, tuple), (
        "get_fixture_timings must return a tuple (immutable) per ADR-0005"
        " immutable-by-default return contract"
    )
    assert timings == (), (
        "empty session must produce empty timings -- Rust iterates the collection and a"
        " non-empty result would indicate phantom fixture activity"
    )


def test_get_fixture_timings_entry_has_required_attrs() -> None:
    """Each timing entry has the 5 required attributes with correct types."""
    session = helpers.make_session_with("timed_fx", lambda: 1)
    session.get_fixture_by_name("timed_fx", "mod.py", [])
    timings = session.get_fixture_timings()

    assert len(timings) == 1, (
        "a single fixture invocation must produce exactly one timing entry -- Rust"
        " aggregates per-fixture timing for cache and scheduling"
    )
    entry = timings[0]
    assert isinstance(entry, FixtureTiming), (
        f"timing entries must be FixtureTiming dataclasses so PyO3 FromPyObject can"
        f" extract fields by name -- got {type(entry)}"
    )
    assert isinstance(entry.name, str), (
        "name must be str -- Rust uses it as the fixture identifier key in timing"
        " aggregation"
    )
    assert isinstance(entry.total_setup_ms, float), (
        "total_setup_ms must be float -- Rust performs arithmetic on this value for"
        " scheduling cost estimates"
    )
    assert isinstance(entry.setup_count, int), (
        "setup_count must be int -- Rust divides total_setup_ms by this to compute"
        " per-invocation averages"
    )
    assert isinstance(entry.total_teardown_ms, float), (
        "total_teardown_ms must be float -- Rust performs arithmetic on this value for"
        " scheduling cost estimates"
    )
    assert isinstance(entry.teardown_count, int), (
        "teardown_count must be int -- Rust divides total_teardown_ms by this to"
        " compute per-invocation averages"
    )


# ── FixtureSession bridge contract ────────────────────────────────────────────


def test_fixture_session_has_bridge_methods() -> None:
    """FixtureSession exposes the methods invoked from bridge source files via PyO3.

    A missing method here causes a runtime PyO3 error (call_method finds no
    matching Python method). Python-callers of FixtureSession are protected
    by static type checking (ty) and don't need runtime hasattr coverage.

    The set of required methods is scraped from src/bridge.rs and
    src/reporter/bridge.rs at test-run time so it stays in sync automatically
    as the Rust side evolves.
    """
    source = "\n".join(f.read_text(encoding="utf-8") for f in _BRIDGE_RS_FILES)
    bridge_methods = _rust_bridge_method_calls(source)
    session = FixtureSession(FixtureRegistry())
    for method in sorted(bridge_methods):
        assert hasattr(session, method), (
            f"Rust bridge invokes {method!r} via PyO3 call_method — "
            f"a missing method causes a runtime PyO3 error"
        )


def test_fixture_session_end_module_does_not_raise() -> None:
    """end_module accepts a module path without raising."""
    session = FixtureSession(FixtureRegistry())
    session.end_module("mod.py")


def test_debug_context_defaults_are_off_and_null() -> None:
    """DebugContext defaults must reify 'no debug' as concrete values, not None.

    Per ADR-0007 Rule 4 option 1: mode=DebugMode.OFF (sentinel enum value),
    backend=_NULL_DEBUGGER (null-object from #1567).
    """
    ctx = DebugContext()
    assert ctx.mode is DebugMode.OFF, (
        "Default DebugContext.mode must be DebugMode.OFF sentinel — not None"
    )
    assert ctx.backend is _NULL_DEBUGGER, (
        "Default DebugContext.backend must be _NULL_DEBUGGER null-object — not None"
    )
    assert ctx.node_id == "", "Default DebugContext.node_id must be empty string"
    assert ctx.file is None, (
        "Default DebugContext.file must be None — Rule 7a no-value default (stdout)"
    )
    assert not ctx.show_locals, "Default DebugContext.show_locals must be False"
    assert not ctx.show_internals, "Default DebugContext.show_internals must be False"


def test_no_debug_matches_default_debug_context() -> None:
    """The module-level NO_DEBUG sentinel must equal a fresh DebugContext()."""
    assert DebugContext() == NO_DEBUG, (
        "NO_DEBUG must remain the canonical default DebugContext instance"
    )


def test_strict_mode_documents_one_section_per_violation_kind() -> None:
    """The strict-mode guide's check count must match ViolationKind.

    Retiring a check without editing the guide leaves readers looking for a
    section that no longer exists, and the heading itself states a count
    (#1720). Nothing else compares the two.
    """
    doc = (
        pathlib.Path(__file__).resolve().parents[2]
        / "docs"
        / "user"
        / "explanation"
        / "strict-mode.md"
    )
    text = doc.read_text(encoding="utf-8")
    sections = [ln for ln in text.splitlines() if ln.startswith("### ")]
    kinds = list(ViolationKind)
    assert len(sections) == len(kinds), (
        f"strict-mode.md documents {len(sections)} checks but ViolationKind has "
        f"{len(kinds)} — a reader sent to a retired check finds nothing, and the "
        f"'The N checks' heading above them states the count outright"
    )
