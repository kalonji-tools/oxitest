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
]

import inspect
from collections.abc import Callable
from typing import (
    Any,
    TypeVar,
    overload,
)

from oxitest._bridge._errors import (  # noqa: F401
    FixtureCycleError as FixtureCycleError,
    FixtureNotFoundError as FixtureNotFoundError,
    FixtureSetupError as FixtureSetupError,
    UnannotatedFixtureParamError as UnannotatedFixtureParamError,
)
from oxitest._bridge._fixture_registry import (
    FixtureDef as FixtureDef,
    FixtureRegistry as FixtureRegistry,
    _fixture_inner_type as _fixture_inner_type,
    _fixture_ref_inner_type as _fixture_ref_inner_type,
)
from oxitest._bridge._fixture_session import (
    BuiltinFixture as BuiltinFixture,
    FixtureSession as FixtureSession,
    FixtureTeardownWarning as FixtureTeardownWarning,
    _instantiation_context,
    _Node as _Node,
    _Scope as _Scope,
    _SessionProtocol as _SessionProtocol,
    _teardown_local,
    _TestContext as _TestContext,
    _warn_teardown as _warn_teardown,
)
from oxitest._bridge._fn_metadata import get_metadata, get_or_create

_F = TypeVar("_F", bound=Callable[..., Any])


class FixtureAccessor:
    """Returned by ``Fixtures.__getattr__``; serves two roles:

    1. **FixtureRef target** — carries ``_oxitest_fixture_name`` and wraps the
       underlying fixture function (``_fa_func``) so the executor can resolve it
       as a callable fixture reference in ``@oxitest.parametrize``.  The
       namespace is looked up via ``FixtureDef.namespace`` in the registry.

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
        self._oxitest_fixture_name: str = get_metadata(func).fixture_name or name
        self.__name__ = name

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
        session, module_path = ctx
        try:
            fn_teardowns: list[Callable[[], None]] = _teardown_local.fn_teardowns  # type: ignore[attr-defined]
        except AttributeError:
            raise RuntimeError(
                "FixtureAccessor attribute access occurred outside an active "
                "resolve_for_test call. This is a bug in the test framework."
            ) from None
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

    def fixture(
        self,
        fn: _F | None = None,
        *,
        autouse: bool = False,
        name: str | None = None,
        shared: bool = False,
    ):
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
            fixture_name = name or getattr(f, "__name__", repr(f))
            get_or_create(f).fixture_name = fixture_name
            defn = FixtureDef(
                name=fixture_name,
                func=f,
                autouse=autouse,
                params=None,
                conftest_path="",
                shared=shared,
                is_async=(
                    inspect.iscoroutinefunction(f) or inspect.isasyncgenfunction(f)
                ),
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
