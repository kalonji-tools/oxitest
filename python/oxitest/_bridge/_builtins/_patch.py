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

    All four surfaces mutate **process-global** state. A Worker runs one test
    at a time, so a restored change is never visible to another test — but a
    change whose restore does not run is inherited by every later test in that
    Worker. Changes are reverted in LIFO order when the test completes; every
    undo is attempted even when an earlier one raises, and a failure is
    reported rather than swallowed (#1966).

    The four mutation surfaces:

    - :meth:`setattr` — swap an attribute on any object.
    - :meth:`setenv` — set an environment variable.
    - :meth:`delenv` — remove an environment variable.
    - :meth:`chdir` — change the working directory.

    Each records the previous value so :meth:`close` restores everything
    on teardown.

    See Also:
        - :class:`TestContext` — for imperative teardown of arbitrary
          resources via ``addfinalizer``.

    Examples:
        Injected by parameter type — declare ``patch: Patcher`` on the
        test::

            def test_env(patch: Patcher) -> None:
                patch.setenv("DEBUG", "1")
                run_thing()  # sees DEBUG=1

        For illustration, direct construction + close (production code
        should let oxitest manage the lifecycle):

        >>> from oxitest import Patcher
        >>> import os
        >>> patcher = Patcher()
        >>> patcher.setenv("OXITEST_DOCTEST_VAR", "hello")
        >>> os.environ["OXITEST_DOCTEST_VAR"]
        'hello'
        >>> patcher.close()
        >>> "OXITEST_DOCTEST_VAR" in os.environ
        False

    """

    def __init__(self) -> None:
        self._undos: list[Callable[[], None]] = []

    def setattr(self, obj: Any, name: str, value: Any) -> None:
        """Temporarily set an attribute on *obj*, restoring the original after the test.

        If *obj* does not own *name* — the attribute is reached through a base
        class or through the type — the undo **removes** it rather than writing
        the inherited value onto *obj*. Writing it back made *obj* own an
        attribute it did not own before, and that entry hid the base for every
        later test in the same worker, because the state is process-global
        (#2146). ``setenv`` and ``delenv`` already make the same distinction.

        An object with ``__slots__`` has no ``__dict__``, so ownership cannot be
        read there. Such an object is restored with ``setattr``, which is what
        every object got before: ``delattr`` on a set slot would clear a value
        the object really had.

        Args:
            obj: The object whose attribute is being patched.
            name: Attribute name on *obj*.
            value: Replacement value for the duration of the test.

        Raises:
            AttributeError: If *obj* does not have an attribute named *name*.

        """
        old = getattr(obj, name)
        # Read before the write, or the write is what the test finds.
        namespace = getattr(obj, "__dict__", None)
        owned = namespace is None or name in namespace
        setattr(obj, name, value)
        if owned:
            self._undos.append(
                lambda _obj=obj, _name=name, _old=old: setattr(_obj, _name, _old)
            )
        else:
            self._undos.append(lambda _obj=obj, _name=name: delattr(_obj, _name))

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

        The sharpest of the four surfaces: the working directory is read
        implicitly by every relative path, and inherited by every subprocess
        spawned without an explicit ``cwd``. A restore that does not run leaves
        later tests in this Worker in the wrong directory (#1957).

        Args:
            path: Directory to change into. Accepts any `os.fspath`-compatible value.

        """
        old = Path.cwd()
        os.chdir(path)
        self._undos.append(lambda p=old: os.chdir(p))

    def close(self) -> None:
        """Revert every override in LIFO order, then re-raise the first failure.

        Every undo is attempted even when an earlier one raises. Aborting on
        the first failure stranded every undo behind it — measured leaking an
        env override into the next test in the same worker (#1966) — and these
        surfaces are process-global, so a stranded undo is inherited rather
        than discarded.

        The first exception is re-raised after the sweep, so a direct caller
        still sees the failure it sees today. Under oxitest the teardown drain
        turns it into a diagnostic rather than a test failure.
        """
        first: Exception | None = None
        for fn in reversed(self._undos):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 — an undo runs user-reachable code; the first failure is re-raised below
                # `is None`, not `or`: an exception is not guaranteed truthy,
                # and this repo has been bitten by that idiom before.
                if first is None:
                    first = exc
        self._undos.clear()
        if first is not None:
            raise first


class _PatcherFixture(BuiltinFixture, fixture_type=Patcher):
    def create(self, *, ctx: _BuiltinContext) -> Patcher:
        patcher = Patcher()
        ctx.teardown_stack.append(patcher.close)
        return patcher
