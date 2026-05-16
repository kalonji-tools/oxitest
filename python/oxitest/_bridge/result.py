from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


@dataclass
class TestResult:
    """Bridge result returned by executor.run_test and consumed by Rust bridge.

    Field names must match the Rust BridgeResult struct in src/bridge.rs.
    """

    status: str
    message: str = ""
    file: str = ""
    lineno: int = 0
    source_line: str = ""
    no_message_lines: list[int] = field(default_factory=list[int])
    left: str = ""
    right: str = ""
    op: str = ""
    strict: bool = True
    exc_type: str = ""


@dataclass
class CollectedItem:
    """Bridge result returned by importer.collect_module and consumed by Rust bridge.

    Field names must match the Rust CollectedItem struct in src/bridge.rs.
    """

    fn_name: str
    lineno: int
    markers: list[str]
    param_id: str | None
    param_values: list[tuple[str, str]]


class ViolationKind(StrEnum):
    """Kind of strict-mode violation.

    StrEnum values are plain strings — PyO3's FromPyObject extracts them
    as String without custom glue because isinstance(ViolationKind.BARE_ASSERT,
    str) is True.
    """

    BARE_ASSERT = "bare_assert"
    DICT_PARAMETRIZE = "dict_parametrize"
    MISSING_MARK_REASON = "missing_mark_reason"


@dataclass
class CollectedViolation:
    """Bridge result for a strict-mode violation detected at collection time.

    Field names must match the Rust RawViolation struct in src/bridge.rs.
    """

    node_id: str
    kind: ViolationKind
    detail: str  # kind-specific payload; empty string when unused
