#!/usr/bin/env python3
"""Apply one mutant to a file by anchored replacement.

Reads the exact old and new text from two files rather than from arguments, so
multi-line and quote-heavy anchors need no shell escaping. The anchor must occur
exactly once: an anchor matching zero or many times is a mutant that was never
applied, and an unapplied mutant reads exactly like a surviving one unless it is
caught here.

Invoked by the `mutate` recipe in the justfile, which owns the surrounding loop
(clean baseline, build, test, revert, rebuild).

Exit codes:
  0  the mutant was applied
  9  the mutant cannot be applied — identical anchors, or the anchor did not
     match exactly once. The tree is left untouched.
  *  anything else is this script failing for a reason of its own, which makes
     the run void rather than a miss; the caller reports it as such.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The caller maps this code to "MUTANT NOT APPLIED"; every other non-zero exit
# maps to "VOID". Keeping them distinct is the difference between "your mutant
# was wrong" and "this measurement never happened".
CANNOT_APPLY = 9
USAGE_ERROR = 2

EXPECTED_ARGS = 3


def apply_mutant(target: Path, old_anchor: Path, new_anchor: Path) -> int:
    """Replace the anchor in `target`, returning the process exit code."""
    old_text = old_anchor.read_text(encoding="utf-8")
    new_text = new_anchor.read_text(encoding="utf-8")

    # Identical anchors are the one way a matched replacement leaves the file
    # unchanged, so ruling it out here is what lets a zero exit mean the file
    # really moved — no second check downstream is needed.
    if old_text == new_text:
        print(
            "anchor and replacement are identical — there is no mutation to apply",
            file=sys.stderr,
        )
        return CANNOT_APPLY

    source = target.read_text(encoding="utf-8")
    matches = source.count(old_text)
    if matches != 1:
        print(
            f"anchor matched {matches} times in {target}, expected exactly 1",
            file=sys.stderr,
        )
        return CANNOT_APPLY

    target.write_text(source.replace(old_text, new_text), encoding="utf-8")
    return 0


def main(argv: list[str]) -> int:
    """Parse arguments and apply the mutant, returning the process exit code."""
    if len(argv) != EXPECTED_ARGS:
        print(
            f"usage: {Path(__file__).name} "
            "<target> <old-anchor-file> <new-anchor-file>",
            file=sys.stderr,
        )
        return USAGE_ERROR

    target, old_anchor, new_anchor = (Path(arg) for arg in argv)
    try:
        return apply_mutant(target, old_anchor, new_anchor)
    except OSError as exc:
        # Most often an anchor argument that is inline text rather than a path.
        print(f"could not read or write: {exc}", file=sys.stderr)
        return USAGE_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
