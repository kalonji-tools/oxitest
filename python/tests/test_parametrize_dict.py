"""Tests for dict-mode @oxi.parametrize: stamps, validation, collection, execution."""

from __future__ import annotations

from dataclasses import dataclass

from oxitest import Fixture, TempDir, helpers, parametrize, raises
from oxitest._bridge._fn_metadata import get_metadata
from oxitest._bridge.conftest_loader import create_session
from oxitest._bridge.importer import collect_module
from oxitest._bridge.parametrize import DataclassCases, DictCases


@dataclass(frozen=True)
class AddCase:
    """Frozen dataclass used as parametrize case type in addition tests."""

    x: int
    y: int
    expected: int


def test_parametrize_dict_mode_stamps_function() -> None:
    """@parametrize with dict values stamps DictCases on the function's metadata."""

    @parametrize(basic={"x": 1, "y": 2, "expected": 3})
    def test_foo(x: int, y: int, expected: int) -> None:
        pass

    raw = get_metadata(test_foo).param_cases
    assert isinstance(raw, tuple), (
        f"dict mode should store a tuple, got {type(raw).__name__}"
    )
    assert len(raw) == 1, f"dict mode should store a 1-tuple, got {raw!r}"
    param_cases = raw[0]
    assert isinstance(param_cases, DictCases), (
        f"dict mode should stamp DictCases, got {type(param_cases)!r}"
    )
    assert param_cases.cases == {"basic": {"x": 1, "y": 2, "expected": 3}}, (
        f"dict mode should store cases correctly, got {param_cases.cases!r}"
    )


def test_parametrize_dict_mode_multiple_cases() -> None:
    """Dict mode with multiple cases stores all entries in the DictCases mapping."""

    @parametrize(
        basic={"x": 1, "y": 2, "expected": 3},
        zero_sum={"x": 0, "y": 0, "expected": 0},
    )
    def test_foo(x: int, y: int, expected: int) -> None:
        pass

    raw = get_metadata(test_foo).param_cases
    assert isinstance(raw, tuple), (
        f"dict mode should store a tuple, got {type(raw).__name__}"
    )
    assert len(raw) == 1, f"dict mode should store a 1-tuple, got {raw!r}"
    param_cases = raw[0]
    assert isinstance(param_cases, DictCases), (
        f"dict mode should stamp DictCases, got {type(param_cases)!r}"
    )
    assert len(param_cases.cases) == 2, (
        f"dict mode with 2 cases should produce 2 entries, got {len(param_cases.cases)}"
    )


def test_parametrize_dict_mode_rejects_extra_key() -> None:
    """@parametrize(dict) raises TypeError when a dict key is not a test parameter."""
    with raises(TypeError, match="unexpected key"):

        @parametrize(basic={"x": 1, "y": 2, "expeced": 3})  # codespell:ignore expeced
        def test_foo(x: int, y: int, expected: int) -> None:
            pass


def test_parametrize_dict_mode_rejects_missing_key() -> None:
    """@parametrize(dict) raises TypeError when a required parameter key is absent."""
    with raises(TypeError, match="missing key"):

        @parametrize(basic={"x": 1, "y": 2})
        def test_foo(x: int, y: int, expected: int) -> None:
            pass


def test_parametrize_dict_mode_rejects_non_dict_case() -> None:
    """@parametrize rejects a case that is not a dict when other cases are dicts."""
    with raises(TypeError, match="must be a dict"):

        @parametrize(
            basic={"x": 1},
            bad=42,
        )
        def test_foo(x: int) -> None:
            pass


def test_parametrize_dict_mode_excludes_fixture_params_from_schema() -> None:
    """Fixture[T] params are not required in the dict — session resolves them."""

    @parametrize(basic={"x": 2, "expected": 20})
    def test_foo(x: int, expected: int, multiplier: Fixture[int]) -> None:
        pass

    raw = get_metadata(test_foo).param_cases
    assert isinstance(raw, tuple), (
        f"dict mode should store a tuple, got {type(raw).__name__}"
    )
    assert len(raw) == 1, f"dict mode should store a 1-tuple, got {raw!r}"
    param_cases = raw[0]
    assert isinstance(param_cases, DictCases), (
        f"dict mode should stamp DictCases, got {type(param_cases)!r}"
    )
    assert param_cases.cases == {"basic": {"x": 2, "expected": 20}}, (
        f"dict mode should not include Fixture params in schema,"
        f" got {param_cases.cases!r}"
    )


def test_executor_dict_mode_passes(tmp: TempDir) -> None:
    """Dict mode: case values injected as individual kwargs, test passes."""
    code = (
        "import oxitest\n"
        "@oxitest.parametrize(\n"
        "    basic=dict(x=1, y=2, expected=3),\n"
        "    negative=dict(x=-1, y=5, expected=4),\n"
        ")\n"
        "def test_add(x: int, y: int, expected: int) -> None:\n"
        "    assert x + y == expected\n"
    )
    result_basic = helpers.common.exec_inline(tmp, code, "test_add", param_id="basic")
    result_neg = helpers.common.exec_inline(tmp, code, "test_add", param_id="negative")
    assert result_basic.status == "passed", result_basic.message
    assert result_neg.status == "passed", result_neg.message


def test_executor_dict_mode_failure(tmp: TempDir) -> None:
    """Dict mode: failing assertion produces 'failed' status."""
    result = helpers.common.exec_inline(
        tmp,
        "import oxitest\n"
        "@oxitest.parametrize(wrong=dict(x=1, y=2, expected=99))\n"
        "def test_add(x: int, y: int, expected: int) -> None:\n"
        "    assert x + y == expected\n",
        "test_add",
        param_id="wrong",
    )
    assert result.status == "failed", (
        f"wrong expected value in dict mode should produce status='failed', got "
        f"{result.status!r}"
    )


def test_executor_dict_mode_with_fixture(tmp: TempDir) -> None:
    """Dict mode: Fixture[T] params resolved from session alongside dict values."""
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "import oxitest\n"
        "fixtures = oxitest.Fixtures()\n"
        "@fixtures.fixture\n"
        "def multiplier():\n"
        "    return 10\n"
    )
    f = tmp / "test_mul.py"
    f.write_text(
        "from __future__ import annotations\n"
        "import oxitest\n"
        "from oxitest import Fixture\n"
        "@oxitest.parametrize(double=dict(x=2, expected=20))\n"
        "def test_mul(x: int, expected: int, multiplier: Fixture[int]) -> None:\n"
        "    assert x * multiplier == expected\n"
    )
    session, _ = create_session([str(conftest)])
    result = helpers.common.run_test(
        str(f), "test_mul", session=session, param_id="double"
    )
    assert result.status == "passed", result.message


def test_collect_dict_parametrize_expands_to_n_items(tmp: TempDir) -> None:
    """collect_module expands an N-case dict @parametrize into N CollectedItems."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "@oxitest.parametrize(\n"
        "    basic=dict(x=1, y=2),\n"
        "    neg=dict(x=-1, y=2),\n"
        ")\n"
        "def test_add(x: int, y: int) -> None:\n"
        "    assert x + y > 0\n",
    )
    items, _ = collect_module(path)
    assert len(items) == 2, (
        f"dict parametrize with 2 cases should yield 2 items, got {len(items)}"
    )
    param_ids = [i.param_id for i in items]
    assert "basic" in param_ids, (
        f"'basic' param_id should be collected, got {param_ids}"
    )
    assert "neg" in param_ids, f"'neg' param_id should be collected, got {param_ids}"


def test_collect_dict_parametrize_item_has_param_values(tmp: TempDir) -> None:
    """Each dict parametrize item carries its values as param_values pairs."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "@oxitest.parametrize(basic=dict(x=1, y=2))\n"
        "def test_add(x: int, y: int) -> None:\n"
        "    pass\n",
    )
    items, _ = collect_module(path)
    assert len(items) == 1, f"expected 1 item, got {len(items)}"
    assert items[0].param_id == "basic", (
        f"expected param_id='basic', got {items[0].param_id!r}"
    )
    assert ("x", "1") in items[0].param_values, (
        f"('x', '1') should be in param_values, got {items[0].param_values}"
    )
    assert ("y", "2") in items[0].param_values, (
        f"('y', '2') should be in param_values, got {items[0].param_values}"
    )


def test_parametrize_inferred_type_stamps_function() -> None:
    """Dataclass mode: type inferred from first case value, no explicit type arg."""

    @parametrize(basic=AddCase(x=1, y=2, expected=3))
    def test_foo(x: int, y: int, expected: int) -> None:
        pass

    raw = get_metadata(test_foo).param_cases
    assert isinstance(raw, tuple), (
        f"dataclass mode should store a tuple, got {type(raw).__name__}"
    )
    assert len(raw) == 1, f"dataclass mode should store a 1-tuple, got {raw!r}"
    param_cases = raw[0]
    assert isinstance(param_cases, DataclassCases), (
        f"dataclass mode should stamp DataclassCases, got {type(param_cases)!r}"
    )
    assert param_cases.param_type is AddCase, (
        f"param_type should be inferred as AddCase, got {param_cases.param_type!r}"
    )
    assert param_cases.cases == {"basic": AddCase(x=1, y=2, expected=3)}, (
        f"cases should store the case value, got {param_cases.cases!r}"
    )


def test_parametrize_rejects_invalid_case_type() -> None:
    """Non-dict, non-dataclass case value raises TypeError at decoration time."""
    with raises(
        TypeError, match="case values must be dicts, frozen dataclass instances"
    ):

        @parametrize(basic=42)
        def test_foo(x: int) -> None:
            pass
