from __future__ import annotations

__all__ = [
    "Frame",
    "TestResult",
    "CollectedItem",
    "ViolationKind",
    "CollectedViolation",
    "_error_result",
    "PROTOCOL_VERSION",
]

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


@dataclass
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

PROTOCOL_VERSION: int = 1


@dataclass
class TestResult:
    """Bridge result returned by executor.run_test and consumed by Rust bridge.

    Field names must match the Rust TestResult struct in src/bridge.rs.
    """

    status: StatusKind
    message: str = ""
    file: str = ""
    lineno: int = 0
    source_line: str = ""
    no_message_lines: tuple[int, ...] = ()
    left: str = ""
    right: str = ""
    op: str = ""
    strict: bool = True
    exc_type: str = ""
    frames: tuple[Frame, ...] = ()

    @property
    def failure_repr(self) -> str | None:
        """Human-readable failure string, or None for non-failure outcomes."""
        if self.status in _NON_FAILURE_STATUSES:
            return None
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
        """Serialize for the worker JSON protocol.

        Produces a compact dict: only non-falsy optional fields are included.
        The Rust `WorkerResult` uses `#[serde(default)]` on all optional
        fields, so missing keys deserialize correctly.
        """
        # Fields the worker computes (not on TestResult)
        output: dict[str, Any] = {
            "node_id": node_id,
            "outcome": self.status,
            "duration_ms": duration_ms,
            "protocol_version": PROTOCOL_VERSION,
        }
        # Optional fields — omit falsy values for compact JSON
        optional = {
            "failure_repr": self.failure_repr,
            "message": self.message,
            "file": self.file,
            "lineno": self.lineno,
            "source_line": self.source_line,
            "no_message_lines": self.no_message_lines,
            "left": self.left,
            "right": self.right,
            "op": self.op,
            "strict": self.strict,
            "frames": [asdict(f) for f in self.frames] if self.frames else None,
        }
        output.update({k: v for k, v in optional.items() if v})
        return output

    @classmethod
    def passed(cls, *, no_message_lines: tuple[int, ...] | None = None) -> TestResult:
        """Factory for a passing test result."""
        return cls(
            status=StatusKind.PASSED,
            no_message_lines=no_message_lines or (),
        )

    @classmethod
    def warned(
        cls, message: str, *, no_message_lines: tuple[int, ...] | None = None
    ) -> TestResult:
        """Factory for a test that passed with warnings."""
        return cls(
            status=StatusKind.WARNED,
            message=message,
            no_message_lines=no_message_lines or (),
        )

    @classmethod
    def skipped(cls, message: str) -> TestResult:
        """Factory for a skipped test result."""
        return cls(status=StatusKind.SKIPPED, message=message)

    @classmethod
    def xfailed(cls, message: str = "") -> TestResult:
        """Factory for an expected failure result."""
        return cls(status=StatusKind.XFAILED, message=message)

    @classmethod
    def xpassed(cls, *, strict: bool = True) -> TestResult:
        """Factory for an unexpected pass result."""
        return cls(status=StatusKind.XPASSED, strict=strict)

    @classmethod
    def timeout(cls, message: str) -> TestResult:
        """Factory for a timed-out test result."""
        return cls(status=StatusKind.TIMEOUT, message=message)


def _error_result(
    msg: str, file: str = "", lineno: int = 0, source_line: str = ""
) -> TestResult:
    return TestResult(
        status=StatusKind.ERROR,
        message=msg,
        file=file,
        lineno=lineno,
        source_line=source_line,
    )


@dataclass
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
    fixture_names: tuple[str, ...] = ()


class ViolationKind(StrEnum):
    """Kind of strict-mode violation.

    StrEnum values are plain strings — PyO3's FromPyObject extracts them
    as String without custom glue because isinstance(ViolationKind.BARE_ASSERT,
    str) is True.
    """

    BARE_ASSERT = "bare_assert"
    DICT_PARAMETRIZE = "dict_parametrize"
    MISSING_MARK_REASON = "missing_mark_reason"
    SINGLE_CASE_PARAMETRIZE = "single_case_parametrize"


@dataclass
class CollectedViolation:
    """Bridge result for a strict-mode violation detected at collection time.

    Field names must match the Rust RawViolation struct in src/bridge.rs.
    """

    node_id: str
    kind: ViolationKind
    detail: str  # kind-specific payload; empty string when unused
