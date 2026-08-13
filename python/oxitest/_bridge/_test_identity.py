"""The running test's identity, injectable into a function-lifetime fixture.

Separate from ``TestContext`` deliberately (#1879). ``TestContext`` answers
*where resolution is* and owns teardown registration, at every lifetime tier.
This type answers *which test*, which only has an answer at ``function``
lifetime — so it is refused at declaration time anywhere else, and the two
concerns never share a type that refuses half its surface at runtime.
"""

from __future__ import annotations

__all__ = ["TestIdentity"]

from typing import TYPE_CHECKING

from oxitest._bridge._fixture_type import injectable

if TYPE_CHECKING:
    from oxitest._bridge._test_meta import TestMeta


@injectable
class TestIdentity:
    """Identity of the test a ``function``-lifetime fixture is being built for.

    Declare it as a fixture parameter::

        @oxi.fixture(lifetime="function")
        def db_schema(test: TestIdentity) -> str:
            return f"test_{test.name}"

    Declaring it in a fixture of any wider lifetime is refused at registration:
    such a fixture is built once for whichever test arrives first, so "the
    current test" has no answer. Declaring it on a *test* is refused too — a
    test reads its own identity with ``oxi.current_test()`` (#1949).

    A fixture reached beneath a wider-lifetime consumer is refused as well. The
    consumer caches the value, so the fixture stops being per-test even though
    it declared ``lifetime="function"``.

    Examples:
        >>> from oxitest._bridge._test_identity import TestIdentity
        >>> from oxitest._bridge._test_meta import TestMeta
        >>> meta = TestMeta(
        ...     module_path="/t.py",
        ...     fn_name="test_x",
        ...     node_id="/t.py::test_x",
        ... )
        >>> TestIdentity(meta).name
        'test_x'

    """

    __test__ = False  # prevent pytest from treating this as a test class

    def __init__(self, meta: TestMeta) -> None:
        self._meta = meta

    @property
    def name(self) -> str:
        """Test function name (e.g. ``"test_create"``)."""
        return self._meta.fn_name

    @property
    def node_id(self) -> str:
        """Fully qualified node id (e.g. ``"tests/t.py::test_create"``)."""
        return self._meta.node_id

    @property
    def marks(self) -> frozenset[str]:
        """Marks applied to the running test."""
        return self._meta.markers

    @property
    def param_id(self) -> str | None:
        """Parametrize case id, or ``None`` for an unparametrized test."""
        return self._meta.kind.to_wire()
