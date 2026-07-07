from __future__ import annotations

from oxitest import SharedFixtureMutationError, raises
from oxitest._bridge.proxy import FrozenProxy


def test_frozen_proxy_reads_attribute_through() -> None:
    class Obj:
        x = 42

    p = FrozenProxy(Obj())
    assert p.x == 42, (
        f"FrozenProxy should transparently forward attribute reads, "
        f"expected 42 got {p.x!r}"
    )


def test_frozen_proxy_reads_item_through() -> None:
    p = FrozenProxy({"k": "v"})
    assert p["k"] == "v", (
        f"FrozenProxy should transparently forward item reads, "
        f"expected 'v' got {p['k']!r}"
    )


def test_frozen_proxy_raises_on_setattr() -> None:
    class Obj:
        pass

    p = FrozenProxy(Obj())
    with raises(SharedFixtureMutationError, match="cannot set attribute 'x'"):
        p.x = 1  # type: ignore[misc]


def test_frozen_proxy_raises_on_delattr() -> None:
    class Obj:
        x = 1

    p = FrozenProxy(Obj())
    with raises(SharedFixtureMutationError, match="cannot delete attribute 'x'"):
        del p.x  # type: ignore[misc]


def test_frozen_proxy_raises_on_setitem() -> None:
    p = FrozenProxy({})
    with raises(SharedFixtureMutationError, match="cannot set item"):
        p["k"] = 1  # type: ignore[index]


def test_frozen_proxy_raises_on_delitem() -> None:
    p = FrozenProxy({"k": 1})
    with raises(SharedFixtureMutationError, match="cannot delete item"):
        del p["k"]  # type: ignore[attr-defined]


def test_frozen_proxy_repr() -> None:
    p = FrozenProxy(42)
    assert repr(p) == "FrozenProxy(42)", (
        f"FrozenProxy repr should be 'FrozenProxy(42)', got {repr(p)!r}"
    )


def test_shared_fixture_mutation_error_is_runtime_error() -> None:
    assert issubclass(SharedFixtureMutationError, RuntimeError), (
        "SharedFixtureMutationError should be a subclass of RuntimeError"
    )


def test_frozen_proxy_len() -> None:
    p = FrozenProxy([1, 2, 3])
    assert len(p) == 3, (
        f"FrozenProxy of a 3-element list should have len 3, got {len(p)}"
    )


def test_frozen_proxy_iter() -> None:
    p = FrozenProxy([10, 20])
    assert list(p) == [10, 20], (
        f"FrozenProxy should iterate over wrapped list, got {list(p)}"
    )


def test_frozen_proxy_contains() -> None:
    p = FrozenProxy({"k": "v"})
    assert "k" in p, (
        "FrozenProxy should support __contains__ — 'k' should be in the proxy"
    )


def test_frozen_proxy_eq() -> None:
    p = FrozenProxy(42)
    assert p == 42, f"FrozenProxy(42) should compare equal to 42, got {p!r}"


def test_frozen_proxy_bool_truthy() -> None:
    assert FrozenProxy([1]), "FrozenProxy([1]) should be truthy (non-empty list)"


def test_frozen_proxy_bool_falsy() -> None:
    assert not FrozenProxy([]), "FrozenProxy([]) should be falsy (empty list)"
