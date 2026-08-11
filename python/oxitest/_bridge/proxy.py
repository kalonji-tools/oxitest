from __future__ import annotations

from typing import Any

from oxitest._bridge._errors import (
    SharedFixtureMutationError as SharedFixtureMutationError,  # noqa: PLC0414 — re-export for __all__
)

__all__ = ["FrozenProxy", "SharedFixtureMutationError"]


class FrozenProxy:
    """Transparent proxy for a fixture value that outlives one test.

    Attribute reads, item reads, and string conversion (``str``, ``format``)
    pass through to the wrapped object. ``repr`` deliberately does not — it
    reports the wrapper, so a developer can see that a value is immutable.
    Any write attempt raises SharedFixtureMutationError.

    Note: method calls that mutate the underlying object (e.g. `proxy.list.append(x)`)
    are not intercepted at runtime — use `ty` with `Fixture[T]` annotations to
    catch those at the call site.
    """

    __slots__ = ("_wrapped",)

    def __init__(self, wrapped: Any) -> None:
        object.__setattr__(self, "_wrapped", wrapped)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_wrapped"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        msg = f"fixture value is frozen: cannot set attribute '{name}'"
        raise SharedFixtureMutationError(msg)

    def __delattr__(self, name: str) -> None:
        msg = f"fixture value is frozen: cannot delete attribute '{name}'"
        raise SharedFixtureMutationError(msg)

    def __getitem__(self, key: Any) -> Any:
        return object.__getattribute__(self, "_wrapped")[key]

    def __setitem__(self, key: Any, value: Any) -> None:
        msg = f"fixture value is frozen: cannot set item {key!r}"
        raise SharedFixtureMutationError(msg)

    def __delitem__(self, key: Any) -> None:
        msg = f"fixture value is frozen: cannot delete item {key!r}"
        raise SharedFixtureMutationError(msg)

    def __len__(self) -> int:
        return len(object.__getattribute__(self, "_wrapped"))

    def __iter__(self) -> Any:
        return iter(object.__getattribute__(self, "_wrapped"))

    def __contains__(self, item: Any) -> bool:
        return item in object.__getattribute__(self, "_wrapped")

    def __eq__(self, other: object) -> bool:
        if isinstance(other, FrozenProxy):
            return object.__getattribute__(self, "_wrapped") == object.__getattribute__(
                other, "_wrapped"
            )
        return object.__getattribute__(self, "_wrapped") == other

    def __hash__(self) -> int:
        return hash(object.__getattribute__(self, "_wrapped"))

    def __bool__(self) -> bool:
        return bool(object.__getattribute__(self, "_wrapped"))

    def __str__(self) -> str:
        return str(object.__getattribute__(self, "_wrapped"))

    def __format__(self, format_spec: str) -> str:
        return format(object.__getattribute__(self, "_wrapped"), format_spec)

    def __repr__(self) -> str:
        return f"FrozenProxy({object.__getattribute__(self, '_wrapped')!r})"
