#!/usr/bin/env python3
"""Check that Rust FromPyObject structs stay in sync with Python dataclasses.

Compares field names in src/bridge.rs against python/oxitest/_bridge/result.py.
Exits 0 if all pairs match, 1 with a diff if any mismatch is found.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import TypedDict

# Repo root is two levels up from scripts/
ROOT = Path(__file__).resolve().parent.parent
RUST_PATH = ROOT / "src" / "bridge.rs"
PYTHON_PATH = ROOT / "python" / "oxitest" / "_bridge" / "result.py"
LOCK_PATH = Path(__file__).resolve().parent / "wire_protocol.lock.json"

# Rust struct name -> Python class name (or list of class names for union types)
#
# Note: TestResult was removed in #1206 — the runtime contract is enforced by
# getattr() calls in extract_outcome(), and the unified conversion logic lives
# in RawOutcome::into_test_outcome().  The wire-format check below still
# validates the JSON worker ↔ Python to_wire() contract.
PAIRS: dict[str, str | list[str]] = {
    "CollectedItem": "CollectedItem",
    "RawViolation": "CollectedViolation",
}


def parse_rust_structs(path: Path) -> dict[str, set[str]]:
    """Extract field names from #[derive(FromPyObject)] structs."""
    text = path.read_text(encoding="utf-8")
    structs: dict[str, set[str]] = {}
    # Match struct blocks preceded by a FromPyObject derive
    pattern = re.compile(
        r"#\[derive\([^)]*FromPyObject[^)]*\)\]\s*"
        r"(?:pub(?:\(crate\))?\s+)?struct\s+(\w+)\s*\{([^}]+)\}",
        re.DOTALL,
    )
    field_pattern = re.compile(r"^\s*(?:pub(?:\(crate\))?\s+)?(\w+)\s*:", re.MULTILINE)
    for m in pattern.finditer(text):
        name = m.group(1)
        body = m.group(2)
        fields = set(field_pattern.findall(body))
        structs[name] = fields
    return structs


def parse_python_classes(path: Path) -> dict[str, set[str]]:
    """Extract field names from @dataclass classes."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    classes: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        is_dataclass = any(
            (isinstance(d, ast.Name) and d.id == "dataclass")
            or (isinstance(d, ast.Attribute) and d.attr == "dataclass")
            or (
                isinstance(d, ast.Call)
                and (
                    (isinstance(d.func, ast.Name) and d.func.id == "dataclass")
                    or (
                        isinstance(d.func, ast.Attribute) and d.func.attr == "dataclass"
                    )
                )
            )
            for d in node.decorator_list
        )
        if not is_dataclass:
            continue
        fields: set[str] = set()
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                fields.add(item.target.id)
        classes[node.name] = fields
    return classes


REPORTER_RUST_PATH = ROOT / "src" / "reporter" / "bridge.rs"

REPORTER_PAIRS = {
    "BridgeCacheEntry": "CacheEntry",
    "BridgeCacheStats": "CacheStats",
    "BridgeFixtureTiming": "FixtureTiming",
}

WIRE_RUST_PATH = ROOT / "src" / "worker_result" / "wire.rs"
WORKER_PY_PATH = ROOT / "python" / "oxitest" / "_bridge" / "worker.py"


def _parse_serde_struct_fields(text: str, struct_name: str) -> set[str]:
    """Extract field names from a named serde struct."""
    pattern = re.compile(
        rf"struct\s+{struct_name}\s*(?:<[^>]+>)?\s*\{{([^}}]+)\}}",
        re.DOTALL,
    )
    field_pattern = re.compile(r"^\s*pub\s+(\w+)\s*:", re.MULTILINE)
    m = pattern.search(text)
    if m is None:
        return set()
    return set(field_pattern.findall(m.group(1)))


def parse_worker_result_fields(path: Path) -> set[str]:
    """Extract field names from the WireResult serde enum (or struct fallback).

    With internally-tagged enums, fields are distributed across variants.
    We collect the union of all variant fields, plus the tag field ("outcome").
    """
    text = path.read_text(encoding="utf-8")
    # Try enum first (internally-tagged enum)
    enum_pattern = re.compile(
        r"enum\s+WireResult\s*\{(.+?)^}",
        re.DOTALL | re.MULTILINE,
    )
    m = enum_pattern.search(text)
    if m:
        body = m.group(1)
        field_pattern = re.compile(r"^\s+(\w+)\s*:", re.MULTILINE)
        fields = set(field_pattern.findall(body))
        # The tag field "outcome" drives variant selection but is not a Rust field
        fields.add("outcome")
        # Remove serde rename attributes that aren't actual field names
        fields.discard("serde")
        return fields
    # Fall back to struct-based extraction
    return _parse_serde_struct_fields(text, "WireResult")


def parse_worker_task_item_fields(path: Path) -> set[str]:
    """Extract field names from the WorkerTaskItem serde struct."""
    return _parse_serde_struct_fields(
        path.read_text(encoding="utf-8"), "WorkerTaskItem"
    )


def parse_worker_item_reads(path: Path) -> set[str]:
    """Extract field names that worker.py reads from task item dicts.

    Matches patterns like item["fn_name"] and item.get("param_id").
    """
    text = path.read_text(encoding="utf-8")
    fields: set[str] = set()
    for m in re.finditer(r'item\["(\w+)"\]', text):
        fields.add(m.group(1))
    for m in re.finditer(r'item\.get\("(\w+)"', text):
        fields.add(m.group(1))
    return fields


def parse_wire_base_fields(path: Path) -> set[str]:
    """Extract the required wire field names from the ``_wire_base`` return dict.

    Read through the AST rather than by a regex over the whole file. ``result.py``
    holds many dict literals, so a regex could only tell ``_wire_base`` apart by
    listing the names it already expected — and a required field outside that list
    was then invisible. Adding a sixth key to ``_wire_base`` produced no mismatch.

    Returns an empty set if ``_wire_base`` is absent or stops returning a literal
    dict, which the caller reports as an error rather than as agreement.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_wire_base"):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Dict):
                keys = {
                    key.value
                    for key in stmt.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
                # "type" is the LDJSON envelope discriminator. It selects the
                # line kind the drain loop dispatches on ("result",
                # "diagnostic", "trace"), so no Rust variant carries it and
                # comparing it would always mismatch.
                return keys - {"type"}
    return set()


def parse_to_wire_fields(path: Path) -> set[str]:
    """Extract wire field names from per-outcome to_wire() methods and helpers.

    Collects:
    - Dict keys from _wire_base (required fields)
    - Keyword argument names from _wire_optional calls (optional fields)
    - Direct output[...] assignments (frames, field_diffs)
    """
    text = path.read_text(encoding="utf-8")
    fields: set[str] = parse_wire_base_fields(path)
    # Optional fields from _wire_optional keyword args
    for m in re.finditer(r"_wire_optional\([^)]+\)", text, re.DOTALL):
        call = m.group(0)
        for kw in re.finditer(r"(\w+)\s*=\s*self\.", call):
            fields.add(kw.group(1))
    # Direct output[...] assignments (e.g., output["frames"] = ...)
    for m in re.finditer(r'output\["(\w+)"\]', text):
        fields.add(m.group(1))
    return fields


def _resolve_py_fields(
    py_name: str | list[str], python: dict[str, set[str]]
) -> tuple[set[str] | None, list[str]]:
    """Resolve Python field set for a single or union py_name.

    Returns (fields, missing_classes). fields is None on error (single class missing).
    """
    if isinstance(py_name, list):
        py_fields: set[str] = {"status"}
        missing: list[str] = []
        for cls_name in py_name:
            cls_fields = python.get(cls_name)
            if cls_fields is None:
                missing.append(cls_name)
            else:
                py_fields |= cls_fields
        return (None if missing else py_fields), missing
    cls_fields = python.get(py_name)
    return cls_fields, []


def _check_main_pairs(rust: dict[str, set[str]], python: dict[str, set[str]]) -> int:
    """Check main bridge pairs (CollectedItem, RawViolation)."""
    errors = 0
    for rust_name, py_name in PAIRS.items():
        rust_fields = rust.get(rust_name)
        if rust_fields is None:
            print(f"ERROR: Rust struct '{rust_name}' not found in {RUST_PATH}")
            errors += 1
            continue
        py_fields, missing_classes = _resolve_py_fields(py_name, python)
        if missing_classes:
            print(f"ERROR: Python classes {missing_classes} not found in {PYTHON_PATH}")
            errors += 1
            continue
        if py_fields is None:
            print(f"ERROR: Python class '{py_name}' not found in {PYTHON_PATH}")
            errors += 1
            continue
        rust_only = rust_fields - py_fields
        py_only = py_fields - rust_fields
        # CollectedItem.param_id (Rust) reads a @property backed by
        # ``kind: TestKind`` on the Python side (#1564). PyO3 FromPyObject
        # resolves via attribute lookup, so the property satisfies the Rust
        # field. If we grow a second such bridge, promote this to a table.
        if rust_name == "CollectedItem" and rust_only == {"param_id"}:
            if "kind" in py_only:
                rust_only = set()
                py_only = py_only - {"kind"}
            else:
                # Naming the absent field matters (#2074). Without this the
                # report is "Rust-only fields: ['param_id']", which is true and
                # sends the reader to the wrong side of the bridge: param_id is
                # not missing, the field backing it is.
                print(
                    f"ERROR: Python class '{py_name}' has no 'kind' field. Rust"
                    " reads 'param_id' through the @property backed by"
                    " 'kind: TestKind' (#1564), so 'kind' is what satisfies the"
                    " Rust field."
                )
                errors += 1
                continue
        label = py_name if isinstance(py_name, str) else "per-outcome union"
        if rust_only or py_only:
            print(f"MISMATCH: {rust_name} (Rust) vs {label} (Python)")
            if rust_only:
                print(f"  Rust-only fields: {sorted(rust_only)}")
            if py_only:
                print(f"  Python-only fields: {sorted(py_only)}")
            errors += 1
    return errors


def _check_reporter_pairs(python: dict[str, set[str]]) -> int:
    """Check reporter bridge pairs."""
    errors = 0
    reporter_rust = parse_rust_structs(REPORTER_RUST_PATH)
    for rust_name, py_name in REPORTER_PAIRS.items():
        rust_fields = reporter_rust.get(rust_name)
        py_fields = python.get(py_name)
        if rust_fields is None:
            print(f"ERROR: Rust struct '{rust_name}' not found in {REPORTER_RUST_PATH}")
            errors += 1
            continue
        if py_fields is None:
            print(f"ERROR: Python class '{py_name}' not found in {PYTHON_PATH}")
            errors += 1
            continue
        rust_only = rust_fields - py_fields
        py_only = py_fields - rust_fields
        if rust_only or py_only:
            print(f"MISMATCH: {rust_name} (Rust) vs {py_name} (Python)")
            if rust_only:
                print(f"  Rust-only fields: {sorted(rust_only)}")
            if py_only:
                print(f"  Python-only fields: {sorted(py_only)}")
            errors += 1
    return errors


def _check_raw_frame(python: dict[str, set[str]]) -> int:
    """Check RawFrame (Rust) vs Frame (Python)."""
    raw_frame_fields = _parse_serde_struct_fields(
        WIRE_RUST_PATH.read_text(encoding="utf-8"), "RawFrame"
    )
    py_frame_fields = python.get("Frame")
    if not raw_frame_fields:
        print(f"ERROR: RawFrame not found in {WIRE_RUST_PATH}")
        return 1
    if py_frame_fields is None:
        print(f"ERROR: Python class 'Frame' not found in {PYTHON_PATH}")
        return 1
    rust_only = raw_frame_fields - py_frame_fields
    py_only = py_frame_fields - raw_frame_fields
    if rust_only or py_only:
        print("MISMATCH: RawFrame (Rust) vs Frame (Python)")
        if rust_only:
            print(f"  Rust-only fields: {sorted(rust_only)}")
        if py_only:
            print(f"  Python-only fields: {sorted(py_only)}")
        return 1
    return 0


def _check_wire_format() -> int:
    """Check WireResult (Rust) vs to_wire() (Python)."""
    rust_wire = parse_worker_result_fields(WIRE_RUST_PATH)
    py_wire = parse_to_wire_fields(PYTHON_PATH)
    if not rust_wire:
        print(f"ERROR: WireResult not found in {WIRE_RUST_PATH}")
        return 1
    if not py_wire:
        print(f"ERROR: to_wire() fields not found in {PYTHON_PATH}")
        return 1
    rust_only = rust_wire - py_wire
    py_only = py_wire - rust_wire
    if rust_only or py_only:
        print("MISMATCH: WireResult (Rust wire) vs to_wire() (Python wire)")
        if rust_only:
            print(f"  Rust-only wire fields: {sorted(rust_only)}")
        if py_only:
            print(f"  Python-only wire fields: {sorted(py_only)}")
        return 1
    return 0


def parse_protocol_version_py(path: Path) -> int | None:
    """Extract PROTOCOL_VERSION from result.py via AST.

    Finds a module-level ``PROTOCOL_VERSION: int = <N>`` annotated assignment.
    Returns None if the constant is missing or does not have an integer literal.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "PROTOCOL_VERSION"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int)
        ):
            return node.value.value
    return None


def parse_protocol_version_rs(path: Path) -> int | None:
    """Extract PROTOCOL_VERSION from wire.rs via regex.

    Matches ``pub(crate) const PROTOCOL_VERSION: u32 = <N>;``. Returns None
    when the constant is missing.
    """
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"pub\(crate\)\s+const\s+PROTOCOL_VERSION\s*:\s*u32\s*=\s*(\d+)\s*;"
    )
    m = pattern.search(text)
    if m is None:
        return None
    return int(m.group(1))


def _check_protocol_version() -> int:
    """Check PROTOCOL_VERSION equality between result.py and wire.rs."""
    py_version = parse_protocol_version_py(PYTHON_PATH)
    rs_version = parse_protocol_version_rs(WIRE_RUST_PATH)
    if py_version is None:
        print(f"ERROR: PROTOCOL_VERSION not found in {PYTHON_PATH}")
        return 1
    if rs_version is None:
        print(f"ERROR: PROTOCOL_VERSION not found in {WIRE_RUST_PATH}")
        return 1
    if py_version != rs_version:
        print("MISMATCH: PROTOCOL_VERSION")
        print(f"    result.py={py_version}")
        print(f"    wire.rs={rs_version}")
        print("    bump both")
        return 1
    return 0


def _check_task_format() -> int:
    """Check WorkerTaskItem (Rust) vs worker.py item reads."""
    rust_task_fields = parse_worker_task_item_fields(WIRE_RUST_PATH)
    py_task_fields = parse_worker_item_reads(WORKER_PY_PATH)
    if not rust_task_fields:
        print(f"ERROR: WorkerTaskItem not found in {WIRE_RUST_PATH}")
        return 1
    if not py_task_fields:
        print(f"ERROR: item field reads not found in {WORKER_PY_PATH}")
        return 1
    rust_only = rust_task_fields - py_task_fields
    py_only = py_task_fields - rust_task_fields
    if rust_only or py_only:
        print("MISMATCH: WorkerTaskItem (Rust) vs worker.py item reads (Python)")
        if rust_only:
            print(f"  Rust-only task fields: {sorted(rust_only)}")
        if py_only:
            print(f"  Python-only task fields: {sorted(py_only)}")
        return 1
    return 0


class WireShape(TypedDict):
    """The wire contract a single PROTOCOL_VERSION describes."""

    protocol_version: int | None
    wire_result: list[str]
    worker_task_item: list[str]


def live_wire_shape() -> WireShape:
    """Read the wire shape the tree currently describes.

    The Rust side alone is recorded. The other checks already refuse when the
    Python side disagrees with it, so storing both would store one fact twice
    and could disagree with itself.
    """
    return {
        "protocol_version": parse_protocol_version_rs(WIRE_RUST_PATH),
        "wire_result": sorted(parse_worker_result_fields(WIRE_RUST_PATH)),
        "worker_task_item": sorted(parse_worker_task_item_fields(WIRE_RUST_PATH)),
    }


def lock_report(lock: WireShape, live: WireShape) -> tuple[str, list[str]]:
    """Judge a recorded shape against the live one. Pure, so it is testable.

    Returns the verdict and the lines explaining it. The verdict is one of:

    - ``ok`` — the record describes the tree.
    - ``unbumped`` — the field sets moved and PROTOCOL_VERSION did not. This is
      the defect the lock exists to catch (#2074): a field added to *both* sides
      passes every other check, because they all compare the two sides against
      each other and the two sides agree.
    - ``stale`` — anything else differs, so the record was not regenerated after
      a legitimate bump.

    The two failures are separated because they are different mistakes with
    different fixes, and one message for both would send the reader to the
    wrong one.
    """
    if lock == live:
        return "ok", []
    changed = [
        key
        for key in ("wire_result", "worker_task_item")
        if lock.get(key) != live.get(key)
    ]
    if changed and lock.get("protocol_version") == live["protocol_version"]:
        lines = [
            "MISMATCH: the wire shape changed and PROTOCOL_VERSION is still"
            f" {live['protocol_version']}"
        ]
        for key in changed:
            was, now = set(lock.get(key, [])), set(live.get(key, []))
            lines.append(f"  {key}:")
            if now - was:
                lines.append(f"    added:   {sorted(now - was)}")
            if was - now:
                lines.append(f"    removed: {sorted(was - now)}")
        lines.append(
            "  bump PROTOCOL_VERSION in wire.rs and result.py, then run"
            " python scripts/check_bridge_sync.py --update-lock"
        )
        return "unbumped", lines
    return "stale", [
        f"MISMATCH: {LOCK_PATH.name} is stale — it records protocol version"
        f" {lock.get('protocol_version')}, the tree describes"
        f" {live['protocol_version']}",
        "  run python scripts/check_bridge_sync.py --update-lock",
    ]


class UnreadableLockError(Exception):
    """The lock file exists but is not valid JSON."""


def read_lock(path: Path) -> WireShape | None:
    """Load a recorded shape, or None when the file is absent.

    Raises ``UnreadableLockError`` on malformed JSON rather than letting a
    ``JSONDecodeError`` reach the top. The file is hand-editable and is named in
    the prek hook's trigger set, so a conflict resolved badly inside it is a
    likely path here — and a stack trace out of a pre-commit hook reads as a
    broken tool rather than as a malformed input.
    """
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"{path.name} is not valid JSON: {exc}"
        raise UnreadableLockError(msg) from exc


def _check_protocol_lock() -> int:
    """Refuse a wire-shape change that did not bump PROTOCOL_VERSION.

    ``wire.rs`` instructs: "Bump when adding, removing, or changing fields in
    WorkerTask or WireResult." Nothing enforced it.

    A diff-based check cannot do this job. The CI Quality job runs
    ``prek run --all-files``, which has no base ref, so the check would work at
    ``git commit`` and not in CI — the shape of defect #1974 recorded. The lock
    makes the previous state an ordinary file, so this is a two-file comparison
    at one commit like every other check here.
    """
    try:
        lock = read_lock(LOCK_PATH)
    except UnreadableLockError as exc:
        print(f"ERROR: {exc} — fix it by hand, or run --update-lock to rewrite it")
        return 1
    if lock is None:
        print(f"ERROR: {LOCK_PATH.name} not found — run --update-lock")
        return 1
    verdict, lines = lock_report(lock, live_wire_shape())
    for line in lines:
        print(line)
    return 0 if verdict == "ok" else 1


def update_lock(path: Path | None = None) -> int:
    """Write the lock, refusing when the shape moved without a version bump.

    This refusal is the enforcement. A record that can always be regenerated is
    advisory: change the fields, regenerate, never bump. Refusing here leaves
    bumping the version as the only way forward.
    """
    path = LOCK_PATH if path is None else path
    live = live_wire_shape()
    try:
        lock = read_lock(path)
    except UnreadableLockError as exc:
        # The check tells the reader to run --update-lock to rewrite an
        # unreadable file, so this path has to survive it. A record that cannot
        # be parsed records nothing, and there is no shape in it to compare.
        print(f"{exc} — rewriting it")
        lock = None
    if lock is not None and lock_report(lock, live)[0] == "unbumped":
        print(
            "REFUSED: the wire shape changed and PROTOCOL_VERSION is still"
            f" {live['protocol_version']}. Bump it first — regenerating here"
            " would record the new shape against the old version and lose the"
            " only evidence that the protocol moved."
        )
        return 1
    path.write_text(json.dumps(live, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path.name} for protocol version {live['protocol_version']}")
    return 0


def main() -> int:
    """Parse Rust/Python structs, compare for sync, and return mismatch count."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-lock",
        action="store_true",
        help="rewrite wire_protocol.lock.json from the tree",
    )
    args = parser.parse_args()
    if args.update_lock:
        return update_lock()
    rust = parse_rust_structs(RUST_PATH)
    python = parse_python_classes(PYTHON_PATH)
    errors = 0
    errors += _check_main_pairs(rust, python)
    errors += _check_reporter_pairs(python)
    errors += _check_raw_frame(python)
    errors += _check_wire_format()
    errors += _check_task_format()
    errors += _check_protocol_version()
    errors += _check_protocol_lock()
    if errors == 0:
        total = len(PAIRS) + len(REPORTER_PAIRS) + 1  # +1 for RawFrame
        print(
            f"OK: all {total} bridge contracts + wire format + task format"
            " + protocol version + wire shape lock in sync"
        )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
