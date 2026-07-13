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
    plugin_registry: PluginRegistry | None = field(default=None, repr=False)
    keep_tmp: str | None = None
    result_cell: list[Any] | None = field(default=None, repr=False)

    @property
    def module_path(self) -> str:
        return self.meta.module_path

    @property
    def fn_name(self) -> str:
        return self.meta.fn_name


@injectable
class TestContext:
    """Test identity metadata and imperative teardown registration.

    Injected when a test parameter is annotated with `TestContext`::

        def test_example(ctx: TestContext) -> None:
            ctx.name       # "test_example"
            ctx.node_id    # "tests/test_example.py::test_example"
            ctx.marks      # frozenset({"slow"})
            ctx.addfinalizer(resource.close)

    Use `addfinalizer` (or its alias `on_teardown`) to register cleanup
    callbacks. All registered callbacks run after the test completes, in LIFO
    order, regardless of pass or fail.
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
    def param_id(self) -> str:
        """Parametrize case ID string, or ``""`` for non-parametrized tests."""
        return self._meta.param_id

    @property
    def marks(self) -> frozenset[str]:
        """All mark names applied to this test (e.g. ``frozenset({"slow"})``).

        Includes both built-in marks (``skip``, ``xfail``, ``timeout``,
        ``usefixtures``) and custom marks.
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
