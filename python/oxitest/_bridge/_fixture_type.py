from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Annotated, Any, TypeVar


class _FixtureMarker:
    """Sentinel in Annotated metadata — signals engine injection."""


class _FixtureType:
    """Injection signal for oxitest fixtures.

    Annotating a test or fixture parameter with `Fixture[T]` tells oxitest
    to inject the matching fixture at runtime. The annotation is the injection
    signal — an unannotated parameter is NOT injected, even if its name matches
    a registered fixture.

    Example::

        def test_example(numbers: Fixture[list[int]]) -> None:
            assert sum(numbers) > 0

    The type parameter `T` is the fixture's return type. IDEs and type
    checkers resolve `Fixture[list[int]]` as `list[int]` at the call site
    — no plugin required.

    Incorrect (unannotated — oxitest will not inject this)::

        def test_bad(numbers) -> None:  # missing Fixture[T]
            assert sum(numbers) > 0    # TypeError at runtime
    """

    def __class_getitem__(cls, item: Any) -> Any:
        return Annotated[item, _FixtureMarker()]


Fixture = _FixtureType


class _FixtureRefMarker:
    """Sentinel in Annotated metadata — signals fixture-ref field resolution."""


class _FixtureRefType:
    """Annotation for fixture references inside `@oxitest.parametrize` kwargs.

    Use `FixtureRef[T]` as the type annotation on a frozen dataclass field to
    signal that the field's value is a fixture function, not a literal value.
    The runner resolves it to the live fixture instance before each test.

    Example:
        ```python
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
        ```

    """

    def __class_getitem__(cls, item: Any) -> Any:
        return Annotated[Callable[..., item], _FixtureRefMarker()]


FixtureRef = _FixtureRefType


class _YieldsAlias:
    """Return-type annotation for yield-based fixture teardown.

    `Yields[T]` is shorthand for `Generator[T, None, None]` — the correct
    return annotation for a fixture that uses `yield` to separate setup from
    teardown. Without it, type checkers flag yield fixtures with an incorrect
    return type and require `# type: ignore[return]` suppressions.

    This is a **return-type annotation only** — it does not carry an injection
    marker and will not cause oxitest to inject anything.

    Example::

        @fixtures.fixture
        def store() -> Yields[KVault]:
            s = KVault()
            yield s        # value injected into tests
            s.close()      # teardown runs after each test

        @fixtures.fixture
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
    harmless).  ``Fixture[T]`` remains the only mechanism for conftest
    fixtures whose types are generic (e.g. ``list[int]``).
    """
    setattr(cls, "__oxitest_injectable__", True)  # noqa: B010 — dynamic marker not in type stubs
    return cls


__all__ = [
    "Fixture",
    "FixtureRef",
    "Yields",
    "_FixtureMarker",
    "_FixtureRefMarker",
    "_FixtureRefType",
    "_FixtureType",
    "_YieldsAlias",
    "injectable",
]
