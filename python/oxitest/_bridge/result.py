from __future__ import annotations

__all__ = [
    "CacheEntry",
    "CacheStats",
    "ErrorResult",
    "FailedResult",
    "Frame",
    "FixtureTiming",
    "PassedResult",
    "SkippedResult",
    "TestResult",
    "TimeoutResult",
    "WarnedResult",
    "XFailedResult",
    "XPassedResult",
    "_error_result",
    "PROTOCOL_VERSION",
]

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


@dataclass(frozen=True, slots=True)
class Frame:
    """Single traceback frame for structured display."""

    file: str
    lineno: int
    name: str
    line: str
    locals: tuple[tuple[str, str], ...] = ()


class StatusKind(StrEnum):
    """Status of a completed test.

    StrEnum values are plain strings — PyO3's FromPyObject extracts them
    as String without custom glue because isinstance(StatusKind.PASSED,
    str) is True. Wire format (JSON serialization) is unchanged.
    """

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    XFAILED = "xfailed"
    XPASSED = "xpassed"
    TIMEOUT = "timeout"
    WARNED = "warned"


_NON_FAILURE_STATUSES = frozenset(
    {
        StatusKind.PASSED,
        StatusKind.SKIPPED,
        StatusKind.WARNED,
        StatusKind.XFAILED,
        StatusKind.XPASSED,
        StatusKind.TIMEOUT,
    }
)

PROTOCOL_VERSION: int = 2


def _wire_base(outcome: str, node_id: str, duration_ms: float) -> dict[str, Any]:
    """Build the required wire fields shared by all outcome types."""
    return {
        "node_id": node_id,
        "outcome": outcome,
        "duration_ms": duration_ms,
        "protocol_version": PROTOCOL_VERSION,
    }


def _wire_optional(output: dict[str, Any], **kwargs: Any) -> None:
    """Add non-falsy optional fields to the wire dict."""
    output.update({k: v for k, v in kwargs.items() if v})


@dataclass(frozen=True, slots=True)
class PassedResult:
    """Result for a passing test."""

    no_message_lines: tuple[int, ...] = ()

    @property
    def status(self) -> StatusKind:
        return StatusKind.PASSED

    def to_wire(self, node_id: str, duration_ms: float) -> dict[str, Any]:
        output = _wire_base("passed", node_id, duration_ms)
        _wire_optional(output, no_message_lines=self.no_message_lines)
        return output


@dataclass(frozen=True, slots=True)
class FailedResult:
    """Result for a failed assertion."""

    message: str = ""
    file: str = ""
    lineno: int = 0
    source_line: str = ""
    no_message_lines: tuple[int, ...] = ()
    left: str = ""
    right: str = ""
    op: str = ""
    exc_type: str = ""
    frames: tuple[Frame, ...] = ()
    field_diffs: tuple[tuple[str, str, str], ...] = ()

    @property
    def status(self) -> StatusKind:
        return StatusKind.FAILED

    @property
    def failure_repr(self) -> str | None:
        """Human-readable failure string."""
        parts: list[str] = []
        if self.message:
            parts.append(self.message)
        if self.file:
            location = f"{self.file}:{self.lineno}"
            if self.source_line:
                location += f"  {self.source_line}"
            parts.append(location)
        if self.left:
            if self.right and self.op:
                parts.append(f"assert {self.left} {self.op} {self.right}")
            else:
                parts.append(f"assert {self.left}")
        return "\n".join(parts) if parts else f"Test {self.status}"

    def to_wire(self, node_id: str, duration_ms: float) -> dict[str, Any]:
        output = _wire_base("failed", node_id, duration_ms)
        _wire_optional(
            output,
            message=self.message,
            file=self.file,
            lineno=self.lineno,
            source_line=self.source_line,
            no_message_lines=self.no_message_lines,
            left=self.left,
            right=self.right,
            op=self.op,
        )
        if self.frames:
            output["frames"] = [asdict(f) for f in self.frames]
        if self.field_diffs:
            output["field_diffs"] = [list(fd) for fd in self.field_diffs]
        return output


@dataclass(frozen=True, slots=True)
class ErrorResult:
    """Result for an uncaught exception (non-assertion)."""

    message: str = ""
    file: str = ""
    lineno: int = 0
    source_line: str = ""
    exc_type: str = ""
    frames: tuple[Frame, ...] = ()

    @property
    def status(self) -> StatusKind:
        return StatusKind.ERROR

    @property
    def failure_repr(self) -> str | None:
        """Human-readable failure string."""
        parts: list[str] = []
        if self.message:
            parts.append(self.message)
        if self.file:
            location = f"{self.file}:{self.lineno}"
            if self.source_line:
                location += f"  {self.source_line}"
            parts.append(location)
        return "\n".join(parts) if parts else f"Test {self.status}"

    def to_wire(self, node_id: str, duration_ms: float) -> dict[str, Any]:
        output = _wire_base("error", node_id, duration_ms)
        _wire_optional(
            output,
            message=self.message,
            file=self.file,
            lineno=self.lineno,
            source_line=self.source_line,
        )
        if self.frames:
            output["frames"] = [asdict(f) for f in self.frames]
        return output


@dataclass(frozen=True, slots=True)
class SkippedResult:
    """Result for a skipped test."""

    message: str = ""

    @property
    def status(self) -> StatusKind:
        return StatusKind.SKIPPED

    def to_wire(self, node_id: str, duration_ms: float) -> dict[str, Any]:
        output = _wire_base("skipped", node_id, duration_ms)
        _wire_optional(output, message=self.message)
        return output


@dataclass(frozen=True, slots=True)
class WarnedResult:
    """Result for a test that passed with warnings."""

    message: str = ""
    no_message_lines: tuple[int, ...] = ()

    @property
    def status(self) -> StatusKind:
        return StatusKind.WARNED

    def to_wire(self, node_id: str, duration_ms: float) -> dict[str, Any]:
        output = _wire_base("warned", node_id, duration_ms)
        _wire_optional(
            output,
            message=self.message,
            no_message_lines=self.no_message_lines,
        )
        return output


@dataclass(frozen=True, slots=True)
class XFailedResult:
    """Result for an expected failure."""

    message: str = ""

    @property
    def status(self) -> StatusKind:
        return StatusKind.XFAILED

    def to_wire(self, node_id: str, duration_ms: float) -> dict[str, Any]:
        output = _wire_base("xfailed", node_id, duration_ms)
        _wire_optional(output, message=self.message)
        return output


@dataclass(frozen=True, slots=True)
class XPassedResult:
    """Result for an unexpected pass (xfail test that passed)."""

    strict: bool = True

    @property
    def status(self) -> StatusKind:
        return StatusKind.XPASSED

    def to_wire(self, node_id: str, duration_ms: float) -> dict[str, Any]:
        output = _wire_base("xpassed", node_id, duration_ms)
        _wire_optional(output, strict=self.strict)
        return output


@dataclass(frozen=True, slots=True)
class TimeoutResult:
    """Result for a timed-out test."""

    message: str = ""

    @property
    def status(self) -> StatusKind:
        return StatusKind.TIMEOUT

    def to_wire(self, node_id: str, duration_ms: float) -> dict[str, Any]:
        output = _wire_base("timeout", node_id, duration_ms)
        _wire_optional(output, message=self.message)
        return output


TestResult = (
    PassedResult
    | FailedResult
    | ErrorResult
    | SkippedResult
    | WarnedResult
    | XFailedResult
    | XPassedResult
    | TimeoutResult
)


def _error_result(
    msg: str, file: str = "", lineno: int = 0, source_line: str = ""
) -> ErrorResult:
    return ErrorResult(
        message=msg,
        file=file,
        lineno=lineno,
        source_line=source_line,
    )


@dataclass(frozen=True, slots=True)
class CollectedItem:
    """Bridge result returned by importer.collect_module and consumed by Rust bridge.

    Field names must match the Rust CollectedItem struct in src/bridge.rs.
    """

    fn_name: str
    lineno: int
    markers: tuple[str, ...]
    param_id: str | None
    param_values: tuple[tuple[str, str], ...]
    is_async: bool = False
    fixture_deps: tuple[tuple[str, str], ...] = ()  # (qualifier, type_name)
    fixref_deps: tuple[tuple[str, str], ...] = ()  # (qualifier, type_name)


class ViolationKind(StrEnum):
    """Kind of strict-mode violation.

    StrEnum values are plain strings — PyO3's FromPyObject extracts them
    as String without custom glue because isinstance(ViolationKind.BARE_ASSERT,
    str) is True.
    """

    BARE_ASSERT = "bare_assert"
    DICT_PARAMETRIZE = "dict_parametrize"
    INVALID_MODULE_MARK = "invalid_module_mark"
    MISSING_MARK_REASON = "missing_mark_reason"
    MISSING_RETURN_ANNOTATION = "missing_return_annotation"
    REGISTRAR_IN_TEST_MODULE = "registrar_in_test_module"
    SINGLE_CASE_PARAMETRIZE = "single_case_parametrize"
    BROAD_FIXTURE_TYPE = "broad_fixture_type"
    UNUSED_FIXTURE = "unused_fixture"


@dataclass(frozen=True, slots=True)
class CollectedViolation:
    """Bridge result for a strict-mode violation detected at collection time.

    Field names must match the Rust RawViolation struct in src/bridge.rs.
    """

    node_id: str
    kind: ViolationKind
    detail: str  # kind-specific payload; empty string when unused


@dataclass(frozen=True, slots=True)
class CacheEntry:
    name: str
    hits: int
    misses: int


@dataclass(frozen=True, slots=True)
class CacheStats:
    total_hits: int
    total_misses: int
    breakdown: tuple[CacheEntry, ...]


@dataclass(frozen=True, slots=True)
class FixtureTiming:
    name: str
    total_setup_ms: float
    setup_count: int
    total_teardown_ms: float
    teardown_count: int
