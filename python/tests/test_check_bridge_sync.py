"""Tests for the source-symmetry extractors in check_bridge_sync.py.

The script is the single enforcement point for field-name lockstep between the
Rust and Python halves of the bridge (#2074). It reads five files as text and
compares the field sets it finds:

- ``src/bridge.rs`` — ``CollectedItem``, ``RawViolation``
- ``src/reporter/bridge.rs`` — the three ``Bridge*`` reporter structs
- ``src/worker_result/wire.rs`` — ``WireResult``, ``RawFrame``, ``WorkerTaskItem``,
  and ``pub(crate) const PROTOCOL_VERSION: u32 = N;``
- ``python/oxitest/_bridge/result.py`` — the dataclasses, ``to_wire()``, and
  ``PROTOCOL_VERSION: int = N``
- ``python/oxitest/_bridge/worker.py`` — the task-item reads

The check is a **drift lint, not a safety guard**. An unknown wire field is
dropped by design so a newer worker can talk to an older coordinator — see
``extra_unknown_fields_are_ignored`` in ``src/worker_result/tests.rs``. The lint
catches an author who changed one side and forgot the other.

Every extractor is a parser over source text, so each one can silently return a
partial set and read as agreement. These tests pin each extractor against a
fixture whose expected field set is known, plus a subprocess integration test
that exercises the full script against a mock repo layout with a deliberate
mismatch.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from types import ModuleType

from oxitest import TempDir

# ── Script location ──────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_bridge_sync.py"


def _load_script_module() -> ModuleType:
    """Load ``scripts/check_bridge_sync.py`` as a module for direct-function testing.

    The scripts directory is not a package, so we use ``importlib.util`` rather
    than a normal import. Fresh module load per call — no cross-test state.
    """
    spec = importlib.util.spec_from_file_location(
        "check_bridge_sync_under_test", _SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Python extractor: parse_protocol_version_py ──────────────────────────────


def test_extractor_py_reads_annotated_int_assignment(tmp: TempDir) -> None:
    """Extractor returns the integer from a module-level ``PROTOCOL_VERSION: int = N``.

    Matches the real ``result.py`` shape — a plain annotated assignment with an
    integer literal. Anything else (call, arithmetic, non-int) should be
    rejected as ``None`` by other tests below.
    """
    module = _load_script_module()
    fake_py = tmp / "result.py"
    fake_py.write_text("PROTOCOL_VERSION: int = 7\n", encoding="utf-8")

    version = module.parse_protocol_version_py(fake_py)

    assert version == 7, (
        "extractor must return the literal integer so a bumped PROTOCOL_VERSION "
        "is detected — a wrong int here silently hides drift"
    )


def test_extractor_py_returns_none_when_constant_absent(tmp: TempDir) -> None:
    """Extractor returns ``None`` when ``PROTOCOL_VERSION`` is not defined.

    A missing constant is a hard failure mode the caller MUST report as an
    ERROR rather than treat as ``0``. Returning ``None`` lets the caller
    distinguish "absent" from "present but zero".
    """
    module = _load_script_module()
    fake_py = tmp / "result.py"
    fake_py.write_text("OTHER_CONSTANT: int = 3\n", encoding="utf-8")

    version = module.parse_protocol_version_py(fake_py)

    assert version is None, (
        "missing PROTOCOL_VERSION must surface as None so the caller emits a "
        "clear ERROR — silently defaulting would mask a rename or deletion"
    )


def test_extractor_py_ignores_non_module_level_assignments(tmp: TempDir) -> None:
    """A ``PROTOCOL_VERSION`` bound inside a class or function is not the wire constant.

    The wire constant lives at module scope. Anything nested is unrelated —
    treating it as the wire version would introduce false positives.
    """
    module = _load_script_module()
    fake_py = tmp / "result.py"
    fake_py.write_text(
        textwrap.dedent("""\
        class Something:
            PROTOCOL_VERSION: int = 99
        """),
        encoding="utf-8",
    )

    version = module.parse_protocol_version_py(fake_py)

    assert version is None, (
        "class-scope PROTOCOL_VERSION must be ignored — only the module-level "
        "constant defines the wire protocol version"
    )


# ── Rust extractor: parse_protocol_version_rs ────────────────────────────────


def test_extractor_rs_reads_pub_crate_const(tmp: TempDir) -> None:
    """Extractor returns the integer from the ``pub(crate) const`` declaration.

    Matches the real ``wire.rs`` shape:
    ``pub(crate) const PROTOCOL_VERSION: u32 = N;``. The visibility qualifier
    and semicolon are load-bearing — the script's regex must not accept the
    constant with the wrong visibility or a missing terminator.
    """
    module = _load_script_module()
    fake_rs = tmp / "wire.rs"
    fake_rs.write_text(
        "pub(crate) const PROTOCOL_VERSION: u32 = 42;\n", encoding="utf-8"
    )

    version = module.parse_protocol_version_rs(fake_rs)

    assert version == 42, (
        "extractor must return the literal integer so a bumped PROTOCOL_VERSION "
        "in wire.rs is detected — a wrong int here silently hides drift"
    )


def test_extractor_rs_returns_none_when_constant_absent(tmp: TempDir) -> None:
    """Extractor returns ``None`` when ``PROTOCOL_VERSION`` is not defined.

    Matches the Python extractor's contract: absent means None, not 0.
    """
    module = _load_script_module()
    fake_rs = tmp / "wire.rs"
    fake_rs.write_text("pub(crate) const OTHER_CONST: u32 = 3;\n", encoding="utf-8")

    version = module.parse_protocol_version_rs(fake_rs)

    assert version is None, (
        "missing PROTOCOL_VERSION in wire.rs must surface as None so the caller "
        "emits a clear ERROR — silently defaulting would mask a rename"
    )


# ── Integration: subprocess-invoke the script with a mismatch ────────────────


def _build_mock_repo(dst: TempDir, py_version: int, rs_version: int) -> None:
    """Copy the real repo layout into ``dst`` but rewrite PROTOCOL_VERSION values.

    The script inspects several files (bridge.rs, worker.py, etc.), so we copy
    the whole set that the script reads and only mutate the two files carrying
    the version constant. This way the other checks pass, and any failure in
    the output is definitively from the protocol-version check.
    """
    files_to_copy = [
        Path("scripts/check_bridge_sync.py"),
        Path("src/bridge.rs"),
        Path("src/reporter/bridge.rs"),
        Path("src/worker_result/wire.rs"),
        Path("python/oxitest/_bridge/result.py"),
        Path("python/oxitest/_bridge/worker.py"),
    ]
    for rel in files_to_copy:
        src = _REPO_ROOT / rel
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, target)

    # Rewrite the two version constants to the requested values.
    #
    # Matched by pattern, not by the literal current number: a hardcoded
    # "= 3" here silently becomes a no-op the next time the protocol is
    # bumped, leaving both sides equal — which makes the mismatch test pass
    # for the wrong reason and the matched test pass vacuously.
    py_path = dst / "python/oxitest/_bridge/result.py"
    py_text, py_subs = re.subn(
        r"PROTOCOL_VERSION: int = \d+",
        f"PROTOCOL_VERSION: int = {py_version}",
        py_path.read_text(encoding="utf-8"),
    )
    assert py_subs == 1, (
        f"expected exactly one PROTOCOL_VERSION assignment in result.py, "
        f"rewrote {py_subs} — the scaffold cannot control the version it is "
        "testing, so every assertion downstream is meaningless"
    )
    py_path.write_text(py_text, encoding="utf-8")

    rs_path = dst / "src/worker_result/wire.rs"
    rs_text, rs_subs = re.subn(
        r"pub\(crate\) const PROTOCOL_VERSION: u32 = \d+;",
        f"pub(crate) const PROTOCOL_VERSION: u32 = {rs_version};",
        rs_path.read_text(encoding="utf-8"),
    )
    assert rs_subs == 1, (
        f"expected exactly one PROTOCOL_VERSION const in wire.rs, rewrote "
        f"{rs_subs} — see above"
    )
    rs_path.write_text(rs_text, encoding="utf-8")


def test_script_exits_zero_when_versions_match(tmp: TempDir) -> None:
    """Baseline: the mock-repo scaffold passes when both constants equal.

    Guards against test scaffold drift — if this ever fails, the mock-repo
    builder is stale relative to the real script's checks, not the version
    check under test.
    """
    _build_mock_repo(tmp, py_version=5, rs_version=5)

    result = subprocess.run(
        [sys.executable, str(tmp / "scripts" / "check_bridge_sync.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, (
        "matched constants must exit 0 — non-zero exit here means an unrelated "
        f"check is failing on the scaffold; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert "protocol version in sync" in result.stdout, (
        "aggregate OK line must name the protocol-version check — if the "
        "summary drifts and drops this token, prek's success output would "
        "no longer document that the check was included in the run; "
        f"stdout={result.stdout!r}"
    )


def test_script_exits_nonzero_and_reports_mismatch(tmp: TempDir) -> None:
    """Mismatched constants: exit non-zero and print a clear mismatch message.

    The failure message must name both files and both values so a developer
    fixing the drift knows exactly what to edit — a bare "mismatch" line
    would send them hunting.
    """
    _build_mock_repo(tmp, py_version=3, rs_version=4)

    result = subprocess.run(
        [sys.executable, str(tmp / "scripts" / "check_bridge_sync.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp,
        check=False,
        timeout=30,
    )

    assert result.returncode != 0, (
        "mismatched PROTOCOL_VERSION must cause a non-zero exit so the "
        "pre-commit hook blocks the commit — exit 0 would let drift ship"
    )
    assert "MISMATCH: PROTOCOL_VERSION" in result.stdout, (
        "output must use the MISMATCH: prefix used by the sibling checks so "
        "developers and tooling can grep for a consistent failure marker; "
        f"stdout={result.stdout!r}"
    )
    assert "result.py=3" in result.stdout, (
        "output must name the Python-side value so the developer knows "
        f"which file holds the older constant; stdout={result.stdout!r}"
    )
    assert "wire.rs=4" in result.stdout, (
        "output must name the Rust-side value so the developer knows "
        f"which file holds the older constant; stdout={result.stdout!r}"
    )


# ── Rust extractor: parse_rust_structs ───────────────────────────────────────


def test_rust_structs_reads_fields_of_a_frompyobject_struct(tmp: TempDir) -> None:
    """The extractor reads every field of a struct carrying the FromPyObject derive.

    PyO3 resolves these by attribute name at runtime, so a field the extractor
    cannot see is a field the lint cannot compare — and the check reports sync.
    """
    module = _load_script_module()
    fake_rs = tmp / "bridge.rs"
    fake_rs.write_text(
        textwrap.dedent("""\
        #[derive(FromPyObject)]
        struct BridgeCacheEntry {
            name: String,
            hits: usize,
        }
        """),
        encoding="utf-8",
    )

    structs = module.parse_rust_structs(fake_rs)

    assert structs.get("BridgeCacheEntry") == {"name", "hits"}, (
        "every field of a FromPyObject struct must be extracted — a missing name "
        f"is compared against nothing and reads as agreement; got {structs}"
    )


def test_rust_structs_ignores_a_struct_without_the_derive(tmp: TempDir) -> None:
    """A struct with no FromPyObject derive does not cross the PyO3 boundary.

    Including it would invent a contract that does not exist and produce a
    mismatch against a Python class that is not required to match it.
    """
    module = _load_script_module()
    fake_rs = tmp / "bridge.rs"
    fake_rs.write_text(
        textwrap.dedent("""\
        #[derive(Debug)]
        struct NotBridged {
            ignored: String,
        }
        """),
        encoding="utf-8",
    )

    structs = module.parse_rust_structs(fake_rs)

    assert "NotBridged" not in structs, (
        "only FromPyObject structs cross the bridge — extracting others would "
        f"assert a contract nothing enforces; got {structs}"
    )


# ── Python extractor: parse_python_classes ───────────────────────────────────


def test_python_classes_reads_dataclass_fields(tmp: TempDir) -> None:
    """One call to this extractor feeds three of the six checks.

    ``main`` passes its result to _check_main_pairs, _check_reporter_pairs and
    _check_raw_frame. A field it fails to read is a field those three compare
    against nothing, and the comparison then agrees on a subset.
    """
    module = _load_script_module()
    fake_py = tmp / "result.py"
    fake_py.write_text(
        textwrap.dedent("""\
        @dataclass(frozen=True)
        class Frame:
            file: str
            lineno: int
        """),
        encoding="utf-8",
    )

    classes = module.parse_python_classes(fake_py)

    assert classes.get("Frame") == {"file", "lineno"}, (
        "every annotated field must be read — a partial set agrees with the Rust "
        f"side on a subset and the check prints OK; got {classes}"
    )


def test_python_classes_reads_the_module_qualified_decorator(tmp: TempDir) -> None:
    """``@dataclasses.dataclass`` is the same decorator by another spelling.

    The extractor matches on the attribute name, so both spellings must land.
    A spelling it misses drops the class, which reads as "class not found".
    """
    module = _load_script_module()
    fake_py = tmp / "result.py"
    fake_py.write_text(
        textwrap.dedent("""\
        @dataclasses.dataclass
        class CollectedViolation:
            kind: str
        """),
        encoding="utf-8",
    )

    classes = module.parse_python_classes(fake_py)

    assert "CollectedViolation" in classes, (
        "the module-qualified decorator must be recognised — missing it drops a "
        f"class the bridge does compare; got {sorted(classes)}"
    )


def test_python_classes_ignores_an_undecorated_class(tmp: TempDir) -> None:
    """A plain class is not a bridge dataclass and has no Rust counterpart."""
    module = _load_script_module()
    fake_py = tmp / "result.py"
    fake_py.write_text(
        textwrap.dedent("""\
        class Helper:
            attribute: str
        """),
        encoding="utf-8",
    )

    classes = module.parse_python_classes(fake_py)

    assert "Helper" not in classes, (
        "only dataclasses cross the bridge — including a plain class would "
        f"assert a contract nothing enforces; got {sorted(classes)}"
    )


# ── Wire extractor: parse_worker_result_fields ───────────────────────────────


def test_wire_result_unions_variant_fields_and_adds_the_tag(tmp: TempDir) -> None:
    """WireResult is an internally-tagged enum, so its fields span the variants.

    The wire payload is one flat JSON object, so the comparable field set is the
    union across variants plus the ``outcome`` tag that selects the variant.
    """
    module = _load_script_module()
    fake_rs = tmp / "wire.rs"
    fake_rs.write_text(
        textwrap.dedent("""\
        #[derive(Debug, serde::Deserialize)]
        #[serde(tag = "outcome")]
        pub enum WireResult {
            Passed {
                node_id: String,
                duration_ms: f64,
            },
            Failed {
                node_id: String,
                message: String,
            },
        }
        """),
        encoding="utf-8",
    )

    fields = module.parse_worker_result_fields(fake_rs)

    assert fields == {"node_id", "duration_ms", "message", "outcome"}, (
        "the union across variants plus the tag is the set the Python to_wire() "
        f"output is compared against; a partial union hides drift; got {fields}"
    )


def test_wire_result_returns_empty_when_the_enum_is_absent(tmp: TempDir) -> None:
    """An empty result must be distinguishable from agreement.

    ``_check_wire_format`` reports an ERROR on an empty set. If the extractor
    returned a partial set instead, a rename of the enum would read as sync.
    """
    module = _load_script_module()
    fake_rs = tmp / "wire.rs"
    fake_rs.write_text("pub struct Unrelated { pub x: u32 }\n", encoding="utf-8")

    fields = module.parse_worker_result_fields(fake_rs)

    assert fields == set(), (
        "a missing WireResult must yield an empty set so the caller emits an "
        f"ERROR rather than comparing nothing against nothing; got {fields}"
    )


# ── Task extractors: parse_worker_task_item_fields, parse_worker_item_reads ──


def test_task_item_reads_the_serde_struct_fields(tmp: TempDir) -> None:
    """WorkerTaskItem is what the coordinator serializes into each worker task."""
    module = _load_script_module()
    fake_rs = tmp / "wire.rs"
    fake_rs.write_text(
        textwrap.dedent("""\
        #[derive(serde::Serialize)]
        pub struct WorkerTaskItem<'a> {
            pub fn_name: &'a str,
            pub node_id: &'a str,
        }
        """),
        encoding="utf-8",
    )

    fields = module.parse_worker_task_item_fields(fake_rs)

    assert fields == {"fn_name", "node_id"}, (
        "the lifetime parameter must not stop the struct matching — the real "
        f"WorkerTaskItem carries one, so a miss here disables the check; got {fields}"
    )


def test_item_reads_cover_subscript_and_get(tmp: TempDir) -> None:
    """worker.py reads task fields two ways, and both are part of the contract.

    A field read only via ``.get()`` is still a field the Rust side must send.
    Missing one form would report a false Rust-only mismatch.
    """
    module = _load_script_module()
    fake_py = tmp / "worker.py"
    fake_py.write_text(
        textwrap.dedent("""\
        def run(item):
            name = item["fn_name"]
            param = item.get("param_id")
            return name, param
        """),
        encoding="utf-8",
    )

    fields = module.parse_worker_item_reads(fake_py)

    assert fields == {"fn_name", "param_id"}, (
        "the subscript form and the get form are both reads of the task "
        f"contract; missing either produces a false mismatch; got {fields}"
    )


# ── Wire base extractor: parse_wire_base_fields ──────────────────────────────


def test_wire_base_reads_every_key_of_the_return_dict(tmp: TempDir) -> None:
    """A required wire key must be found however recently it was added (#2074).

    This is the regression test for the hole this extractor replaced. The old
    regex kept only four names it already knew, so a fifth required key was
    invisible and the check reported sync.
    """
    module = _load_script_module()
    fake_py = tmp / "result.py"
    fake_py.write_text(
        textwrap.dedent("""\
        def _wire_base(outcome, node_id, duration_ms):
            return {
                "type": "result",
                "node_id": node_id,
                "outcome": outcome,
                "shard_id": 0,
            }
        """),
        encoding="utf-8",
    )

    fields = module.parse_wire_base_fields(fake_py)

    assert "shard_id" in fields, (
        "a newly added required key must be extracted — an allowlist of known "
        f"names is what let a sixth key ship uncompared; got {fields}"
    )


def test_wire_base_excludes_the_envelope_discriminator(tmp: TempDir) -> None:
    """``type`` selects the LDJSON line kind and is not a WireResult field.

    The drain loop dispatches on it to tell a result from a diagnostic or a
    trace. No Rust variant carries it, so comparing it would always mismatch.
    """
    module = _load_script_module()
    fake_py = tmp / "result.py"
    fake_py.write_text(
        textwrap.dedent("""\
        def _wire_base(outcome, node_id, duration_ms):
            return {
                "type": "result",
                "node_id": node_id,
            }
        """),
        encoding="utf-8",
    )

    fields = module.parse_wire_base_fields(fake_py)

    assert fields == {"node_id"}, (
        "'type' is the envelope discriminator, not a wire field — including it "
        f"would report a permanent false mismatch; got {fields}"
    )


def test_wire_base_returns_empty_when_the_helper_is_absent(tmp: TempDir) -> None:
    """A renamed or deleted ``_wire_base`` must not read as "no required fields"."""
    module = _load_script_module()
    fake_py = tmp / "result.py"
    fake_py.write_text("def _something_else():\n    return {}\n", encoding="utf-8")

    fields = module.parse_wire_base_fields(fake_py)

    assert fields == set(), (
        "an absent _wire_base must yield an empty set so the required keys go "
        f"missing loudly in the comparison rather than silently; got {fields}"
    )


# ── Whole-payload extractor: parse_to_wire_fields ────────────────────────────


def test_to_wire_unions_all_three_field_sources(tmp: TempDir) -> None:
    """The wire payload is built from three places and all three are contract.

    ``_wire_base`` carries the required keys, ``_wire_optional`` the optional
    ones, and a direct ``output[...]`` assignment the rest. The Rust WireResult
    field set is compared against the union, so a source the extractor cannot
    read produces a false Rust-only mismatch.
    """
    module = _load_script_module()
    fake_py = tmp / "result.py"
    fake_py.write_text(
        textwrap.dedent("""\
        def _wire_base(outcome, node_id, duration_ms):
            return {
                "type": "result",
                "node_id": node_id,
            }

        class FailedResult:
            def to_wire(self, node_id, duration_ms):
                output = _wire_base("failed", node_id, duration_ms)
                _wire_optional(output, message=self.message)
                output["frames"] = self.frames
                return output
        """),
        encoding="utf-8",
    )

    fields = module.parse_to_wire_fields(fake_py)

    assert fields == {"node_id", "message", "frames"}, (
        "the union of the three sources is what the Rust field set is compared "
        f"against; a missing source reports drift that does not exist; got {fields}"
    )


# ── Dogfooding note ──────────────────────────────────────────────────────────

# Uses subprocess + shutil rather than an oxitest fixture because we're
# exercising a standalone script that runs *outside* the oxitest process
# (as a pre-commit hook). oxi.raises / Patcher / etc. don't fit — the code
# under test is invoked as a separate Python process. `TempDir` is the one
# oxitest fixture that does apply, and every test above uses it.
