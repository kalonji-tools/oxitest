#!/usr/bin/env python3
r"""Check that no justfile interpolation is wrapped in quotes.

`just` substitutes `{{ x }}` textually, before the shell sees the line. Wrapping
that substitution in quotes — `'{{ x }}'` or `"{{ x }}"` — therefore does not
quote the value: it puts the value *between* two quote characters, and a quote
of the same kind inside the value closes the pair early. The rest of the
argument then reaches the shell unquoted.

Both quote characters carry the defect. Measured with an argv-echo stub:

    $ just dq 'he said "hi" and (x)'
    ["he said hi and (x)"]

Both halves of that are bad, and the quiet one is worse. A value containing a
quote **and** shell syntax kills the recipe:

    just review-dispose ponytail 3 Fixed "It now reads 'a not in (b, c)'."
    sh: -c: line 1: syntax error near unexpected token `('

A value containing only a quote succeeds and is silently altered — the shell
joins the fragments and drops the quote characters, so the recorded text is not
the text the caller supplied. Measured on #2015: `a quoted 'token' inside` was
recorded as `a quoted token inside`.

The fix is `just`'s own shell-escaper, `{{ quote(x) }}`, which emits `'\''` for
an embedded quote and round-trips any value byte-exact.

## The predicate, and what it deliberately does not flag

Only a quoted token that is **nothing but** one interpolation is a violation.
An interpolation embedded in a longer quoted string is a different construct:

    @printf '\033[{{color}}m→ %s\033[0m\n' {{ quote(msg) }}

`quote()` cannot compose there — it escapes a whole value, and here the value is
one part of a literal. Flagging it would demand string concatenation for a
colour code that never contains a quote, so the rule keys on the whole-token
form and this shape is accepted.

Unquoted interpolations are also accepted, and `{{ args }}` relies on it: a
variadic argument is meant to word-split into separate arguments, which is
exactly what quoting would prevent.

## Scope

This checks one file. `just` also reads `.justfile` and can import further
files, and none of those would be gated. The repo has one justfile; widen the
`files` pattern in `prek.toml` alongside this default when that stops being true.

Exits 0 when no interpolation is quoted, 1 with a report of each that is.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Repo root is two levels up from scripts/
ROOT = Path(__file__).resolve().parent.parent

# A quoted token that is nothing but one interpolation. The backreference makes
# the closing quote match the opening one, so `'{{ a }}'` and `"{{ b }}"` are
# both caught and a quote of the other kind inside the value is not mistaken for
# the closing one. The inner class excludes both quote characters, so
# `'{{ a }}' '{{ b }}'` matches twice rather than once across the gap, and
# excludes `{` and `}` so the match cannot run past one interpolation.
#
# Known limit: an interpolation containing a `just` conditional carries braces —
# `'{{ if x == "a" { "p" } else { "q" } }}'` — so this pattern does not match it.
# Admitting braces would let one match run across two interpolations, which is
# the worse failure. This justfile has no conditional inside an interpolation;
# every conditional in it is a top-level assignment such as `fix_env := if ...`.
_QUOTED_INTERPOLATION = re.compile(r"(['\"])\{\{([^'\"{}]*)\}\}\1")


@dataclass(frozen=True)
class Violation:
    """One quoted interpolation: where it is, what it quotes, and with which quote."""

    line: int
    name: str
    quote: str

    @property
    def found(self) -> str:
        """The construct as written, so the report echoes the real quote character."""
        return f"{self.quote}{{{{{self.name}}}}}{self.quote}"

    @property
    def suggestion(self) -> str:
        """The form that would have been correct."""
        return f"{{{{ quote({self.name.strip()}) }}}}"


def find_violations(text: str) -> list[Violation]:
    """Every single-quoted interpolation in `text`, in file order."""
    return [
        Violation(number, match.group(2), match.group(1))
        for number, line in enumerate(text.splitlines(), start=1)
        for match in _QUOTED_INTERPOLATION.finditer(line)
    ]


def main() -> int:
    """Report every violation and exit non-zero if there are any."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--justfile", type=Path, default=ROOT / "justfile")
    args = parser.parse_args()

    violations = find_violations(args.justfile.read_text(encoding="utf-8"))
    if not violations:
        return 0

    print("Quoted interpolations in the justfile:\n")
    for violation in violations:
        print(
            f"  {args.justfile.name}:{violation.line}"
            f"  {violation.found}  ->  {violation.suggestion}"
        )
    # ASCII only, deliberately. This runs as a child of `prek` with its stdout
    # piped, and on Windows a piped `sys.stdout` encodes with the locale codec.
    # A cp1252 em dash is byte 0x97, which a UTF-8 reader cannot decode: the
    # parent's reader thread dies and `result.stdout` becomes None, far from
    # the cause (#1986, #2004). Reproduce on any platform by running this
    # script with PYTHONIOENCODING=cp1252 and reading it back as UTF-8.
    print(
        f"\n{len(violations)} interpolation(s). Quotes do not quote a just"
        "\nsubstitution - a quote of the same kind inside the value closes the"
        " pair\nearly, so the value is either altered silently or breaks the"
        " recipe.\nUse `{{ quote(x) }}` (#2015)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
