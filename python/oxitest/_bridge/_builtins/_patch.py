from __future__ import annotations

__all__ = ["Patcher", "_PatcherFixture"]

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from oxitest._bridge._builtins._base import BuiltinFixture

if TYPE_CHECKING:
    from oxitest._bridge._builtin_context import _BuiltinContext
from oxitest._bridge._fixture_type import injectable


@injectable
class Patcher:
    """Applies temporary overrides to attributes, env vars, and working directory.

    All changes are reverted in LIFO order when the test completes.
    """

    def __init__(self) -> None:
        self._undos: list[Callable[[], None]] = []

    def setattr(self, obj: Any, name: str, value: Any) -> None:
        """Temporarily set an attribute on *obj*, restoring the original after the test.

        Args:
            obj: The object whose attribute is being patched.
            name: Attribute name on *obj*.
            value: Replacement value for the duration of the test.

        Raises:
            AttributeError: If *obj* does not have an attribute named *name*.

        """
        old = getattr(obj, name)
        setattr(obj, name, value)
        self._undos.append(
            lambda _obj=obj, _name=name, _old=old: setattr(_obj, _name, _old)
        )

    def setenv(self, name: str, value: str) -> None:
        """Temporarily set an environment variable, restoring it after the test.

        Args:
            name: Environment variable name.
            value: New string value.

        """
        old = os.environ.get(name)
        os.environ[name] = value
        if old is None:

            def _undo(n: str = name) -> None:
                os.environ.pop(n, None)

            self._undos.append(_undo)
        else:
            self._undos.append(lambda n=name, v=old: os.environ.__setitem__(n, v))

    def delenv(self, name: str) -> None:
        """Temporarily remove an environment variable, restoring it after the test.

        If *name* is not set in the environment, this is a silent no-op.

        Args:
            name: Environment variable name to remove.

        """
        old = os.environ.get(name)
        if old is not None:
            del os.environ[name]
            self._undos.append(lambda n=name, v=old: os.environ.__setitem__(n, v))

    def chdir(self, path: Any) -> None:
        """Temporarily change the working directory, restoring it after the test.

        Args:
            path: Directory to change into. Accepts any `os.fspath`-compatible value.

        """
        old = Path.cwd()
        os.chdir(path)
        self._undos.append(lambda p=old: os.chdir(p))

    def close(self) -> None:
        for fn in reversed(self._undos):
            fn()
        self._undos.clear()


class _PatcherFixture(BuiltinFixture, fixture_type=Patcher):
    def create(self, ctx: _BuiltinContext) -> Patcher:
        patcher = Patcher()
        ctx.teardown_stack.append(patcher.close)
        return patcher
