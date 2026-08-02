"""User-facing fixture registration API.

``Fixtures`` is the instance-based fixture registry that users create in
``conftest.py``.  ``FixtureAccessor`` is the lazy proxy returned by
``Fixtures.__getattr__``, supporting both ``FixtureRef`` usage in
``@oxitest.parametrize`` and attribute-proxying inside test/fixture bodies.

These classes depend on ``_fixture_context`` (for ContextVar access) and
``_fixture_registry`` (for ``FixtureDef``).  They do **not** import from
``_fixture_session`` — the session module re-exports them for backward
compatibility.
"""

from __future__ import annotations

__all__ = [
    "FixtureAccessor",
    "Fixtures",
]

import inspect
from collections.abc import Callable
from typing import Any, TypeVar, overload

from oxitest._bridge._errors import AutouseRegistrationError
from oxitest._bridge._fixture_context import _fixture_context
from oxitest._bridge._fixture_registry import ConftestSource, FixtureDef, FixtureScope
from oxitest._bridge._fn_metadata import _update, get_metadata

_F = TypeVar("_F", bound=Callable[..., Any])


class FixtureAccessor:
    """Returned by `Fixtures.__getattr__`; serves two roles.

    1. **FixtureRef target** — carries `_oxitest_fixture_name` and wraps the
       underlying fixture function (`_fa_func`) so the executor can resolve it
       as a callable fixture reference in `@oxitest.parametrize`.  The
       namespace is looked up via `FixtureDef.namespace` in the registry.

    2. **Lazy attribute proxy** — when attribute access happens *inside* a
       test or fixture body, it resolves the live fixture instance via the
       `_fixture_context` contextvar and proxies the attribute.

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

    @property
    def fixture_name(self) -> str:
        """The canonical fixture name used for executor resolution."""
        return self._oxitest_fixture_name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._fa_func(*args, **kwargs)

    def __getattr__(self, attr: str) -> Any:
        if attr.startswith("_"):
            raise AttributeError(attr)
        ctx = _fixture_context.get(None)
        if ctx is None:
            msg = (
                f"Attribute '{attr}' of fixture '{self._fa_name}' can only be accessed "
                f"inside a test or fixture body (no active instantiation context). "
                f"If you meant to use the fixture value, annotate with "
                f"fx: Fixtures and access via fx.<namespace>.{self._fa_name}.{attr}."
            )
            raise AttributeError(msg)
        session = ctx.session
        module_path = ctx.module_path
        fn_teardowns = ctx.fn_teardowns
        namespace = self._fa_fixtures.namespace_name
        if namespace:
            resolved = session.get_fixture_in_namespace(
                self._oxitest_fixture_name,
                namespace,
                module_path,
                fn_teardowns,
                # Asserted, not derived: this accessor resolves out of a
                # ContextVar that carries no test kind. It is not the hole
                # #1876 closed, because the next line does
                # ``getattr(resolved, attr)`` — an async fixture therefore
                # arrives as an AsyncFixtureHandle and fails on the attribute
                # with "await it before use", which is true whichever kind the
                # test is. Passing False would print "cannot be used by a sync
                # test" at an async test.
                test_is_async=True,
            )
        else:
            resolved = session.get_fixture_by_name(
                self._oxitest_fixture_name, module_path, fn_teardowns
            )
        return getattr(resolved, attr)


class Fixtures:
    """Instance-based fixture registry. Create one per conftest.py.

    Fixtures are setup routines that produce values injected into tests via
    the :class:`Fixture` annotation. Register a callable with
    :meth:`fixture` — either as a bare decorator or with options
    (``autouse``, ``shared``, ``name``) — and the registered function
    becomes discoverable at test-collection time.

    The optional ``name`` parameter on ``Fixtures()`` sets the namespace
    name used when accessing fixtures via ``fx: Fixtures`` (e.g.
    ``fx.db.conn``). If omitted, the name is derived from the variable
    name in conftest.py (``db = Fixtures()`` → namespace ``"db"``).

    See Also:
        - :class:`Fixture` — the injection-signal annotation.
        - :class:`Yields` — return-type annotation for yield fixtures.

    Examples:
        Register fixtures on a module-level ``fixtures`` instance in
        ``conftest.py``:

        >>> from oxitest import Fixtures
        >>> fixtures = Fixtures()
        >>> @fixtures.fixture
        ... def db() -> str:
        ...     return "db://test"
        >>> len(fixtures.defs)
        1
        >>> fixtures.defs[0].name
        'db'

        A custom name overrides the function name at registration:

        >>> @fixtures.fixture(name="cache")
        ... def _redis_client() -> str:
        ...     return "redis://localhost"
        >>> [d.name for d in fixtures.defs]
        ['db', 'cache']

    """

    def __init__(self, name: str | None = None) -> None:
        self._defs: list[FixtureDef[Any]] = []
        self._defs_by_name: dict[str, FixtureDef[Any]] = {}
        self._namespace_name: str = name or ""
        frame = inspect.currentframe()
        caller = frame.f_back if frame is not None else None
        self._source_line: int = caller.f_lineno if caller is not None else 0

    @property
    def namespace_name(self) -> str:
        return self._namespace_name

    @namespace_name.setter
    def namespace_name(self, value: str) -> None:
        self._namespace_name = value

    @property
    def defs(self) -> tuple[FixtureDef[Any], ...]:
        return tuple(self._defs)

    @property
    def source_line(self) -> int:
        """Line number where this Fixtures() was instantiated."""
        return self._source_line

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
            def workspace(tmp: TempDir) -> Generator[Path, None, None]:
                yield tmp_path          # setup: value given to the test
                shutil.rmtree(tmp_path) # teardown: runs after the test

        **Return types:** Prefer a typed return over a plain dict for fixtures that
        produce multiple values. A `dict` return is opaque at the call site —
        `Fixture[dict]` tells a reader nothing about available keys. A dataclass
        or TypedDict makes the contract visible::

            from dataclasses import dataclass

            @dataclass
            class WorkspaceEnv:
                root: Path
                log: Path

            @fixtures.fixture
            def workspace_env(tmp: TempDir) -> WorkspaceEnv:
                return WorkspaceEnv(root=tmp, log=tmp / "run.log")

        At the call site, `workspace_env: Fixture[WorkspaceEnv]` gives IDE
        completion on `workspace_env.root` and `workspace_env.log`, and
        type checkers enforce the field types without a plugin.

        Args:
            fn: The fixture function (when used as a bare decorator).
            autouse: If `True`, fixture runs for every test without being
                explicitly requested.
            name: Override the fixture name. Defaults to the function name.
            shared: If `True`, fixture is session-lifetime and immutable
                (wrapped with `FrozenProxy`).

        """

        def _register(f: _F) -> _F:
            fixture_name = name or getattr(f, "__name__", repr(f))
            _update(f, fixture_name=fixture_name)
            is_async = inspect.iscoroutinefunction(f) or inspect.isasyncgenfunction(f)
            if autouse and not shared and is_async:
                raise AutouseRegistrationError(f)
            defn = FixtureDef(
                name=fixture_name,
                fixture_type=object,  # placeholder — overwritten by conftest_loader
                scope=FixtureScope.SHARED if shared else FixtureScope.EACH,
                source=ConftestSource(func=f, conftest_path=""),
                autouse=autouse,
                is_async=is_async,
            )
            self._defs.append(defn)
            self._defs_by_name[fixture_name] = defn
            return f

        return _register(fn) if fn is not None else _register

    def __getattr__(self, name: str) -> Any:
        """Return a FixtureAccessor for the named fixture.

        Enables both FixtureRef usage in `@oxitest.parametrize` and lazy
        attribute-proxying inside fixture/test bodies::

            backend=kvault.store          # FixtureRef: resolves at test time
            kvault.store.namespace("x")   # lazy: proxied via contextvar

        Returns ``Any`` because this class name also annotates the access proxy
        (``fx: Fixtures``), where the runtime object is a ``FixturesProxy`` and
        a top-level attribute is three-valued — sub-proxy, fixture value, or
        awaitable handle. No single class models that. Splitting the annotation
        from the registry belongs to #1720, which retires the registry role.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        defn = self._defs_by_name.get(name)
        if defn is not None:
            return FixtureAccessor(name, self, defn.func)
        available = [d.name for d in self._defs]
        msg = (
            f"'{type(self).__name__}' has no registered fixture '{name}'. "
            f"Available: {available}"
        )
        raise AttributeError(msg)
