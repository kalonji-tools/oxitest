from __future__ import annotations

__all__ = [
    # Public API
    "Fixtures",
    "FixtureDef",
    "FixtureRegistry",
    "FixtureSession",
    "FixtureTeardownWarning",
    "UnannotatedFixtureParamError",
    # Internal bridge protocol (used by executor and loader)
    "_SessionProtocol",
    "_fixture_inner_type",
    "_fixture_ref_inner_type",
    "_Node",
    "_Scope",
    "MarkInfo",
]

import inspect
import warnings
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import (
    Annotated,
    Any,
    Generic,
    NamedTuple,
    Protocol,
    TypeVar,
    get_args,
    get_origin,
    overload,
)

from oxitest._bridge._exceptions import (  # noqa: F401
    FixtureCycleError as FixtureCycleError,
    FixtureNotFoundError as FixtureNotFoundError,
    FixtureSetupError as FixtureSetupError,
    UnannotatedFixtureParamError as UnannotatedFixtureParamError,
)
from oxitest._bridge._fixture_type import _FixtureMarker, _FixtureRefMarker
from oxitest._bridge._loader import ModuleCache
from oxitest._bridge._marks import (  # noqa: F401
    MarkInfo as MarkInfo,
    _append_mark as _append_mark,  # type: ignore[reportPrivateUsage]
    mark as mark,
    skip as skip,
)
from oxitest._bridge._metadata import get_type_hints_cached as _get_hints

T = TypeVar("T")
_F = TypeVar("_F", bound=Callable[..., Any])

# Contextvar set in _instantiate so FixtureAccessor can resolve lazily
# inside fixture bodies. Holds (session, module_path, fn_teardowns).
_instantiation_context: ContextVar[tuple[Any, str, list[Callable[[], None]]] | None] = (
    ContextVar("_instantiation_context", default=None)
)

# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class FixtureDef(Generic[T]):
    name: str
    func: Callable[..., T]
    autouse: bool
    params: list[Any] | None
    conftest_path: str  # which conftest registered this (for locality precedence)
    shared: bool = False  # True = session-lifetime, immutable (FrozenProxy-wrapped)
    namespace: str = ""  # Fixtures() instance name; empty = no namespace


class FixtureRegistry:
    def __init__(self) -> None:
        # name → list of FixtureDef, ordered from root conftest to leaf conftest
        self._defs: dict[str, list[FixtureDef[Any]]] = {}
        self._namespaces: set[str] = set()  # O(1) namespace existence check

    def register(self, defn: FixtureDef[Any]) -> None:
        self._defs.setdefault(defn.name, []).append(defn)
        if defn.namespace:
            self._namespaces.add(defn.namespace)

    def get(self, name: str) -> FixtureDef[Any] | None:
        """Return the most-local (last-registered) FixtureDef for name."""
        defs = self._defs.get(name)
        return defs[-1] if defs else None

    def get_autouse(self) -> list[FixtureDef[Any]]:
        """Return all autouse fixtures (most-local version of each name)."""
        return [defs[-1] for defs in self._defs.values() if defs and defs[-1].autouse]

    def get_in_namespace(self, name: str, namespace: str) -> FixtureDef[Any] | None:
        """Return the most-local FixtureDef for name within the given namespace."""
        defs = self._defs.get(name)
        if not defs:
            return None
        for defn in reversed(defs):
            if defn.namespace == namespace:
                return defn
        return None

    def has_namespace(self, namespace: str) -> bool:
        """Return True if any registered fixture belongs to the given namespace."""
        return namespace in self._namespaces


class FixtureAccessor:
    """Returned by ``Fixtures.__getattr__``; serves two roles:

    1. **FixtureRef target** — carries ``_oxitest_fixture_name`` and
       ``_oxitest_namespace`` so the executor can resolve it as a callable
       fixture reference in ``@oxitest.parametrize``.

    2. **Lazy attribute proxy** — when attribute access happens *inside* a
       test or fixture body, it resolves the live fixture instance via the
       ``_instantiation_context`` contextvar and proxies the attribute.

    Example::

        # conftest.py
        kvault = Fixtures()

        @kvault.fixture
        def store() -> KVault: ...

        @kvault.fixture
        def ns() -> Namespace:
            return kvault.store.namespace("test")  # lazy: resolves live store

        # test_query.py
        @oxitest.parametrize(
            memory=BackendCase(backend=kvault.store, ...),  # FixtureRef
        )
        def test_foo(backend: Fixture[KVault]) -> None: ...
    """

    def __init__(self, name: str, fixtures: Fixtures, func: Callable[..., Any]) -> None:
        self._fa_name = name
        self._fa_fixtures = fixtures
        self._fa_func = func
        # Mirror what _register sets so the executor can read them directly.
        self._oxitest_fixture_name: str = getattr(func, "_oxitest_fixture_name", name)
        self.__name__ = name

    @property
    def _oxitest_namespace(self) -> str | None:
        """Dynamic: reflects the namespace even if stamped after creation."""
        ns = self._fa_fixtures._namespace_name
        return ns or None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._fa_func(*args, **kwargs)

    def __getattr__(self, attr: str) -> Any:
        if attr.startswith("_"):
            raise AttributeError(attr)
        ctx = _instantiation_context.get(None)
        if ctx is None:
            raise AttributeError(
                f"Attribute '{attr}' of fixture '{self._fa_name}' can only be accessed "
                f"inside a test or fixture body (no active instantiation context). "
                f"If you meant to use the fixture value, annotate with "
                f"fx: Fixtures and access via fx.<namespace>.{self._fa_name}.{attr}."
            )
        session, module_path, fn_teardowns = ctx
        namespace = self._fa_fixtures._namespace_name
        if namespace:
            resolved = session.get_fixture_in_namespace(
                self._oxitest_fixture_name, namespace, module_path, fn_teardowns
            )
        else:
            resolved = session.get_fixture(
                self._oxitest_fixture_name, module_path, fn_teardowns
            )
        return getattr(resolved, attr)


class Fixtures:
    """Instance-based fixture registry. Create one per conftest.py.

    The optional ``name`` parameter sets the namespace name used when accessing
    fixtures via ``fx: Fixtures`` (e.g. ``fx.db.conn``). If omitted, the name
    is derived from the variable name in conftest.py (``db = Fixtures()`` →
    namespace ``"db"``).

    Usage:
        fixtures = Fixtures()

        @fixtures.fixture
        def my_db(): ...

        @fixtures.fixture
        def my_client(my_db): ...
    """

    def __init__(self, name: str | None = None) -> None:
        self._defs: list[FixtureDef[Any]] = []
        self._defs_by_name: dict[str, FixtureDef[Any]] = {}
        self._namespace_name: str = name or ""

    @overload
    def fixture(self, fn: _F) -> _F: ...

    @overload
    def fixture(
        self,
        fn: None = None,
        *,
        autouse: bool = False,
        name: str | None = None,
        shared: bool = False,
    ) -> Callable[[_F], _F]: ...

    def fixture(self, fn=None, *, autouse=False, name=None, shared=False):
        """Register a fixture function with this registry.

        Usage (bare decorator)::

            @fixtures.fixture
            def my_db() -> Database:
                return Database()

        Usage (with options)::

            @fixtures.fixture(autouse=False)
            def my_client(my_db: Fixture[Database]) -> Client:
                return Client(my_db)

        **Teardown:** Yield once to provide the fixture value. Code **after** the
        yield runs as teardown — it executes after every test that used this fixture,
        regardless of pass or fail::

            @fixtures.fixture
            def workspace(tmp_path: Fixture[Path]) -> Generator[Path, None, None]:
                yield tmp_path          # setup: value given to the test
                shutil.rmtree(tmp_path) # teardown: runs after the test

        **Return types:** Prefer a typed return over a plain dict for fixtures that
        produce multiple values. A ``dict`` return is opaque at the call site —
        ``Fixture[dict]`` tells a reader nothing about available keys. A dataclass
        or TypedDict makes the contract visible::

            from dataclasses import dataclass

            @dataclass
            class WorkspaceEnv:
                root: Path
                log: Path

            @fixtures.fixture
            def workspace_env(tmp: Fixture[Path]) -> WorkspaceEnv:
                return WorkspaceEnv(root=tmp, log=tmp / "run.log")

        At the call site, ``workspace_env: Fixture[WorkspaceEnv]`` gives IDE
        completion on ``workspace_env.root`` and ``workspace_env.log``, and
        type checkers enforce the field types without a plugin.

        Args:
            autouse: If ``True``, fixture runs for every test without being
                explicitly requested.
            name: Override the fixture name. Defaults to the function name.
            shared: If ``True``, fixture is session-lifetime and immutable
                (wrapped with ``FrozenProxy``).
        """

        def _register(f: _F) -> _F:
            fixture_name = name or f.__name__
            setattr(f, "_oxitest_fixture_name", fixture_name)
            defn = FixtureDef(
                name=fixture_name,
                func=f,
                autouse=autouse,
                params=None,
                conftest_path="",
                shared=shared,
            )
            self._defs.append(defn)
            self._defs_by_name[fixture_name] = defn
            return f

        return _register(fn) if fn is not None else _register

    def __getattr__(self, name: str) -> FixtureAccessor:
        """Return a FixtureAccessor for the named fixture.

        Enables both FixtureRef usage in ``@oxitest.parametrize`` and lazy
        attribute-proxying inside fixture/test bodies::

            backend=kvault.store          # FixtureRef: resolves at test time
            kvault.store.namespace("x")   # lazy: proxied via contextvar
        """
        if name.startswith("_"):
            raise AttributeError(name)
        defn = self._defs_by_name.get(name)
        if defn is not None:
            return FixtureAccessor(name, self, defn.func)
        available = [d.name for d in self._defs]
        raise AttributeError(
            f"'{type(self).__name__}' has no registered fixture '{name}'. "
            f"Available: {available}"
        )


class _SessionProtocol(Protocol):
    def resolve_for_test(
        self,
        fn: Callable[..., Any],
        module_path: str,
        *,
        skip_names: frozenset[str] = frozenset(),
    ) -> tuple[dict[str, Any], list[Callable[[], None]]]: ...

    def get_fixture(
        self,
        name: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
    ) -> Any: ...

    def get_fixture_in_namespace(
        self,
        name: str,
        namespace: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
    ) -> Any: ...


# ── _TestContext ──────────────────────────────────────────────────────────────


class _Node(NamedTuple):
    """Minimal test-node info threaded through _TestContext."""

    fn_name: str
    module_path: str


class _TestContext:
    """Provides imperative teardown registration for a single test.

    Injected when a test parameter is annotated with ``TestContext``::

        def test_example(ctx: TestContext) -> None:
            resource = acquire()
            ctx.addfinalizer(resource.close)
            ...

    Use ``addfinalizer`` (or its alias ``on_teardown``) to register cleanup
    callbacks. All registered callbacks run after the test completes, in LIFO
    order, regardless of pass or fail.
    """

    __test__ = False  # prevent pytest from treating this as a test class

    def __init__(
        self,
        node: _Node,
        teardown_stack: list[Callable[[], None]],
    ) -> None:
        self.node = node
        self.param: Any = None
        self._teardown_stack = teardown_stack

    def addfinalizer(self, fn: Callable[[], None]) -> None:
        """Register a cleanup function to run after this test or fixture completes."""
        self._teardown_stack.append(fn)

    #: Beginner-friendly alias for addfinalizer.
    on_teardown = addfinalizer


# ── BuiltinFixture (imported after _TestContext to avoid circular import) ─────
# _builtins/__init__.py imports _TestContext from this module, so this import
# must come after _TestContext is defined.
from oxitest._bridge._builtins._base import (  # noqa: E402
    BuiltinFixture as BuiltinFixture,
    _BuiltinContext,
)

# ── FixtureSession ────────────────────────────────────────────────────────────


class FixtureTeardownWarning(UserWarning):
    """Emitted when an exception occurs inside a yield-fixture teardown.

    Captured by ``WarnCapture`` when a test annotates ``warn: WarnCapture``.
    """


def _warn_teardown(name: str, exc: Exception) -> None:
    if name:
        msg = f"error in teardown of fixture '{name}': {exc}"
    else:
        msg = f"error during teardown: {exc}"
    warnings.warn(FixtureTeardownWarning(msg), stacklevel=2)


def _safe_call(fn: Callable[[], None], name: str = "") -> None:
    try:
        fn()
    except Exception as exc:
        _warn_teardown(name, exc)


def _fixture_inner_type(hint: Any) -> tuple[bool, Any]:
    """Return (is_fixture, inner_type). is_fixture is True iff hint is Fixture[T]."""
    if get_origin(hint) is not Annotated:
        return False, None
    inner, *meta = get_args(hint)
    if not any(isinstance(m, _FixtureMarker) for m in meta):
        return False, None
    return True, inner


def _fixture_ref_inner_type(hint: Any) -> tuple[bool, Any]:
    """Return (is_fixture_ref, inner_type). True iff hint is FixtureRef[T]."""
    if get_origin(hint) is not Annotated:
        return False, None
    inner, *meta = get_args(hint)
    if not any(isinstance(m, _FixtureRefMarker) for m in meta):
        return False, None
    return True, inner


@dataclass
class _Scope:
    """A single fixture scope: a cache dict and its associated teardown stack."""

    cache: dict[str, Any] = field(default_factory=dict)
    teardowns: list[Callable[[], None]] = field(default_factory=list)

    def get_or_create(self, name: str, factory: Callable[[], Any]) -> Any:
        if name not in self.cache:
            self.cache[name] = factory()
        return self.cache[name]

    def drain(self) -> None:
        """Run teardowns in reverse, then clear the stack."""
        for fn in reversed(self.teardowns):
            _safe_call(fn)
        self.teardowns.clear()


class FixtureSession:
    def __init__(self, registry: FixtureRegistry) -> None:
        self._registry = registry
        self._session_scope = _Scope()
        self._shared_scope = (
            _Scope()
        )  # shared=True fixtures — init once, drain at end_session
        self._module_cache = ModuleCache()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def begin_module(self, module_path: str) -> None:
        # Rust no longer calls this; kept as a no-op for test-suite compatibility.
        pass

    def end_module(self, module_path: str) -> None:
        self._module_cache.evict(module_path)

    def end_session(self) -> None:
        # Drain shared (user fixtures) before session (builtins like TempDirFactory)
        # so that shared fixture teardowns can still access session-scoped builtins.
        self._shared_scope.drain()
        self._session_scope.drain()

    def _inject_builtin(
        self,
        impl_cls: type[BuiltinFixture],
        module_path: str,
        inject_scope: str,
        teardown_stack: list[Callable[[], None]],
        fn_name: str = "",
    ) -> Any:
        """Create and return a built-in fixture value, respecting its declared scope."""
        if impl_cls.scope == "session":
            return self._session_scope.get_or_create(
                f"__builtin_{impl_cls.__name__}",
                lambda: impl_cls().create(
                    _BuiltinContext(
                        module_path=module_path,
                        inject_scope="session",
                        teardown_stack=self._session_scope.teardowns,
                    )
                ),
            )
        return impl_cls().create(
            _BuiltinContext(
                module_path=module_path,
                inject_scope=inject_scope,
                teardown_stack=teardown_stack,
                fn_name=fn_name,
            )
        )

    def _resolve_param(
        self,
        param_name: str,
        hint: Any,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        fn_name: str,
        resolve_user_fixture: Callable[[str], Any],
    ) -> tuple[bool, Any]:
        """Resolve a single parameter by its type hint.

        Returns (resolved, value) where resolved=True means the value should be
        injected for this parameter. Returns (False, None) if the hint is not
        injectable (not Fixture[T], not bare Fixtures).
        """
        if hint is Fixtures:
            from oxitest._bridge.proxy_ns import FixturesProxy

            return True, FixturesProxy(self, module_path, fn_teardowns, fn_name=fn_name)
        is_fx, inner = _fixture_inner_type(hint)
        if not is_fx:
            return False, None
        impl_cls = BuiltinFixture.for_type(inner)
        if impl_cls is not None:
            return True, self._inject_builtin(
                impl_cls, module_path, "function", fn_teardowns, fn_name=fn_name
            )
        return True, resolve_user_fixture(param_name)

    # ── Resolution ────────────────────────────────────────────────────────────

    def resolve_for_test(
        self,
        fn: Callable[..., Any],
        module_path: str,
        *,
        skip_names: frozenset[str] = frozenset(),
    ) -> tuple[dict[str, Any], list[Callable[[], None]]]:
        """Return (fixture_kwargs, fn_teardowns) for calling fn(**fixture_kwargs).

        Only parameters annotated with Fixture[T] are resolved. Parameters without
        that annotation (plain types, parametrize values) are skipped. Parameters
        named in skip_names are skipped even if annotated with Fixture[T].
        """
        fn_teardowns: list[Callable[[], None]] = []
        hints = _get_hints(fn)
        fn_name = getattr(fn, "__name__", "")

        # Collect names of explicitly-requested fixtures (to skip in autouse check)
        requested_names: set[str] = set()
        for param_name, hint in hints.items():
            if param_name == "return":
                continue
            is_fx, inner = _fixture_inner_type(hint)
            if (
                is_fx
                and BuiltinFixture.for_type(inner) is None
                and param_name not in skip_names
            ):
                requested_names.add(param_name)

        # Autouse: run for side effects; value NOT injected unless explicitly requested
        for defn in self._registry.get_autouse():
            if defn.name not in requested_names:
                self.get_fixture(defn.name, module_path, fn_teardowns)

        # Resolve Fixture[T]-annotated parameters
        kwargs: dict[str, Any] = {}
        for param_name, hint in hints.items():
            if param_name == "return":
                continue
            if param_name in skip_names:
                continue
            resolved, value = self._resolve_param(
                param_name,
                hint,
                module_path,
                fn_teardowns=fn_teardowns,
                fn_name=fn_name,
                resolve_user_fixture=lambda n: self.get_fixture(
                    n, module_path, fn_teardowns
                ),
            )
            if resolved:
                kwargs[param_name] = value

        # Check for unannotated params whose name matches a known fixture
        for param_name in inspect.signature(fn).parameters:
            if param_name in skip_names or param_name in kwargs:
                continue
            hint = hints.get(param_name)
            is_fx = _fixture_inner_type(hint)[0] if hint is not None else False
            if not is_fx and self._registry.get(param_name) is not None:
                raise UnannotatedFixtureParamError(
                    param_name, getattr(fn, "__name__", repr(fn))
                )

        return kwargs, fn_teardowns

    def get_fixture(
        self, name: str, module_path: str, fn_teardowns: list[Callable[[], None]]
    ) -> Any:
        return self._resolve_fixture(name, module_path, fn_teardowns, frozenset())

    def get_fixture_in_namespace(
        self,
        name: str,
        namespace: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
    ) -> Any:
        defn = self._registry.get_in_namespace(name, namespace)
        if defn is None:
            raise FixtureNotFoundError(name, namespace=namespace)
        # NOTE: bypasses the cycle-detection entry guard in _resolve_fixture;
        # self-referential namespace fixtures are not caught here. Acceptable
        # trade-off — such fixtures are nonsensical and unsupported.
        return self._resolve_fixture_defn(
            defn, module_path, fn_teardowns, frozenset({name})
        )

    def _resolve_fixture(
        self,
        name: str,
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        resolving: frozenset[str],
    ) -> Any:
        if name in resolving:
            raise FixtureCycleError(name, set(resolving))
        defn = self._registry.get(name)
        if defn is None:
            raise FixtureNotFoundError(name)
        return self._resolve_fixture_defn(
            defn, module_path, fn_teardowns, resolving | {name}
        )

    def _resolve_fixture_defn(
        self,
        defn: FixtureDef[Any],
        module_path: str,
        fn_teardowns: list[Callable[[], None]],
        resolving: frozenset[str],
    ) -> Any:
        if defn.shared:
            s = self._shared_scope
            if defn.name in s.cache:
                return s.cache[defn.name]
            from oxitest._bridge.proxy import FrozenProxy

            def _make_shared(
                defn: FixtureDef[Any] = defn,
                s: _Scope = s,
                resolving: frozenset[str] = resolving,
            ) -> FrozenProxy:
                return FrozenProxy(
                    self._instantiate(
                        defn, module_path, s.teardowns, fn_teardowns, resolving
                    )
                )

            return s.get_or_create(defn.name, _make_shared)

        return self._instantiate(
            defn, module_path, fn_teardowns, fn_teardowns, resolving
        )

    def _instantiate(
        self,
        defn: FixtureDef[Any],
        module_path: str,
        scope_teardowns: list[Callable[[], None]],
        fn_teardowns: list[Callable[[], None]],
        resolving: frozenset[str],
    ) -> Any:
        hints = _get_hints(defn.func)
        deps: dict[str, Any] = {}
        for param_name, hint in hints.items():
            if param_name == "return":
                continue
            resolved, value = self._resolve_param(
                param_name,
                hint,
                module_path,
                fn_teardowns=fn_teardowns,
                fn_name=defn.name,
                resolve_user_fixture=lambda n: self._resolve_fixture(
                    n, module_path, fn_teardowns, resolving
                ),
            )
            if resolved:
                deps[param_name] = value

        # Set instantiation context so FixtureAccessor attribute access
        # (e.g. kvault.store.namespace("x") inside a fixture body) can
        # resolve the live fixture instance via _instantiation_context.
        token = _instantiation_context.set((self, module_path, fn_teardowns))
        try:
            result = defn.func(**deps)
            _is_gen = inspect.isgenerator(result)
            if _is_gen:
                value = next(result)
            else:
                value = result
        except Exception as exc:
            raise FixtureSetupError(defn.name, exc) from exc
        finally:
            _instantiation_context.reset(token)

        if _is_gen:
            fixture_name = defn.name

            def teardown(gen=result, n=fixture_name):  # type: ignore[misc]
                try:
                    next(gen)
                except StopIteration:
                    pass
                except Exception as exc:
                    _warn_teardown(n, exc)

            scope_teardowns.append(teardown)
            return value

        return result


# Trigger built-in fixture registrations by importing the _builtins package.
# This import is deferred to avoid circular imports — _builtins/__init__.py
# imports _TestContext from this module, so it must be imported after _TestContext
# is defined. The import is at module scope so it runs once on first load.
import oxitest._bridge._builtins  # noqa: F401, E402
