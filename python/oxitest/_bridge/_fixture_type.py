from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Annotated, Any, TypeAlias, TypeVar

BindingType: TypeAlias = object
"""What ``Fixture[T]`` puts in the type index — **not** always a class.

`CONTEXT.md` calls this the **Binding Type**: the raw annotation, used as the
key of ``FixtureRegistry._by_type``. Only some of the forms a parameter may
legally carry are classes. ``str | int`` is a ``types.UnionType``,
``list[str]`` is a ``types.GenericAlias``, and ``Union[str, int]`` is a
``typing`` alias — none of them is an instance of ``type``.

Annotated ``type`` until #2098, which is why four call sites read ``__name__``
off one without a guard: the annotation said a non-class could not arrive, so
neither a reader nor ``ty`` expected one, and a ``Fixture[str | int]``
parameter stopped collection for the whole run. Use
:func:`type_display_name` to name one.
"""


class _FixtureMarker:
    """Sentinel in Annotated metadata — signals engine injection."""


class _FixtureType:
    """Injection signal for oxitest fixtures.

    Annotating a test or fixture parameter with ``Fixture[T]`` tells
    oxitest to inject the matching fixture at runtime. The annotation is
    the injection signal — an unannotated parameter is NOT injected, even
    if its name matches a registered fixture.

    The type parameter ``T`` is the fixture's return type. IDEs and type
    checkers resolve ``Fixture[list[int]]`` as ``list[int]`` at the call
    site — no plugin required.

    See Also:
        - :class:`Fixtures` — the registry that owns fixture registrations.
        - :class:`FixtureRef` — for fixture references inside parametrize
          kwargs.
        - :class:`Yields` — return-type annotation for yield fixtures.

    Examples:
        ``Fixture[T]`` expands to ``Annotated[T, _FixtureMarker()]`` — the
        payload carries the marker sentinel that flags injection, while
        the outer type resolves as ``T`` for IDEs and type checkers:

        >>> from oxitest import Fixture
        >>> from oxitest._bridge._fixture_type import _FixtureMarker
        >>> from typing import get_args
        >>> args = get_args(Fixture[int])
        >>> args[0]
        <class 'int'>
        >>> isinstance(args[1], _FixtureMarker)
        True

        Correct usage on a test parameter::

            def test_example(numbers: Fixture[list[int]]) -> None:
                assert sum(numbers) > 0

        Unannotated parameters are NOT injected::

            def test_bad(numbers) -> None:  # missing Fixture[T]
                assert sum(numbers) > 0    # TypeError at runtime

    """

    def __class_getitem__(cls, item: Any) -> Any:
        return Annotated[item, _FixtureMarker()]


Fixture = _FixtureType


class _FixtureRefMarker:
    """Sentinel in Annotated metadata — signals fixture-ref field resolution."""


class _FixtureRefType:
    """Annotation for fixture references inside ``@oxitest.parametrize`` kwargs.

    Use ``FixtureRef[T]`` as the type annotation on a frozen dataclass
    field to signal that the field's value is a fixture function, not a
    literal value. The runner resolves it to the live fixture instance
    before each test.

    See Also:
        - :class:`Fixture` — the runtime-injection sibling.
        - :func:`oxitest.parametrize` — the consumer that resolves refs.

    Examples:
        ``FixtureRef[T]`` expands to
        ``Annotated[Callable[..., T], _FixtureRefMarker()]`` — the marker
        signals that the field carries a fixture reference:

        >>> from oxitest import FixtureRef
        >>> from oxitest._bridge._fixture_type import _FixtureRefMarker
        >>> from typing import get_args
        >>> args = get_args(FixtureRef[int])
        >>> isinstance(args[1], _FixtureRefMarker)
        True

        Usage on a parametrize case::

            from dataclasses import dataclass
            import oxitest
            from oxitest import Fixture, FixtureRef

            @dataclass(frozen=True)
            class BackendCase:
                store: FixtureRef[KVault]
                label: str

            @oxitest.parametrize(
                memory=BackendCase(store=kvault.store, label="in-memory"),
            )
            def test_backend(store: Fixture[KVault], label: str) -> None:
                assert store.ping()

    """

    def __class_getitem__(cls, item: Any) -> Any:
        return Annotated[Callable[..., item], _FixtureRefMarker()]


FixtureRef = _FixtureRefType


class _YieldsAlias:
    """Return-type annotation for yield-based fixture teardown.

    ``Yields[T]`` is shorthand for ``Generator[T, None, None]`` — the
    correct return annotation for a fixture that uses ``yield`` to
    separate setup from teardown. Without it, type checkers flag yield
    fixtures with an incorrect return type and require
    ``# type: ignore[return]`` suppressions.

    Return-type annotation only — it does not carry an injection marker
    and will not cause oxitest to inject anything.

    See Also:
        - :class:`Fixture` — the injection-signal annotation for parameters.
        - :class:`Fixtures` — the registry that owns yield fixtures.

    Examples:
        ``Yields[T]`` is exactly ``Generator[T, None, None]``:

        >>> from oxitest import Yields
        >>> from collections.abc import Generator
        >>> Yields[int] == Generator[int, None, None]
        True

        Usage on a yield fixture::

            @oxi.fixture(lifetime="function")
            def store() -> Yields[KVault]:
                s = KVault()
                yield s        # value injected into tests
                s.close()      # teardown runs after each test

            @oxi.fixture(lifetime="function")
            def tx_cleanup(store: Fixture[KVault]) -> Yields[None]:
                yield          # setup is a no-op; teardown does the work
                store.rollback_if_open()

    """

    def __class_getitem__(cls, item: Any) -> Any:
        return Generator[item, None, None]


Yields = _YieldsAlias


_T = TypeVar("_T", bound=type)


def injectable(cls: _T) -> _T:
    """Mark a class as automatically injectable by oxitest.

    Parameters annotated with an ``@injectable`` class trigger fixture
    injection without ``Fixture[T]`` wrapping::

        @injectable
        class DbSession:
            ...

        def test_query(db_session: DbSession) -> None:
            ...

    ``Fixture[T]`` still works on ``@injectable`` types (redundant but
    harmless). ``Fixture[T]`` remains the only mechanism for conftest
    fixtures whose types are generic (e.g. ``list[int]``).

    See Also:
        - :class:`Fixture` — the explicit injection-signal annotation.

    Examples:
        Applying ``@injectable`` sets the marker attribute the
        collector uses to recognise the class:

        >>> from oxitest import injectable
        >>> @injectable
        ... class DbSession:
        ...     pass
        >>> DbSession.__oxitest_injectable__
        True

    """
    setattr(cls, "__oxitest_injectable__", True)  # noqa: B010 — dynamic marker not in type stubs
    return cls


def type_display_name(fixture_type: BindingType) -> str:
    """The name to print for a binding type.

    A class carries ``__name__``. A PEP 604 union does not: ``str | int`` is a
    ``types.UnionType``, and reading ``__name__`` off one raises
    ``AttributeError`` — which stopped collection for the whole run, because
    the first reader of it runs while parameters are being collected (#2098).

    ``str()`` renders a union the way its author spelled it, so the fallback is
    the better name here and not a degraded one.

    Every caller that names a binding type goes through this function. Six call
    sites each held their own copy of the read, two guarded and four not, which
    is how the crash survived: a guard added to one branch does not protect its
    siblings.

    The union result depends on the interpreter, so it is not shown below. From
    CPython 3.14 ``types.UnionType`` *is* ``typing.Union``, so ``str | int``
    carries ``__name__`` and renders ``'Union'``; below 3.14 it has none and
    renders ``'str | int'``. ``test_union_annotation.py`` pins both arms.

    Examples:
        >>> from oxitest._bridge._fixture_type import type_display_name
        >>> type_display_name(str)
        'str'
        >>> type_display_name(list[str])
        'list'

    """
    return getattr(fixture_type, "__name__", str(fixture_type))


__all__ = [
    "BindingType",
    "Fixture",
    "FixtureRef",
    "Yields",
    "_FixtureMarker",
    "_FixtureRefMarker",
    "_FixtureRefType",
    "_FixtureType",
    "_YieldsAlias",
    "injectable",
    "type_display_name",
]
