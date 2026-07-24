from __future__ import annotations

__all__ = ["Helpers"]

import inspect
from collections.abc import Callable
from typing import Any, TypeVar, overload

from oxitest._bridge._fixture_registry import ConftestSource
from oxitest._bridge._helper_registry import HelperDef

_F = TypeVar("_F", bound=Callable[..., Any])


class Helpers:
    """Instance-based helper registry. Create one per conftest.py.

    Helpers are stateless test utilities — factory functions, assertion
    builders, or shared setup routines. Register them with :meth:`helper`
    to expose them as attributes on the registry, then access via
    ``helpers.<name>()``.

    Namespace-aware injection into tests happens by declaring a
    ``fx: Fixtures`` parameter and reading ``fx.helpers``. This class
    itself is the registration surface only.

    See Also:
        - :class:`oxitest.Fixtures` — the sibling registry for fixtures.

    Examples:
        Register helpers on a module-level ``helpers`` instance in your
        ``conftest.py``, then call them by attribute:

        >>> from oxitest import Helpers
        >>> helpers = Helpers()
        >>> @helpers.helper
        ... def make_user(name: str = "test") -> str:
        ...     return f"user:{name}"
        >>> helpers.make_user()
        'user:test'
        >>> helpers.make_user("alice")
        'user:alice'

        A custom name can override the function name:

        >>> @helpers.helper(name="build")
        ... def _build() -> int:
        ...     return 42
        >>> helpers.build()
        42

        Unknown helper names raise ``AttributeError``:

        >>> from oxitest import raises
        >>> with raises(AttributeError):
        ...     helpers.nonexistent

    """

    def __init__(self, name: str | None = None) -> None:
        self._defs: list[HelperDef] = []
        self._defs_by_name: dict[str, HelperDef] = {}
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
    def defs(self) -> tuple[HelperDef, ...]:
        return tuple(self._defs)

    @property
    def source_line(self) -> int:
        """Line number where this Helpers() was instantiated."""
        return self._source_line

    @overload
    def helper(self, fn: _F) -> _F: ...

    @overload
    def helper(
        self,
        fn: None = None,
        *,
        name: str | None = None,
    ) -> Callable[[_F], _F]: ...

    def helper(
        self,
        fn: _F | None = None,
        *,
        name: str | None = None,
    ):
        """Register a helper callable with this registry."""

        def _register(f: _F) -> _F:
            helper_name = name or getattr(f, "__name__", repr(f))
            defn = HelperDef(
                name=helper_name,
                func=f,
                source=ConftestSource(func=f, conftest_path=""),
            )
            self._defs.append(defn)
            self._defs_by_name[helper_name] = defn
            return f

        return _register(fn) if fn is not None else _register

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        defn = self._defs_by_name.get(name)
        if defn is not None:
            return defn.func
        available = [d.name for d in self._defs]
        msg = (
            f"'{type(self).__name__}' has no registered helper '{name}'. "
            f"Available: {available}"
        )
        raise AttributeError(msg)
