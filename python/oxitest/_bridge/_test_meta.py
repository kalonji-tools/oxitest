"""Test identity metadata bundle.

Internal to ``_bridge/`` — threaded through the call chain from
``run_test()`` → ``resolve_for_test()`` → ``_inject_builtin()`` →
``_BuiltinContext`` → ``TestContext``.
"""

from __future__ import annotations

__all__ = ["TestMeta"]

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TestMeta:
    """Immutable bundle of test identity fields."""

    module_path: str
    fn_name: str
    node_id: str
    param_id: str = ""
    markers: frozenset[str] = frozenset()
