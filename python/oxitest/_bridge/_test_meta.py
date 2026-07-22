"""Test identity metadata bundle.

Internal to ``_bridge/`` — threaded through the call chain from
``run_test()`` → ``resolve_for_test()`` → ``_inject_builtin()`` →
``_BuiltinContext`` → ``TestContext``.
"""

from __future__ import annotations

__all__ = ["TestMeta"]

from dataclasses import dataclass, field

from oxitest._bridge._test_kind import Solitary, TestKind


@dataclass(frozen=True, slots=True)
class TestMeta:
    """Immutable bundle of test identity fields."""

    module_path: str
    fn_name: str
    node_id: str
    kind: TestKind = field(default_factory=Solitary)
    markers: frozenset[str] = frozenset()

    @property
    def param_id(self) -> str | None:
        """Legacy accessor — see kind for the sum-type source of truth."""
        return self.kind.to_wire()
