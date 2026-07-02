from __future__ import annotations

__all__ = ["BuiltinFixture"]

from typing import Any, ClassVar

from oxitest._bridge._builtin_context import _BuiltinContext


class BuiltinFixture:
    """Base class for built-in fixtures.

    Subclasses declare the type they provide via `fixture_type=` and are
    auto-registered in `_registry`. No central dict to maintain — adding a
    new built-in requires touching only its own module.

    Usage::

        class _TempDirFixture(BuiltinFixture, fixture_type=TempDir):
            def create(self, ctx: _BuiltinContext) -> TempDir:
                ...

    Registry pattern: auto-registration via ``__init_subclass__``.
    Appropriate when all registrants are known at import time (static
    subclasses) and no runtime addition/removal is needed. Compare with
    ``FixtureRegistry`` (instance-based, runtime), ``_MARK_REGISTRY``
    (module-level dict, fixed set), and ``PluginRegistry`` (dataclass
    with lazy cached_property).
    """

    # Override to "session" for session-scoped built-ins.
    scope: ClassVar[str] = "function"
    _registry: ClassVar[dict[type, type[BuiltinFixture]]] = {}
    _registered: ClassVar[bool] = False

    def __init_subclass__(cls, *, fixture_type: type | None = None, **kw: Any) -> None:
        super().__init_subclass__(**kw)
        if fixture_type is not None:
            BuiltinFixture._registry[fixture_type] = cls

    @classmethod
    def for_type(cls, inner: type) -> type[BuiltinFixture] | None:
        """Return the registered implementation class for `inner`, or None."""
        return cls._registry.get(inner)

    @classmethod
    def ensure_registered(cls) -> None:
        """Import all builtin modules to trigger __init_subclass__ registration.

        Idempotent — safe to call multiple times. Called by FixtureSession
        before first fixture resolution.
        """
        if cls._registered:
            return

        cls._registered = True

    def create(self, ctx: _BuiltinContext) -> Any:
        raise NotImplementedError
