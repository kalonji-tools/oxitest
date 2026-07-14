"""Tests for FunctionMetadata get_or_create and get_metadata registry functions."""

from __future__ import annotations

from types import MappingProxyType

from oxitest._bridge._fn_metadata import (
    FunctionMetadata,
    _update,
    get_metadata,
    get_or_create,
)
from oxitest._bridge._mark_api import MarkInfo


def test_get_or_create_creates_on_first_access() -> None:
    """get_or_create should initialize a fresh FunctionMetadata on first call."""

    def fn() -> None:
        pass

    meta = get_or_create(fn)
    assert isinstance(meta, FunctionMetadata), (
        f"get_or_create should return a FunctionMetadata instance, "
        f"got {type(meta).__name__}"
    )
    assert meta.marks == (), (
        f"newly created FunctionMetadata should have empty marks, got {meta.marks!r}"
    )
    assert meta.param_cases is None, (
        f"newly created FunctionMetadata should have param_cases=None, "
        f"got {meta.param_cases!r}"
    )
    assert meta.fixture_name is None, (
        f"newly created FunctionMetadata should have fixture_name=None, "
        f"got {meta.fixture_name!r}"
    )
    assert meta.arranged == (), (
        f"newly created FunctionMetadata should have empty arranged, "
        f"got {meta.arranged!r} — "
        f"undecorated tests must not require explicit initialization of arranged"
    )


def test_get_or_create_returns_same_instance() -> None:
    """get_or_create should return the identical object on repeated calls."""

    def fn() -> None:
        pass

    first = get_or_create(fn)
    second = get_or_create(fn)
    assert first is second, (
        "get_or_create called twice on the same function should return the "
        "identical object"
    )


def test_get_metadata_returns_default_for_unknown_function() -> None:
    """get_metadata on an unregistered function should return empty default metadata."""

    def fn() -> None:
        pass

    meta = get_metadata(fn)
    assert isinstance(meta, FunctionMetadata), (
        f"get_metadata on unknown function should return a FunctionMetadata, "
        f"got {type(meta).__name__}"
    )
    assert meta.marks == (), (
        f"default metadata marks should be empty, got {meta.marks!r}"
    )
    assert meta.param_cases is None, (
        f"default metadata param_cases should be None, got {meta.param_cases!r}"
    )
    assert meta.fixture_name is None, (
        f"default metadata fixture_name should be None, got {meta.fixture_name!r}"
    )


def test_get_metadata_returns_registered_metadata() -> None:
    """get_metadata should reflect marks added via get_or_create."""

    def fn() -> None:
        pass

    mark = MarkInfo("slow", (), MappingProxyType({}))
    _update(fn, marks=(mark,))

    meta = get_metadata(fn)
    assert len(meta.marks) == 1, (
        f"get_metadata should see marks added via _update, got {meta.marks!r}"
    )
    assert meta.marks[0].name == "slow", (
        f"registered mark name should be 'slow', got {meta.marks[0].name!r}"
    )


def test_mutations_persist_across_calls() -> None:
    """Mutations via _update are visible through subsequent get_metadata calls."""

    def fn() -> None:
        pass

    mark = MarkInfo("integration", (), MappingProxyType({}))
    _update(fn, marks=(mark,))

    assert get_metadata(fn).marks[0].name == "integration", (
        f"mutation via _update should be visible through get_metadata, "
        f"got {get_metadata(fn).marks!r}"
    )


def test_different_functions_get_independent_metadata() -> None:
    """Marks added to fn_a should not appear on fn_b's independent metadata."""

    def fn_a() -> None:
        pass

    def fn_b() -> None:
        pass

    _update(fn_a, marks=(MarkInfo("slow", (), MappingProxyType({})),))

    meta_b = get_metadata(fn_b)
    assert meta_b.marks == (), (
        f"fn_b should have independent empty marks, got {meta_b.marks!r}"
    )
