"""Tests for FrozenProxy — the read-only wrapper used for shared fixtures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from oxitest import SharedFixtureMutationError, parametrize, raises
from oxitest._bridge.proxy import FrozenProxy


def test_frozen_proxy_reads_attribute_through() -> None:
    """Attribute reads pass through transparently to the wrapped object."""

    class Obj:
        x = 42

    p = FrozenProxy(Obj())
    assert p.x == 42, (
        f"FrozenProxy should transparently forward attribute reads, "
        f"expected 42 got {p.x!r}"
    )


def test_frozen_proxy_reads_item_through() -> None:
    """Item reads via [] pass through transparently to the wrapped mapping."""
    p = FrozenProxy({"k": "v"})
    assert p["k"] == "v", (
        f"FrozenProxy should transparently forward item reads, "
        f"expected 'v' got {p['k']!r}"
    )


def test_frozen_proxy_raises_on_setattr() -> None:
    """Setting an attribute on a FrozenProxy raises SharedFixtureMutationError."""

    class Obj:
        pass

    p = FrozenProxy(Obj())
    with raises(SharedFixtureMutationError, match="cannot set attribute 'x'"):
        p.x = 1


def test_frozen_proxy_raises_on_delattr() -> None:
    """Deleting an attribute on a FrozenProxy raises SharedFixtureMutationError."""

    class Obj:
        x = 1

    p = FrozenProxy(Obj())
    with raises(SharedFixtureMutationError, match="cannot delete attribute 'x'"):
        del p.x


def test_frozen_proxy_raises_on_setitem() -> None:
    """Item assignment on a FrozenProxy raises SharedFixtureMutationError."""
    p = FrozenProxy({})
    with raises(SharedFixtureMutationError, match="cannot set item"):
        p["k"] = 1


def test_frozen_proxy_raises_on_delitem() -> None:
    """Item deletion on a FrozenProxy raises SharedFixtureMutationError."""
    p = FrozenProxy({"k": 1})
    with raises(SharedFixtureMutationError, match="cannot delete item"):
        del p["k"]


def test_frozen_proxy_repr() -> None:
    """FrozenProxy repr includes the wrapped value for easy debugging."""
    p = FrozenProxy(42)
    assert repr(p) == "FrozenProxy(42)", (
        f"FrozenProxy repr should be 'FrozenProxy(42)', got {repr(p)!r}"
    )


def test_frozen_proxy_wrapped_slot_is_an_accepted_escape_hatch() -> None:
    """`_wrapped` hands back the original object — by design, per ADR-0005."""
    original = [1, 2]

    p = FrozenProxy(original)

    assert p._wrapped is original, (  # noqa: SLF001
        "ADR-0005 accepts _wrapped as an escape hatch alongside p[k] and "
        "iter(p) returning unwrapped elements; blocking it would need a "
        "__getattribute__ hook on every attribute read. If this ever stops "
        "being true, update the FrozenProxy limitation bullet in ADR-0005 — "
        "the change would be a decision, not a bugfix"
    )


def test_shared_fixture_mutation_error_is_runtime_error() -> None:
    """SharedFixtureMutationError is a RuntimeError subclass."""
    assert issubclass(SharedFixtureMutationError, RuntimeError), (
        "SharedFixtureMutationError should be a subclass of RuntimeError"
    )


def test_frozen_proxy_len() -> None:
    """len() on a FrozenProxy delegates to the wrapped object's __len__."""
    p = FrozenProxy([1, 2, 3])
    assert len(p) == 3, (
        f"FrozenProxy of a 3-element list should have len 3, got {len(p)}"
    )


def test_frozen_proxy_iter() -> None:
    """Iterating a FrozenProxy yields the wrapped object's elements in order."""
    p = FrozenProxy([10, 20])
    assert list(p) == [10, 20], (
        f"FrozenProxy should iterate over wrapped list, got {list(p)}"
    )


def test_frozen_proxy_contains() -> None:
    """`in` on a FrozenProxy delegates to the wrapped object's __contains__."""
    p = FrozenProxy({"k": "v"})
    assert "k" in p, (
        "FrozenProxy should support __contains__ — 'k' should be in the proxy"
    )


def test_frozen_proxy_eq() -> None:
    """Equality comparison on a FrozenProxy delegates to the wrapped object's __eq__."""
    p = FrozenProxy(42)
    assert p == 42, f"FrozenProxy(42) should compare equal to 42, got {p!r}"


def test_frozen_proxy_bool_truthy() -> None:
    """A FrozenProxy wrapping a non-empty container is truthy."""
    assert FrozenProxy([1]), "FrozenProxy([1]) should be truthy (non-empty list)"


def test_frozen_proxy_bool_falsy() -> None:
    """A FrozenProxy wrapping an empty container is falsy."""
    assert not FrozenProxy([]), "FrozenProxy([]) should be falsy (empty list)"


@dataclass(frozen=True)
class RenderCase:
    """Parametrize case for one string-conversion route through FrozenProxy."""

    label: str
    render: Callable[[Any], str]


@parametrize(
    builtin_str=RenderCase(label="str()", render=str),
    fstring=RenderCase(label='f"{p}"', render=lambda p: f"{p}"),
    builtin_format=RenderCase(label="format()", render=format),
    # %-formatting is the behaviour under test, not a style choice — rewriting
    # it to an f-string would exercise a different route.
    percent=RenderCase(label='"%s" %', render=lambda p: "%s" % (p,)),  # noqa: UP031
)
def test_frozen_proxy_string_conversion_forwards_to_wrapped(case: RenderCase) -> None:
    """Every string-conversion route renders the wrapped value, not the wrapper."""
    p = FrozenProxy("pg://db")

    rendered = case.render(p)

    assert rendered == "pg://db", (
        f"{case.label} rendered the wrapper — assertion messages interpolate "
        f"fixture values, so this is the text a user debugs a failure against; "
        f"got {rendered!r}"
    )


def test_frozen_proxy_format_honours_spec() -> None:
    """A non-empty format spec is forwarded to the wrapped object."""
    p = FrozenProxy(3.14159)

    rendered = f"{p:.2f}"

    assert rendered == "3.14", (
        "object.__format__ raises on any non-empty spec, so an unforwarded "
        f"spec is a hard TypeError in a user's f-string — got {rendered!r}"
    )


def test_frozen_proxy_repr_still_shows_wrapper() -> None:
    """repr() keeps reporting the proxy even though str() no longer does."""
    p = FrozenProxy("pg://db")

    rendered = repr(p)

    assert rendered == "FrozenProxy('pg://db')", (
        "repr must keep naming the wrapper — it is how a developer sees that a "
        "value is immutable, and oxi.raises/reporter output depends on it; a "
        f"future 'make it fully transparent' change must fail here — got {rendered!r}"
    )
