from __future__ import annotations

from types import MappingProxyType

from oxitest._bridge._fn_metadata import FunctionMetadata, get_metadata, get_or_create
from oxitest._bridge._mark_api import MarkInfo


def test_get_or_create_creates_on_first_access() -> None:
    def fn():
        pass

    meta = get_or_create(fn)
    assert isinstance(meta, FunctionMetadata), (
        f"get_or_create should return a FunctionMetadata instance, "
        f"got {type(meta).__name__}"
    )
    assert meta.marks == [], (
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


def test_get_or_create_returns_same_instance() -> None:
    def fn():
        pass

    first = get_or_create(fn)
    second = get_or_create(fn)
    assert first is second, (
        "get_or_create called twice on the same function should return the "
        "identical object"
    )


def test_get_metadata_returns_default_for_unknown_function() -> None:
    def fn():
        pass

    meta = get_metadata(fn)
    assert isinstance(meta, FunctionMetadata), (
        f"get_metadata on unknown function should return a FunctionMetadata, "
        f"got {type(meta).__name__}"
    )
    assert meta.marks == [], (
        f"default metadata marks should be empty, got {meta.marks!r}"
    )
    assert meta.param_cases is None, (
        f"default metadata param_cases should be None, got {meta.param_cases!r}"
    )
    assert meta.fixture_name is None, (
        f"default metadata fixture_name should be None, got {meta.fixture_name!r}"
    )


def test_get_metadata_returns_registered_metadata() -> None:
    def fn():
        pass

    mark = MarkInfo("slow", (), MappingProxyType({}))
    get_or_create(fn).marks.append(mark)

    meta = get_metadata(fn)
    assert len(meta.marks) == 1, (
        f"get_metadata should see marks added via get_or_create, got {meta.marks!r}"
    )
    assert meta.marks[0].name == "slow", (
        f"registered mark name should be 'slow', got {meta.marks[0].name!r}"
    )


def test_mutations_persist_across_calls() -> None:
    def fn():
        pass

    mark = MarkInfo("integration", (), MappingProxyType({}))
    get_or_create(fn).marks.append(mark)

    assert get_metadata(fn).marks[0].name == "integration", (
        f"mutation via get_or_create should be visible through get_metadata, "
        f"got {get_metadata(fn).marks!r}"
    )


def test_different_functions_get_independent_metadata() -> None:
    def fn_a():
        pass

    def fn_b():
        pass

    get_or_create(fn_a).marks.append(MarkInfo("slow", (), MappingProxyType({})))

    meta_b = get_metadata(fn_b)
    assert meta_b.marks == [], (
        f"fn_b should have independent empty marks, got {meta_b.marks!r}"
    )
