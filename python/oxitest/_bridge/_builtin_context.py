from __future__ import annotations

__all__ = ["TestContext", "_BuiltinContext"]

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from oxitest._bridge._fixture_type import injectable

if TYPE_CHECKING:
    from oxitest._bridge._test_meta import TestMeta

from oxitest._bridge.plugin_loader import PluginRegistry


@dataclass(frozen=True, slots=True)
class _BuiltinContext:
    """Passed to BuiltinFixture.create() — carries injection-site metadata."""

    meta: TestMeta
    inject_scope: str  # "function" for test-level injections
    teardown_stack: list[Callable[[], None]]
    plugin_registry: PluginRegistry = field(default_factory=PluginRegistry, repr=False)
    keep_tmp: str = "cleanup"
    result_cell: list[Any] = field(default_factory=list, repr=False)

    @property
    def module_path(self) -> str:
        return self.meta.module_path

    @property
    def fn_name(self) -> str:
        return self.meta.fn_name


@injectable
class TestContext:
    """Test identity metadata and imperative teardown registration.

    Injected when a test parameter is annotated with ``TestContext``. Exposes
    the current test's name, node id, module path, applied marks, and the
    current parametrize case value. Use :meth:`addfinalizer` (or its alias
    :meth:`on_teardown`) to register cleanup callbacks — all registered
    callbacks run after the test completes, in LIFO order, regardless of
    pass or fail.

    See Also:
        - :class:`Patcher` — for scoped attribute / env / cwd overrides
          with automatic restoration.

    Examples:
        Injected by parameter type — declare ``ctx: TestContext`` on
        the test::

            def test_example(ctx: TestContext) -> None:
                ctx.name       # "test_example"
                ctx.node_id    # "tests/test_example.py::test_example"
                ctx.marks      # frozenset({"slow"})
                ctx.addfinalizer(resource.close)

        For illustration, direct construction with a synthetic
        :class:`TestMeta`:

        >>> from oxitest import TestContext
        >>> from oxitest._bridge._test_meta import TestMeta
        >>> meta = TestMeta(
        ...     module_path="/t.py",
        ...     fn_name="test_x",
        ...     node_id="/t.py::test_x",
        ... )
        >>> teardown = []
        >>> ctx = TestContext(meta, teardown)
        >>> ctx.name
        'test_x'
        >>> ctx.node_id
        '/t.py::test_x'
        >>> ctx.addfinalizer(lambda: None)
        >>> len(teardown)
        1

    """

    __test__ = False  # prevent pytest from treating this as a test class

    def __init__(
        self,
        meta: TestMeta,
        teardown_stack: list[Callable[[], None]],
    ) -> None:
        self._meta = meta
        self._param: Any = None
        self._teardown_stack = teardown_stack

    @property
    def name(self) -> str:
        """Test function name (e.g. ``"test_create"``)."""
        return self._meta.fn_name

    @property
    def module_path(self) -> str:
        """Absolute filesystem path to the test module."""
        return self._meta.module_path

    @property
    def node_id(self) -> str:
        """Full qualified test ID (e.g. ``"tests/test_db.py::test_create[case_a]"``)."""
        return self._meta.node_id

    @property
    def param_id(self) -> str | None:
        """Parametrize case ID string, or ``None`` for non-parametrized tests."""
        return self._meta.param_id

    @property
    def marks(self) -> frozenset[str]:
        """All mark names applied to this test (e.g. ``frozenset({"slow"})``).

        Includes both built-in marks (``skip``, ``xfail``, ``timeout``)
        and custom marks.
        """
        return self._meta.markers

    @property
    def param(self) -> Any:
        """Current parametrize case value, or ``None`` if not parametrized."""
        return self._param

    def addfinalizer(self, fn: Callable[[], None]) -> None:
        """Register a cleanup function to run after this test or fixture completes."""
        self._teardown_stack.append(fn)

    #: Beginner-friendly alias for addfinalizer.
    on_teardown = addfinalizer
