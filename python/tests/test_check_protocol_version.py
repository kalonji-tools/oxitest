"""Tests for the ``PROTOCOL_VERSION`` equality check in check_bridge_sync.py.

The Python and Rust wire-protocol version constants live in two files that
must stay in lock-step:

- ``python/oxitest/_bridge/result.py`` — ``PROTOCOL_VERSION: int = N``
- ``src/worker_result/wire.rs`` — ``pub(crate) const PROTOCOL_VERSION: u32 = N;``

The bridge-sync pre-commit hook enforces this. Tests cover the extractor
functions in isolation plus a subprocess integration test that exercises the
full script against a mock repo layout with a deliberate mismatch.
"""

from __future__ import annotations

import importlib.util
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
    fake_py.write_text("PROTOCOL_VERSION: int = 7\n")

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
    fake_py.write_text("OTHER_CONSTANT: int = 3\n")

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
        """)
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
    fake_rs.write_text("pub(crate) const PROTOCOL_VERSION: u32 = 42;\n")

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
    fake_rs.write_text("pub(crate) const OTHER_CONST: u32 = 3;\n")

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
    py_path = dst / "python/oxitest/_bridge/result.py"
    py_text = py_path.read_text()
    py_path.write_text(
        py_text.replace(
            "PROTOCOL_VERSION: int = 3",
            f"PROTOCOL_VERSION: int = {py_version}",
        )
    )
    rs_path = dst / "src/worker_result/wire.rs"
    rs_text = rs_path.read_text()
    rs_path.write_text(
        rs_text.replace(
            "pub(crate) const PROTOCOL_VERSION: u32 = 3;",
            f"pub(crate) const PROTOCOL_VERSION: u32 = {rs_version};",
        )
    )


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


# ── Guardrail: the real repo is always in sync ───────────────────────────────


def test_real_repo_protocol_versions_are_in_sync() -> None:
    """Sanity guard: the current repo's two PROTOCOL_VERSION constants agree.

    Complements the pre-commit hook by making drift visible in the test suite
    too — CI catches it even if a contributor bypassed the hook.
    """
    module = _load_script_module()
    py_version = module.parse_protocol_version_py(module.PYTHON_PATH)
    rs_version = module.parse_protocol_version_rs(module.WIRE_RUST_PATH)

    assert py_version is not None, (
        "PROTOCOL_VERSION must exist in result.py — its absence breaks the "
        "wire-format contract with the Rust coordinator"
    )
    assert rs_version is not None, (
        "PROTOCOL_VERSION must exist in wire.rs — its absence breaks the "
        "wire-format contract with the Python bridge"
    )
    assert py_version == rs_version, (
        f"Python PROTOCOL_VERSION={py_version} disagrees with Rust "
        f"PROTOCOL_VERSION={rs_version}; bump both in the same commit "
        "or workers and the coordinator will refuse to talk"
    )


# ── Dogfooding note ──────────────────────────────────────────────────────────

# Uses subprocess + shutil rather than an oxitest fixture because we're
# exercising a standalone script that runs *outside* the oxitest process
# (as a pre-commit hook). oxi.raises / Patcher / etc. don't fit — the code
# under test is invoked as a separate Python process. `TempDir` is the one
# oxitest fixture that does apply, and every test above uses it.
