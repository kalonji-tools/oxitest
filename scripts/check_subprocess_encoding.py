#!/usr/bin/env python3
"""Check that every text-mode `subprocess` call names its encoding.

A text-mode call with no `encoding=` decodes the child's bytes with
`locale.getencoding()`. On Linux and macOS that is UTF-8 and nothing is ever
noticed; on Windows it is cp1252, and the decode dies on the first byte outside
that codec. oxitest's reporter emits `═` (U+2550) in its separator rule, whose
third byte is `0x90` — so the very first line of oxitest output kills the
reader thread, `result.stdout` becomes `None`, and every downstream assertion
fails with `TypeError: ... 'NoneType'` far from the cause.

Measured on `windows-latest` (#1951): 292 of 1774 Python tests failed that way,
with **zero** `AssertionError`s in the whole run — every failure was the harness
crashing before it could assert. One missing keyword argument, 263 red tests.

Ruff has no rule for this as of 0.15.16, which is why this file exists.

## The predicate, and the mistake it encodes

Text mode is `text=True` or `universal_newlines=True` — **not** `capture_output`.
`capture_output=True` only sets `stdout=PIPE, stderr=PIPE`; the result is `bytes`
and cannot raise `UnicodeDecodeError` on any platform.

That distinction is the whole reason this is an AST check. #1986's first sweep
treated `capture_output` as text mode and reported 28 violations where there are
18 — it would have "fixed" 10 git-plumbing calls that discard their output and
were never at risk. A gate that fires on correct code gets suppressed, and then
it is not a gate.

`errors=` also implies text mode and is accepted in place of `encoding=`: passing
either one is enough to take the call off the locale default.

Exits 0 if every text-mode call names an encoding, 1 with a report of each that
does not.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# Repo root is two levels up from scripts/
ROOT = Path(__file__).resolve().parent.parent

# Directories that hold first-party Python. `python/oxitest/` currently has no
# subprocess calls at all — the product spawns workers from the Rust side — but
# it is in scope so that stays true by gate rather than by luck.
SCAN_DIRS = ("python", "scripts")

# Every callable in `subprocess` that spawns and can be put into text mode.
SPAWNERS = frozenset({"run", "Popen", "check_output", "check_call", "call"})

# Passing any of these puts the call in text mode.
TEXT_FLAGS = ("text", "universal_newlines")

# Passing either of these takes the call off the locale default, which is all
# this gate asks for.
ENCODING_KWARGS = ("encoding", "errors")


@dataclass(frozen=True)
class Violation:
    """One text-mode subprocess call that does not name an encoding."""

    path: Path
    line: int
    func: str


def _is_truthy(node: ast.expr) -> bool:
    """Whether a keyword's value is anything other than a literal False/None.

    `text=False` is an explicit opt-out of text mode and is not a violation.
    A non-literal (`text=flag`) is treated as truthy: the gate cannot know, and
    guessing "safe" is the direction that lets a real one through.
    """
    return not (isinstance(node, ast.Constant) and node.value in (False, None))


def _subprocess_attr(call: ast.Call) -> str | None:
    """The `subprocess.X` attribute name this call invokes, if it is one.

    Only the qualified `subprocess.run(...)` form is recognised. A bare
    `from subprocess import run` would slip past — accepted deliberately: the
    repo uses the qualified form everywhere, and matching bare names means
    matching every local function called `run`.
    """
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    value = func.value
    if not (isinstance(value, ast.Name) and value.id == "subprocess"):
        return None
    return func.attr if func.attr in SPAWNERS else None


def find_violations(root: Path) -> list[Violation]:
    """Every text-mode subprocess call under SCAN_DIRS that names no encoding."""
    violations: list[Violation] = []
    for scan_dir in SCAN_DIRS:
        for path in sorted((root / scan_dir).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                # Deliberately syntax-broken fixtures live under python/tests/data/.
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                attr = _subprocess_attr(node)
                if attr is None:
                    continue
                keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
                in_text_mode = any(
                    flag in keywords and _is_truthy(keywords[flag])
                    for flag in TEXT_FLAGS
                )
                names_encoding = any(kw in keywords for kw in ENCODING_KWARGS)
                if in_text_mode and not names_encoding:
                    violations.append(Violation(path, node.lineno, attr))
    return violations


def main() -> int:
    """Report every violation and exit non-zero if there are any."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()

    violations = find_violations(args.root)
    if not violations:
        return 0

    print("Text-mode subprocess calls that do not name an encoding:\n")
    for violation in violations:
        relative = violation.path.relative_to(args.root)
        print(f"  {relative}:{violation.line}  subprocess.{violation.func}")
    print(
        f'\n{len(violations)} call(s). Add `encoding="utf-8"`.\n'
        "Without it the child's output is decoded with locale.getencoding(),"
        " which is\ncp1252 on Windows — and oxitest's own separator rule"
        " contains a byte it cannot\ndecode, so the read fails before any"
        " assertion runs (#1951, #1986)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
