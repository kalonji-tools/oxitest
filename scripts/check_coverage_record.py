#!/usr/bin/env python3
"""Refuse a coverage obligation record that does not account for every region.

ADR-0019 replaces ``codecov.yml``'s hand-written ``ignore:`` list with a record
whose completeness is the gate. This script enforces that record and emits the
``ignore:`` block from it.

It differs from ``check_band_record.py`` in one way that matters. That script
*derives* every row from source text, so ``--update`` can regenerate the whole
record and any disagreement is staleness. A coverage state is not derivable
from source: whether a region is ``exempt`` or ``uncovered`` is a judgement
informed by a coverage run, and no amount of parsing produces it. So this
script derives only the **set** of regions and validates the states a human
wrote. ``--update`` adds a row for a new product file as ``unowned`` and drops
the row for a deleted one. It never invents a state, because a state it invented
would be a claim nothing measured.

The four states, from ADR-0019 as amended by #2176:

``measured``    a test reaches the region and the instrument reports it
``exempt``      the instrument cannot reach the region; ``reason`` says why
``uncovered``   the instrument reaches it and no test does; ``reason`` gives the rate
``unowned``     nothing has been decided for this region — this is the refusing state

``exempt`` and ``uncovered`` are separate because they name different absent
things. ``src/query/bridge.rs`` is a PyO3 entry point with 0 ``#[test]`` that
``cargo test`` cannot enter, so the *instrument* is absent. ``src/pipeline/
transitions/*`` is entered by ``cargo llvm-cov`` every CI run and reports 7.9 %,
so the absent thing is a *test*. Recording the second as ``exempt`` would claim
a measurement problem over code that is measured and untested.

Only ``exempt`` reaches ``codecov.yml``'s ``ignore:``. An ``uncovered`` region
stays in the report: once the project status refuses nothing, hiding untested
code from a report that cannot fail serves only to flatter the number.

The record holds one row per product **file**, not per directory. The old
``ignore:`` list carried ``src/pipeline/transitions/``, and a file added under
that directory inherited the exclusion silently. A per-file record makes that
file ``unowned``, which is the only failure this record can catch that a reader
of ``codecov.yml`` could not.

Exits 0 when the record accounts for every region and agrees with
``codecov.yml``, and 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORD_PATH = Path(__file__).resolve().parent / "coverage_record.tsv"
CODECOV_PATH = ROOT / "codecov.yml"

HEADER = ("state", "region", "instrument", "reason")

MEASURED = "measured"
EXEMPT = "exempt"
UNCOVERED = "uncovered"
UNOWNED = "unowned"
STATES = frozenset({MEASURED, EXEMPT, UNCOVERED, UNOWNED})

# A reason is what separates a recorded judgement from a bare label.
NEEDS_REASON = frozenset({EXEMPT, UNCOVERED})

# A row with no reason writes this rather than an empty field. An empty final
# field leaves a trailing tab, and prek's `trailing-whitespace` hook strips it —
# which silently turns a four-field row into a three-field one and makes the
# record unparsable. `band_record.tsv` never meets this because its last column
# is always populated.
NO_REASON = "-"

# The regions codecov must not count — `exempt` alone, never `uncovered`. The
# docstring above says why; ADR-0019 Amendment 1 decides it.
EMITS_IGNORE = frozenset({EXEMPT})

# Where product code lives, and which instrument reads it. `[tool.coverage.run]
# source` names `python/oxitest`, and codecov's `rust` flag names `src/`, so
# these two roots are the set both instruments claim to cover.
PRODUCT_ROOTS = (("src", "*.rs", "rust"), ("python/oxitest", "*.py", "python"))

IGNORE_HEADER = (
    "# Emitted by scripts/check_coverage_record.py from"
    " scripts/coverage_record.tsv.\n"
    "# Do not edit by hand — `just check` refuses when the two disagree.\n"
    "# A region is here because the record says `exempt`: the instrument that\n"
    "# reads it cannot enter it, so its lines distort the denominator. A region\n"
    "# the record calls `uncovered` is NOT here — it is real untested product\n"
    "# code and the report should say so.\n"
    "ignore:\n"
)


@dataclass(frozen=True)
class Row:
    """One region of product code and what is known about its coverage."""

    state: str
    region: str
    instrument: str
    reason: str


def test_only_modules(root: Path) -> set[str]:
    """Return every ``src/`` file that is compiled only under ``cfg(test)``.

    ``src/`` is about half test code, and a file declared ``#[cfg(test)] mod x;``
    is not product code however it is spelled. Counting one as a region would
    put a test file in ``codecov.yml`` and, worse, let a reader believe the
    record covers a product surface it does not.

    Both spellings occur in this crate and the second is easy to miss:

        #[cfg(test)]
        mod test_doubles;

        #[cfg(test)]
        #[path = "../pipeline_tests.rs"]
        mod tests;

    The ``#[path]`` attribute sits between the gate and the declaration, so a
    pattern that expects them adjacent finds five of the eight modules here.
    """
    pattern = re.compile(
        r"#\[cfg\(test\)\]\s*"
        r"(?:#\[path\s*=\s*\"(?P<path>[^\"]+)\"\]\s*)?"
        r"(?:pub\s+)?mod\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;"
    )
    found: set[str] = set()
    for source in (root / "src").rglob("*.rs"):
        for match in pattern.finditer(source.read_text(encoding="utf-8")):
            explicit = match.group("path")
            if explicit:
                candidates = [(source.parent / explicit).resolve()]
            else:
                name = match.group("name")
                candidates = [
                    source.parent / f"{name}.rs",
                    source.parent / name / "mod.rs",
                ]
            for candidate in candidates:
                if candidate.is_file():
                    found.add(candidate.resolve().relative_to(root).as_posix())
    return found


def product_regions(root: Path) -> dict[str, str]:
    """Return every product file, mapped to the instrument that reads it.

    Keys are repo-relative POSIX paths, which is what ``codecov.yml`` accepts
    and what a row's ``region`` holds. Test-only modules are excluded — see
    ``test_only_modules``.
    """
    excluded = test_only_modules(root)
    found: dict[str, str] = {}
    for sub, glob, instrument in PRODUCT_ROOTS:
        base = root / sub
        if not base.is_dir():
            continue
        for path in base.rglob(glob):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative in excluded:
                continue
            found[relative] = instrument
    return found


def parse_record(text: str) -> tuple[list[Row], list[str]]:
    """Parse the record, returning its rows and every structural problem.

    Problems are collected rather than raised on the first, so one run names
    everything wrong instead of sending the author round a fix-and-retry loop.
    """
    problems: list[str] = []
    rows: list[Row] = []
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return rows, ["the record is empty"]
    if tuple(lines[0].split("\t")) != HEADER:
        problems.append(f"the header row must be {'/'.join(HEADER)}, got {lines[0]!r}")
        return rows, problems
    for number, line in enumerate(lines[1:], start=2):
        fields = line.split("\t")
        if len(fields) != len(HEADER):
            problems.append(
                f"line {number}: expected {len(HEADER)} tab-separated fields,"
                f" got {len(fields)}"
            )
            continue
        rows.append(Row(*fields))
    return rows, problems


def validate(rows: list[Row], regions: dict[str, str]) -> list[str]:
    """Return every reason the record cannot be accepted; empty means go."""
    problems: list[str] = []
    seen: set[str] = set()

    for row in rows:
        if row.region in seen:
            problems.append(f"{row.region}: the record holds this region twice")
        seen.add(row.region)

        if row.state not in STATES:
            problems.append(
                f"{row.region}: state {row.state!r} is not one of"
                f" {', '.join(sorted(STATES))}"
            )
            continue

        if row.state == UNOWNED:
            problems.append(
                f"{row.region}: unowned — decide whether a test reaches it"
                f" (measured), the instrument cannot (exempt), or neither"
                f" (uncovered)"
            )

        reason = row.reason.strip()
        if row.state in NEEDS_REASON and reason in ("", NO_REASON):
            problems.append(
                f"{row.region}: state {row.state} needs a reason, and"
                f" {NO_REASON!r} is the omission the field exists to prevent"
            )
        if row.state not in NEEDS_REASON and reason != NO_REASON:
            problems.append(
                f"{row.region}: state {row.state} must carry {NO_REASON!r} as its"
                f" reason, because a reason nothing acts on is the defect this"
                f" record replaces"
            )

        expected = regions.get(row.region)
        if expected is None:
            problems.append(
                f"{row.region}: the record holds this region and the tree does"
                f" not — run --update"
            )
        elif row.instrument != expected:
            problems.append(
                f"{row.region}: instrument is {row.instrument!r}, and its path"
                f" is read by {expected!r}"
            )

    problems.extend(
        f"{region}: the tree holds this region and the record does not."
        f" A product file with no row is unowned — run --update, then give"
        f" it a state"
        for region in sorted(set(regions) - seen)
    )
    return problems


def emit_ignore(rows: list[Row]) -> str:
    """Render the ``ignore:`` block the record implies."""
    excluded = sorted(row.region for row in rows if row.state in EMITS_IGNORE)
    body = "".join(f'  - "{region}"\n' for region in excluded)
    return IGNORE_HEADER + body


def split_codecov(text: str) -> tuple[str, str]:
    """Return the text before ``ignore:`` and the ignore block itself.

    The block runs to end of file. ``ignore:`` is the last key in this file and
    the emitter appends it, so a key added after it would be silently absorbed —
    which is why the check below compares the whole tail rather than a slice.
    """
    marker = "\nignore:\n"
    index = text.find(marker)
    if index == -1:
        return text, ""
    # Walk back over the comment lines that introduce the block. They are part
    # of what the emitter writes, so they belong to the tail — leaving them in
    # the head makes the comparison compare a block with its header against one
    # without, which can never match and reports as a stale codecov.yml forever.
    lines = text[: index + 1].splitlines(keepends=True)
    header: list[str] = []
    while lines and lines[-1].lstrip().startswith("#"):
        header.insert(0, lines.pop())
    return "".join(lines), "".join(header) + text[index + 1 :]


def format_record(rows: list[Row]) -> str:
    """Render the record, header first, sorted by region."""
    ordered = sorted(rows, key=lambda row: row.region)
    lines: list[str] = ["\t".join(HEADER)]
    lines.extend(
        f"{row.state}\t{row.region}\t{row.instrument}"
        f"\t{row.reason.strip() or NO_REASON}"
        for row in ordered
    )
    return "\n".join(lines) + "\n"


def sync(rows: list[Row], regions: dict[str, str]) -> list[Row]:
    """Add a row for every new region and drop the row for every absent one.

    A new region enters as ``unowned`` with no reason. That state refuses, which
    is the point: ``--update`` makes the record complete and leaves the decision
    to a person. Inventing ``measured`` here would assert that a test reaches
    code nobody has looked at.
    """
    by_region = {row.region: row for row in rows if row.region in regions}
    for region, instrument in regions.items():
        if region not in by_region:
            by_region[region] = Row(UNOWNED, region, instrument, NO_REASON)
    return sorted(by_region.values(), key=lambda row: row.region)


def main() -> int:
    """Validate the record against the tree and against ``codecov.yml``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="add a row for each new region, drop absent ones, re-emit ignore:",
    )
    args = parser.parse_args()

    regions = product_regions(ROOT)

    if not RECORD_PATH.exists():
        if not args.update:
            print(
                f"ERROR: {RECORD_PATH.name} not found — run"
                f" python scripts/check_coverage_record.py --update"
            )
            return 1
        rows: list[Row] = []
    else:
        rows, problems = parse_record(RECORD_PATH.read_text(encoding="utf-8"))
        if problems:
            for problem in problems:
                print(f"  {problem}")
            print("the coverage record cannot be parsed")
            return 1

    if args.update:
        synced = sync(rows, regions)
        RECORD_PATH.write_text(format_record(synced), encoding="utf-8")
        head, _ = split_codecov(CODECOV_PATH.read_text(encoding="utf-8"))
        CODECOV_PATH.write_text(head + emit_ignore(synced), encoding="utf-8")
        counts = Counter(row.state for row in synced)
        summary = " · ".join(f"{state} {counts[state]}" for state in sorted(counts))
        print(f"wrote {RECORD_PATH.name} with {len(synced)} rows — {summary}")
        if counts[UNOWNED]:
            print(
                f"  {counts[UNOWNED]} region(s) are unowned and `just check`"
                f" will refuse until each one has a state"
            )
        return 0

    problems = validate(rows, regions)
    _, committed = split_codecov(CODECOV_PATH.read_text(encoding="utf-8"))
    if committed != emit_ignore(rows):
        problems.append(
            "codecov.yml's ignore: block differs from the one the record emits"
            " — run --update"
        )

    if problems:
        for problem in problems:
            print(f"  {problem}")
        print("the coverage obligation record does not account for every region")
        return 1

    counts = Counter(row.state for row in rows)
    summary = " · ".join(f"{state} {counts[state]}" for state in sorted(counts))
    print(f"OK: {len(rows)} regions — {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
