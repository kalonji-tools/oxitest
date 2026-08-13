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
    #: Whether this bundle describes a *test*. ``False`` marks the synthetic
    #: bundles built for fixture resolution, where ``node_id``, ``markers`` and
    #: ``kind`` have no answer and ``fn_name`` names the fixture rather than
    #: any test (#1874). ``TestContext`` reads this and refuses the identity
    #: accessors rather than reporting a wrong-but-well-formed value.
    #:
    #: Not inferable from field emptiness: the fixture-to-fixture bundle
    #: carries a plausible non-empty ``fn_name``, which is exactly the case
    #: that returned ``"db_schema"`` where a test name belonged. And
    #: ``module_path``/``fn_name`` stay meaningful here — the first selects the
    #: module-lifetime scope bucket, the second prefixes ``TempDir`` — so this
    #: is a claim about identity only, not about the whole bundle.
    describes_a_test: bool = True
    #: Whether a ``TestIdentity`` resolved against this bundle may answer
    #: (#1879). True only for a ``function``-lifetime fixture that is not
    #: beneath a wider consumer. Deliberately NOT ``describes_a_test``: this
    #: bundle still describes no test, and flipping that flag would make
    #: ``TestContext.name`` answer inside a fixture, which #1874 exists to
    #: prevent. The identity itself is read ambiently, not carried here.
    identity_available: bool = False

    @property
    def param_id(self) -> str | None:
        """Legacy accessor — see kind for the sum-type source of truth."""
        return self.kind.to_wire()
