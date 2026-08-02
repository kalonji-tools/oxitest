from __future__ import annotations

__all__ = ["TestContext", "_BuiltinContext"]

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from oxitest._bridge._errors import TestIdentityUnavailableError
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

    Injected when a **test** parameter is annotated with ``TestContext``.
    Exposes that test's name, node id, module path, applied marks, and the
    current parametrize case value. Use :meth:`addfinalizer` (or its alias
    :meth:`on_teardown`) to register cleanup callbacks — all registered
    callbacks run after the test completes, in LIFO order, regardless of
    pass or fail.

    A **fixture** may also declare ``ctx: TestContext``, and there the promise
    is narrower. A fixture is resolved for whichever test arrives first at its
    lifetime tier, so "the current test" has no answer: at ``module`` lifetime
    and wider it is genuinely undefined, and even at ``function`` lifetime the
    identity is not threaded to the resolution site. So inside a fixture body
    :attr:`name`, :attr:`node_id`, :attr:`marks` and :attr:`param_id` raise
    ``TestIdentityUnavailableError`` (#1874) rather than reporting the
    fixture's own name as if it were the test's. :meth:`addfinalizer`,
    :meth:`on_teardown` and :attr:`module_path` work exactly as they do on a
    test — they are what ``ctx`` is for in a fixture.

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

    def _require_test_identity(self, accessed: str) -> None:
        """Refuse an identity read on a context that describes no test (#1874).

        The four accessors below are the whole reason ``TestContext`` exists,
        and inside a fixture body none of them has an answer. Returning one
        anyway failed silently and plausibly: ``f"test_{ctx.name}"`` yielded a
        well-formed string that looked per-test in every log line while being
        identical for the whole run.
        """
        if not self._meta.describes_a_test:
            raise TestIdentityUnavailableError(accessed)

    @property
    def name(self) -> str:
        """Test function name (e.g. ``"test_create"``)."""
        self._require_test_identity("name")
        return self._meta.fn_name

    @property
    def module_path(self) -> str:
        """Absolute filesystem path to the test module.

        Unguarded, unlike the identity accessors: this is *where resolution
        is*, not *who the test is*, and it is correct inside a fixture body —
        it is the same value that selects the module-lifetime scope bucket.
        """
        return self._meta.module_path

    @property
    def node_id(self) -> str:
        """Full qualified test ID (e.g. ``"tests/test_db.py::test_create[case_a]"``)."""
        self._require_test_identity("node_id")
        return self._meta.node_id

    @property
    def param_id(self) -> str | None:
        """Parametrize case ID string, or ``None`` for non-parametrized tests."""
        self._require_test_identity("param_id")
        return self._meta.param_id

    @property
    def marks(self) -> frozenset[str]:
        """All mark names applied to this test (e.g. ``frozenset({"slow"})``).

        Includes both built-in marks (``skip``, ``xfail``, ``timeout``)
        and custom marks.
        """
        self._require_test_identity("marks")
        return self._meta.markers

    @property
    def param(self) -> Any:
        """Always ``None``. Use :attr:`param_id` to identify the current case.

        This is not a "not parametrized" answer — it is ``None`` for
        parametrized tests too. ``_param`` is assigned once in ``__init__`` and
        never written again, on any path.

        Not populated rather than removed: the case *value* has no single
        meaning across the two parametrize forms. A dataclass case is one
        object, but a dict case (``@oxi.parametrize(one={"n": 1})``) is a
        mapping that is spread across several parameters, and the test already
        receives each of them by name. Deciding what one attribute should
        return for both — and threading it from ``resolve_parametrize`` through
        ``resolve_for_test`` to reach here — is a design change, not a bug fix.
        Until someone needs it, saying so beats a second silent ``None``.
        """
        return self._param

    def addfinalizer(self, fn: Callable[[], None]) -> None:
        """Register a cleanup function to run after this test or fixture completes."""
        self._teardown_stack.append(fn)

    #: Beginner-friendly alias for addfinalizer.
    on_teardown = addfinalizer
