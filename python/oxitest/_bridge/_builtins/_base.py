from __future__ import annotations

__all__ = ["BuiltinFixture", "_BuiltinContext"]

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oxitest._bridge.plugin_loader import PluginRegistry


@dataclass
class _BuiltinContext:
    """Passed to BuiltinFixture.create() — carries injection-site metadata."""

    module_path: str
    inject_scope: str  # "function" for test-level injections
    teardown_stack: list[Callable[[], None]]
    fn_name: str = ""  # test function name, used for debuggable temp dir names
    plugin_registry: PluginRegistry | None = field(default=None, repr=False)


class BuiltinFixture:
    """Base class for built-in fixtures.

    Subclasses declare the type they provide via `fixture_type=` and are
    auto-registered in `_registry`. No central dict to maintain — adding a
    new built-in requires touching only its own module.

    Usage::

        class _TempDirFixture(BuiltinFixture, fixture_type=_TempDir):
            def create(self, ctx: _BuiltinContext) -> _TempDir:
                ...
    """

    scope: str = "function"  # override to "session" for session-scoped built-ins
    _registry: dict[type, type[BuiltinFixture]] = {}

    def __init_subclass__(cls, *, fixture_type: type | None = None, **kw: Any) -> None:
        super().__init_subclass__(**kw)
        if fixture_type is not None:
            BuiltinFixture._registry[fixture_type] = cls

    @classmethod
    def for_type(cls, inner: type) -> type[BuiltinFixture] | None:
        """Return the registered implementation class for `inner`, or None."""
        return cls._registry.get(inner)

    def create(self, ctx: _BuiltinContext) -> Any:
        raise NotImplementedError
