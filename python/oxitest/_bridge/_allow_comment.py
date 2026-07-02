from __future__ import annotations

__all__ = ["parse_allow_rules"]

import re

_ALLOW_RE = re.compile(r"#\s*oxitest:\s*allow\[([^\]]*)\]")


def parse_allow_rules(line: str) -> frozenset[str]:
    """Extract rule names from ``# oxitest: allow[rule1, rule2]`` in a source line.

    Returns a frozenset of kebab-case rule names. Empty if no allow comment found.
    """
    match = _ALLOW_RE.search(line)
    if not match:
        return frozenset()
    raw = match.group(1).strip()
    if not raw:
        return frozenset()
    return frozenset(rule.strip() for rule in raw.split(","))
