"""The ``fx:`` injection annotation, and the accessor it hands back.

``Fixtures`` is the annotation a test writes as ``fx: Fixtures`` to read
fixtures through a namespace. It was the instance-based registry until #1720
retired that role; calling it now raises and names the replacement.
``FixtureAccessor`` is the lazy proxy ``Fixtures.__getattr__`` returns,
supporting both ``FixtureRef`` usage in ``@oxitest.parametrize`` and
attribute-proxying inside test/fixture bodies.

These classes depend on ``_fixture_context`` (for ContextVar access). They do
**not** import from ``_fixture_session`` — the session module re-exports them
for backward compatibility.
"""

from __future__ import annotations

__all__ = [
    "FixtureAccessor",
    "Fixtures",
]

from collections.abc import Callable
from typing import Any

from oxitest._bridge._fixture_context import _fixture_context
from oxitest._bridge._fn_metadata import get_metadata


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

        # tests/kvault/__fixtures__.py
        @oxi.fixture(lifetime="function")
        def store() -> KVault: ...

        @oxi.fixture(lifetime="function")
        def ns(fx: Fixtures) -> Namespace:
            return fx.kvault.store.namespace("test")  # lazy: resolves live store

        # tests/kvault/test_query.py
        @oxitest.parametrize(
            memory=BackendCase(backend=fx.kvault.store, ...),  # FixtureRef
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
    """The ``fx:`` injection annotation. Not a registry — see :meth:`__init__`.

    A bare ``fx: Fixtures`` parameter injects the namespace accessor, so
    ``fx.db.conn`` reads the fixture ``conn`` declared under the anchor ``db``.
    Injection matches this class by identity, which is why the name survives
    ADR-0009 Rule 5's retirement rather than being freed (#1720).

    Calling it raises. Fixtures are declared with ``@oxi.fixture`` in a
    ``__fixtures__.py``, an ``__init__.py``, or inline in a test module.

    See Also:
        - :class:`Fixture` — the per-parameter injection annotation.
        - ``docs/user/how-to/migrate-from-old-oxitest.md`` — the full mapping.

    Examples:
        Calling it refuses, and the message names the replacement:

        >>> from oxitest import Fixtures, raises
        >>> with raises(TypeError) as caught:
        ...     Fixtures()
        >>> "@oxi.fixture" in str(caught.value)
        True

    """

    def __init__(self, name: str | None = None) -> None:
        """Refuse the call. ``Fixtures`` is an annotation now, not a registry.

        ADR-0009 Rule 5 reuses this name for the ``fx:`` injection annotation
        rather than freeing it, so ``fixtures = oxitest.Fixtures()`` would
        otherwise fail as a call on a name that means something else. The
        message is what turns that into a migration (#1720).

        Raised from ``__init__`` rather than ``__new__`` on purpose:
        ``_read_fixtures`` builds an attribute shell with
        ``Fixtures.__new__(Fixtures)``, which never reaches here.
        """
        del name
        msg = (
            "Fixtures() is no longer a registry.\n"
            "\n"
            "  Declare fixtures with @oxi.fixture in a __fixtures__.py, an\n"
            "  __init__.py, or inline in a test module:\n"
            "\n"
            "      from oxitest import fixture\n"
            "\n"
            "      @fixture(lifetime='function')\n"
            "      def db() -> Database:\n"
            "          return Database()\n"
            "\n"
            "  The name Fixtures is now the proxy type annotation, so a bare\n"
            "  'fx: Fixtures' parameter still injects the namespace accessor.\n"
            "\n"
            "  Migration guide: docs/user/how-to/migrate-from-old-oxitest.md"
        )
        raise TypeError(msg)

    @property
    def namespace_name(self) -> str:
        return self._namespace_name

    @namespace_name.setter
    def namespace_name(self, value: str) -> None:
        self._namespace_name = value

    def __getattr__(self, name: str) -> Any:
        """Return a FixtureAccessor for the named fixture.

        Enables both FixtureRef usage in `@oxitest.parametrize` and lazy
        attribute-proxying inside fixture/test bodies::

            backend=kvault.store          # FixtureRef: resolves at test time
            kvault.store.namespace("x")   # lazy: proxied via contextvar

        Returns ``Any`` because this class name also annotates the access proxy
        (``fx: Fixtures``), where the runtime object is a ``FixturesProxy`` and
        a top-level attribute is three-valued — sub-proxy, fixture value, or
        awaitable handle. No single class models that, and #1720 kept the name
        on the annotation rather than splitting it.

        The registry lookup this used to do is gone with the registry (#1720).
        ``__init__`` raises, so no instance ever carried ``_defs_by_name``; the
        only instance that exists is the attribute shell
        ``_read_fixtures`` builds, and it carries ``namespace_name`` alone.
        The method stays because ``ty`` resolves ``fx.<ns>`` through it.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        msg = (
            f"'{type(self).__name__}' has no attribute '{name}'. "
            f"Fixtures is the injection annotation, not a registry — declare "
            f"fixtures with @oxi.fixture and read them through a 'fx: Fixtures' "
            f"parameter."
        )
        raise AttributeError(msg)
