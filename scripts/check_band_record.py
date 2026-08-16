#!/usr/bin/env python3
"""Derive ADR-0019 band membership for every test, and refuse a stale record.

A test belongs to the band of what it starts. This script answers that question
for both languages by parsing source text, writes the answer to
``scripts/band_record.tsv``, and exits 1 when the tree and the record disagree.

Run ``--update`` to rewrite the record from the tree.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

# Repo root is two levels up from scripts/
ROOT = Path(__file__).resolve().parent.parent
RECORD_PATH = Path(__file__).resolve().parent / "band_record.tsv"

# The closed funnel of Python helpers that start a product process. A test
# reaching one of these is a Command band test. Widening this set is a reviewed
# line here rather than a silent reclassification of some test's row, which is
# the whole reason the resolver refuses on anything it cannot place.
PRODUCT_STARTERS = frozenset(
    {
        "run_oxitest",
        "run_oxitest_subcmd",
        "run_with_event_log",
        # The installed console script. The suite's only test of the packaging
        # entry point reaches it through a Path variable, so no argv literal
        # can name it (#2004).
        "_console_script",
    }
)


# The name the product is invoked under, whatever the route. A subprocess whose
# argv names this starts the CLI or the worker; one that does not starts the
# environment. ADR-0019 draws that line for Rust — "The 23 Command::new sites in
# src/ start git, true and cat. Those are the environment, not the product."
PRODUCT_MODULE = "oxitest"

# Directories that hold no test. `data/` holds Specimens — test-shaped functions
# a band test writes into a project as input, which `norecursedirs` excludes and
# no band collects.
#
# `generated/` is the one that guards determinism rather than meaning.
# `benchmarks/generate.py` writes it, so it is absent on a clean checkout and
# holds 359 test-shaped files once anyone runs the generator. Reading it would
# make the row count depend on whether the generator had run, which is exactly
# what acceptance criterion 5 forbids.
#
# `python/tests/docs/` is NOT here: `justfile:295` and `.github/workflows/
# docs.yml` both run `python -m oxitest python/tests/docs/ --strict=off`, so
# those 118 functions are collected by a second command rather than by
# `just test-python`.
UNCOLLECTED_DIRS = frozenset({"data", "fixtures", "__pycache__", "generated"})

# The functions that spawn. Any name here means a real process was started, so
# a caller cannot be classified without reading argv.
SPAWN_FUNCTIONS = frozenset({"run", "Popen", "check_output", "check_call", "call"})


class UnresolvedSpawnError(Exception):
    """A test spawns by a route the resolver cannot classify.

    Refusing is the point. The funnel is closed today and nothing keeps it
    closed, so a fourth spawn helper must meet an error that names the test
    rather than a Library row that reads exactly like a correct one.
    """


class StaleExceptionError(Exception):
    """An exception entry no longer answers a live refusal.

    This is what keeps the list shrinking. ``codecov.yml`` accumulated 21
    entries that nothing read, one of them naming a file a commit had deleted;
    an entry that must justify itself on every run cannot reach that state.
    """


# Spawns no parse can read, answered once by a reader. Each entry is a band and
# the reason it is that band. An entry that stops answering a live refusal
# fails the gate, so this list can only shrink without a deliberate edit.
EXCEPTIONS: dict[str, tuple[str, str]] = {
    "python/tests/test_rollup_results_check.py"
    "::test_the_results_program_reaches_the_right_verdict": (
        "Library",
        "argv[0] is a shutil.which result for jq, which is the environment",
    ),
    "python/tests/test_rollup_results_check.py"
    "::test_the_change_filter_guard_reaches_the_right_verdict": (
        "Library",
        "argv[0] is a shutil.which result for bash, which is the environment",
    ),
}


@dataclass(frozen=True)
class ModuleScope:
    """What one test module makes reachable from inside itself.

    The three lookups travel together because they are read together on every
    recursion step, and passing them one by one made the resolver's signature
    wider than the question it answers.
    """

    local_defs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef]
    fixtures: dict[str, ast.FunctionDef | ast.AsyncFunctionDef]
    imported_spawns: frozenset[str]


@dataclass(frozen=True)
class Row:
    """One test's membership: its band, its identity, and its attributes."""

    band: str
    test_id: str
    attributes: tuple[str, ...] = ()


def _iter_tests(
    tree: ast.Module,
) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Yield ``(qualified name, node)`` for every collected test in *tree*.

    Only a module-level function or a class method is collected. A function
    defined inside another function is not, so descending into bodies invents
    rows for tests that do not exist. The class segment is part of the name
    because two classes may hold a method of the same name, and a key that
    dropped it would silently merge two rows into one.
    """
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name.startswith("test_"):
                yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(
                    member, ast.FunctionDef | ast.AsyncFunctionDef
                ) and member.name.startswith("test_"):
                    yield f"{node.name}::{member.name}", member


def _is_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A fixture is a function carrying a ``fixture`` decorator."""
    return any("fixture" in ast.unparse(dec) for dec in node.decorator_list)


def _referenced_names(node: ast.AST) -> set[str]:
    """Every bare name and attribute name appearing under *node*.

    Attribute names are included unqualified so that ``helpers.run_oxitest``
    and a bare ``run_oxitest`` resolve alike — the funnel is defined by the
    function, not by how a caller happened to reach it.
    """
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
    return found


def _is_sys_executable(node: ast.expr) -> bool:
    """``sys.executable`` — the interpreter, which may or may not run the product."""
    return isinstance(node, ast.Attribute) and node.attr == "executable"


def _spawn_starts_product(call: ast.Call, *, where: str) -> bool:
    """Read a spawn call's argv and say whether it starts the product.

    Three answers, and the third is a refusal. A literal argv naming
    ``oxitest`` starts the product. A literal argv naming any other program
    starts the environment. An argv the parse cannot read is neither, and
    guessing it would be indistinguishable from having read it.
    """
    argv = call.args[0] if call.args else None
    if not isinstance(argv, ast.List) or not argv.elts:
        msg = (
            f"{where}: spawn call with an argv this parse cannot read."
            " Give it a literal list, or widen the funnel in"
            " scripts/check_band_record.py and say why."
        )
        raise UnresolvedSpawnError(msg)

    head = argv.elts[0]
    if isinstance(head, ast.Constant) and isinstance(head.value, str):
        # A named program. It is the product only when the program itself is
        # the product — `git clone oxitest` starts git, and an argument that
        # merely names this repository decides nothing.
        return head.value.rsplit("/", 1)[-1].split(".")[0] == PRODUCT_MODULE
    if _is_sys_executable(head):
        # The interpreter. The module it is asked to run decides.
        return any(
            isinstance(element, ast.Constant)
            and isinstance(element.value, str)
            and element.value.split(".")[0] == PRODUCT_MODULE
            for element in argv.elts[1:]
        )

    msg = (
        f"{where}: spawn call whose program this parse cannot identify."
        " Name it literally, or widen the funnel in"
        " scripts/check_band_record.py and say why."
    )
    raise UnresolvedSpawnError(msg)


def _starts_product(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    scope: ModuleScope,
    where: str,
    seen: frozenset[str] = frozenset(),
) -> bool:
    """Does *node* start a product process, by any route it can reach?

    Three routes, and two of them are invisible to a parse of the function
    body alone: a same-file helper that spawns, and a fixture parameter whose
    provider spawns.
    """
    if node.name in seen:
        return False
    seen = seen | {node.name}

    if _referenced_names(node) & PRODUCT_STARTERS:
        return True

    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        function = child.func
        name = (
            function.attr
            if isinstance(function, ast.Attribute)
            else function.id
            if isinstance(function, ast.Name)
            else None
        )
        if _is_spawn_call(function, imported=scope.imported_spawns):
            if _spawn_starts_product(child, where=where):
                return True
        elif name in scope.local_defs and _starts_product(
            scope.local_defs[name],
            scope=scope,
            where=where,
            seen=seen,
        ):
            return True

    # A fixture parameter is a route: the test starts whatever its provider
    # starts. Two real tests in test_process_tier_negatives.py reach the
    # product this way and spawn nothing themselves.
    for argument in node.args.args + node.args.kwonlyargs:
        provider = scope.fixtures.get(argument.arg)
        if provider is not None and _starts_product(
            provider,
            scope=scope,
            where=where,
            seen=seen,
        ):
            return True

    return False


def _is_spawn_call(function: ast.expr, *, imported: frozenset[str]) -> bool:
    """True when this call reaches ``subprocess``, by either spelling.

    ``run`` is a common method name, so the module qualifier is what separates
    ``subprocess.run`` from any other ``.run(...)`` in a test body. A module
    that writes ``from subprocess import run`` has no qualifier to read, so the
    names it imported are collected per module and checked here — otherwise the
    import spelling decides the band, and the wrong answer is the silent one.
    """
    if (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id == "subprocess"
    ):
        return function.attr in SPAWN_FUNCTIONS
    return isinstance(function, ast.Name) and function.id in imported


def _imported_spawns(tree: ast.Module) -> frozenset[str]:
    """The spawn functions this module imported directly from ``subprocess``."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            found |= {
                alias.asname or alias.name
                for alias in node.names
                if alias.name in SPAWN_FUNCTIONS
            }
    return frozenset(found)


def _test_id(path: Path, name: str, *, repo_root: Path) -> str:
    """``<posix path>::<name>``.

    The path is POSIX-separated whatever the platform deriving it. ``just
    check`` runs on ubuntu-latest only, so no gate could report a record
    regenerated on Windows with backslashes in it.
    """
    return f"{path.relative_to(repo_root).as_posix()}::{name}"


def _rows_for_module(
    path: Path,
    *,
    repo_root: Path,
    exceptions: Mapping[str, tuple[str, str]],
) -> tuple[list[Row], set[str], list[str]]:
    """One test module's rows, the exceptions it used, and the ones it did not.

    A caller cannot tell a used exception from an unused one after the fact, so
    both sets come back here rather than being recomputed.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    local_defs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    fixtures: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            local_defs[node.name] = node
            if _is_fixture(node):
                fixtures[node.name] = node

    scope = ModuleScope(local_defs, fixtures, _imported_spawns(tree))
    attributes = _attributes(path, source)
    rows: list[Row] = []
    answered: set[str] = set()
    stale: list[str] = []
    for qualified, node in _iter_tests(tree):
        test_id = _test_id(path, qualified, repo_root=repo_root)
        try:
            band = (
                "Command"
                if _starts_product(node, scope=scope, where=test_id)
                else "Library"
            )
        except UnresolvedSpawnError:
            entry = exceptions.get(test_id)
            if entry is None:
                raise
            band = entry[0]
            answered.add(test_id)
        else:
            if test_id in exceptions:
                stale.append(test_id)
        rows.append(Row(band, test_id, attributes))
    return rows, answered, stale


def python_rows(
    tests_root: Path,
    *,
    repo_root: Path,
    exceptions: Mapping[str, tuple[str, str]],
) -> list[Row]:
    """Derive a row for every Python test function under *tests_root*.

    *exceptions* answers the spawns no parse can read, and it carries no
    default: a caller that does not say which exceptions apply is asking
    for the whole tree's list against whatever subtree it passed, and every
    entry then reads as stale. Every entry must answer a live refusal; one
    that does not raises ``StaleExceptionError``.
    """
    rows: list[Row] = []
    answered: set[str] = set()
    stale: list[str] = []
    for path in sorted(tests_root.rglob("test_*.py")):
        if UNCOLLECTED_DIRS & set(path.parts):
            continue
        found, used, unneeded = _rows_for_module(
            path, repo_root=repo_root, exceptions=exceptions
        )
        rows += found
        answered |= used
        stale += unneeded

    stale += sorted(set(exceptions) - answered - set(stale))
    if stale:
        listed = "\n  ".join(sorted(stale))
        msg = (
            f"exception entries that answer no live refusal — delete them:\n  {listed}"
        )
        raise StaleExceptionError(msg)
    return rows


# Rust is read by brace matching rather than by an attribute grep. The key needs
# the function name and the module path, so the method is forced rather than
# chosen — and the two methods disagree, 1 779 by grep against 1 775 here.
_MOD = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z0-9_]+)\s*\{")
_TEST_ATTR = re.compile(r"^\s*#\[test\]\s*$")
_FN = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z0-9_]+)")
_GIL = re.compile(r"with_gil|Python::attach")

# A tooling test names its subject directory. This repository reaches a script
# by a Path join — `_REPO_ROOT / "scripts" / "check_bridge_sync.py"` — so a
# `scripts/` substring matches none of them.
_TOOLING_SUBJECT = re.compile(
    r"""["'](?:scripts|benchmarks)["']|(?:scripts|benchmarks)/"""
)


def _rust_body(lines: list[str], start: int) -> tuple[str, int]:
    """Return the brace-matched body beginning at *start*, and its last line."""
    depth = 0
    opened = False
    collected: list[str] = []
    index = start
    while index < len(lines):
        line = lines[index]
        collected.append(line)
        if "{" in line:
            opened = True
        depth += line.count("{") - line.count("}")
        if opened and depth <= 0:
            break
        index += 1
    return "\n".join(collected), index


def rust_rows(src_root: Path, *, repo_root: Path) -> list[Row]:
    """Derive a row for every ``#[test]`` function under *src_root*.

    Every Rust test is Crate **except** one that attaches the GIL. ADR-0019
    step 1 asks whether the test starts Python, and 23 of them do.
    """
    rows: list[Row] = []
    for path in sorted(src_root.rglob("*.rs")):
        lines = path.read_text(encoding="utf-8").splitlines()
        modules: list[tuple[str, int]] = []
        depth = 0
        pending = False
        index = 0
        while index < len(lines):
            line = lines[index]

            if _TEST_ATTR.match(line):
                pending = True
                index += 1
                continue

            function = _FN.match(line)
            if pending and function:
                body, end = _rust_body(lines, index)
                qualified = "::".join(
                    [name for name, _ in modules] + [function.group(1)]
                )
                band = "Library" if _GIL.search(body) else "Crate"
                rows.append(Row(band, _test_id(path, qualified, repo_root=repo_root)))
                pending = False
                # The body is brace-balanced, so the running depth is unchanged
                # by skipping it.
                index = end + 1
                continue

            opening = _MOD.match(line)
            if opening:
                modules.append((opening.group(1), depth))

            depth += line.count("{") - line.count("}")
            while modules and depth <= modules[-1][1]:
                modules.pop()
            index += 1
    return rows


def doctest_rows(package_root: Path, *, repo_root: Path) -> list[Row]:
    """Derive a row for every docstring example under *package_root*.

    Derived by parse, never by running the suite. A doctest can be conditional
    on the interpreter — CPython 3.14 unified ``types.UnionType`` with
    ``typing.Union`` and ``type_display_name`` carries an example that turns on
    it (#2098) — so a run-derived record would be a function of whichever of
    cp311-cp314 regenerated it.

    ``test_the_parsed_doctest_set_agrees_with_the_suite`` is where this scanner
    is held to oxitest's own.
    """
    rows: list[Row] = []
    for path in sorted(package_root.rglob("*.py")):
        if UNCOLLECTED_DIRS & set(path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(
                node,
                ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
            ):
                continue
            docstring = ast.get_docstring(node)
            if docstring is None or ">>>" not in docstring:
                continue
            name = "<module>" if isinstance(node, ast.Module) else node.name
            rows.append(
                Row(
                    "Library",
                    _test_id(path, name, repo_root=repo_root),
                    ("documentation",),
                )
            )
    return rows


def _attributes(path: Path, source: str) -> tuple[str, ...]:
    """The attributes a test file's subject earns it.

    An attribute names a subject, not a liveness, which is why neither of
    these is a band. ADR-0019 refused a ``docs`` band and a ``tooling`` band on
    exactly that measurement.
    """
    found: list[str] = []
    if "docs" in path.parts:
        found.append("documentation")
    if {"scripts", "benchmarks"} & set(path.parts) or _TOOLING_SUBJECT.search(source):
        found.append("tooling")
    return tuple(found)


def format_record(rows: Iterable[Row]) -> str:
    """Render *rows* as the committed record.

    Sorted, so a regeneration is byte-identical without anyone remembering to
    ask for it. Tab-separated rather than JSON because the value of this file
    is its diff, and an object-per-row encoding makes the same information
    three times louder.

    A row with no attributes ends at its identifier. A trailing tab is trailing
    whitespace, and the ``trailing-whitespace`` prek hook strips it on commit —
    the record would then disagree with the derivation that had just written
    it, and the gate would refuse forever.
    """
    lines = [
        "\t".join(
            [row.band, row.test_id, ",".join(row.attributes)]
            if row.attributes
            else [row.band, row.test_id]
        )
        for row in sorted(rows, key=lambda row: (row.band, row.test_id))
    ]
    return "".join(f"{line}\n" for line in lines)


def compare(*, derived: Iterable[Row], committed: str) -> tuple[int, list[str]]:
    """Compare the tree against the committed record.

    Returns an exit code and the lines to print. The refusal names the fix,
    following ``check_bridge_sync.py``: a reader who is not told the command
    edits the record by hand, which defeats the derivation.
    """
    rendered = format_record(derived)
    if rendered == committed:
        return 0, []

    live = set(rendered.splitlines())
    stored = set(committed.splitlines())
    lines = [f"  tree has, record lacks:   {row}" for row in sorted(live - stored)]
    lines += [f"  record has, tree lacks:   {row}" for row in sorted(stored - live)]
    lines.append(
        "the band record disagrees with the tree — fix it by running"
        " python scripts/check_band_record.py --update"
    )
    return 1, lines


def derive(repo_root: Path) -> list[Row]:
    """Every row, both languages, doctests included."""
    tests = python_rows(
        repo_root / "python" / "tests", repo_root=repo_root, exceptions=EXCEPTIONS
    )
    # `benchmarks/` holds tests that nothing collects — #2180 owns running them.
    # The record states membership, not collection, so leaving them out would
    # hide the very gap the record exists to show.
    benchmarks = python_rows(
        repo_root / "benchmarks", repo_root=repo_root, exceptions={}
    )
    crate = rust_rows(repo_root / "src", repo_root=repo_root)
    docs = doctest_rows(repo_root / "python" / "oxitest", repo_root=repo_root)
    return tests + benchmarks + crate + docs


def main() -> int:
    """Derive the record and compare it against the committed one."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite band_record.tsv from the tree",
    )
    args = parser.parse_args()

    rows = derive(ROOT)
    if args.update:
        RECORD_PATH.write_text(format_record(rows), encoding="utf-8")
        print(f"wrote {RECORD_PATH.name} with {len(rows)} rows")
        return 0

    if not RECORD_PATH.exists():
        print(
            f"ERROR: {RECORD_PATH.name} not found — run"
            " python scripts/check_band_record.py --update"
        )
        return 1

    code, lines = compare(
        derived=rows, committed=RECORD_PATH.read_text(encoding="utf-8")
    )
    for line in lines:
        print(line)
    if code == 0:
        counts = Counter(row.band for row in rows)
        summary = " · ".join(f"{band} {counts[band]}" for band in sorted(counts))
        print(f"OK: {len(rows)} tests — {summary}")
    return code


if __name__ == "__main__":
    sys.exit(main())
