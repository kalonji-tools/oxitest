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
        "param_cases must be a tuple to support stacking multiple @parametrize layers"
    )
    assert len(raw) == 1, (
        "a single @parametrize decorator produces exactly one layer -- extra layers"
        " mean the decorator ran twice"
    )
    param_cases = raw[0]
    assert isinstance(param_cases, DictCases), (
        "dict kwargs produce DictCases (not DataclassCases) so the executor knows to"
        " inject individual kwargs"
    )
    assert param_cases.cases == {"basic": {"x": 1, "y": 2, "expected": 3}}, (
        "case dict must round-trip unchanged so the executor injects exactly the values"
        " the user specified"
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
        "param_cases must be a tuple so stacked @parametrize layers stay independent"
    )
    assert len(raw) == 1, (
        "one decorator means one layer -- extra layers would cause unexpected"
        " cross-product expansion"
    )
    param_cases = raw[0]
    assert isinstance(param_cases, DictCases), (
        "dict kwargs must produce DictCases so the executor dispatches kwargs"
        " injection, not dataclass unpacking"
    )
    assert len(param_cases.cases) == 2, (
        "each kwarg to @parametrize becomes a separate test case -- losing one means a"
        " test scenario silently disappears"
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
        "param_cases must be a tuple so stacking multiple @parametrize layers works"
        " correctly"
    )
    assert len(raw) == 1, (
        "single @parametrize produces one layer -- more would multiply the test matrix"
        " unexpectedly"
    )
    param_cases = raw[0]
    assert isinstance(param_cases, DictCases), (
        "dict kwargs must stamp DictCases so the executor uses kwargs injection for"
        " these values"
    )
    assert param_cases.cases == {"basic": {"x": 2, "expected": 20}}, (
        "Fixture[T] params must be excluded from the case schema because the session"
        " resolves them separately -- including them would cause duplicate injection"
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
    assert result_basic.status == "passed", (
        f"dict-mode must inject case values as kwargs so the test body receives them --"
        f" failure means injection is broken: {result_basic.message}"
    )
    assert result_neg.status == "passed", (
        f"each dict case runs independently with its own kwargs -- if 'negative' fails"
        f" while 'basic' passes, case isolation is broken: {result_neg.message}"
    )


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
        "dict-mode must propagate assertion failures so users see real test results --"
        " swallowing failures hides bugs"
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
    session, _, _diags = create_session([str(conftest)])
    result = helpers.common.run_test(
        str(f), "test_mul", session=session, param_id="double"
    )
    assert result.status == "passed", (
        f"dict-mode and fixture injection must coexist -- the session resolves"
        f" Fixture[T] params while dict supplies the rest: {result.message}"
    )


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
        "each dict case must expand into its own CollectedItem so the scheduler can"
        " distribute them independently across workers"
    )
    param_ids = [i.param_id for i in items]
    assert "basic" in param_ids, (
        "dict keys become param_ids for readable test output and --only filtering --"
        " missing IDs break test selection"
    )
    assert "neg" in param_ids, (
        "every dict key must appear as a param_id -- a missing key means that test"
        " scenario was silently dropped during collection"
    )


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
    assert len(items) == 1, (
        "single-case dict must produce exactly one CollectedItem -- extra items would"
        " run the test multiple times"
    )
    assert items[0].param_id == "basic", (
        "param_id must match the dict key so --only test selection and reporter output"
        " identify the right case"
    )
    assert ("x", "1") in items[0].param_values, (
        "param_values carry the stringified args for reporter display and cache keying"
        " -- missing 'x' means broken test identity"
    )
    assert ("y", "2") in items[0].param_values, (
        "every dict entry must appear in param_values so the reporter shows all"
        " injected values in test output"
    )


def test_parametrize_inferred_type_stamps_function() -> None:
    """Dataclass mode: type inferred from first case value, no explicit type arg."""

    @parametrize(basic=AddCase(x=1, y=2, expected=3))
    def test_foo(x: int, y: int, expected: int) -> None:
        pass

    raw = get_metadata(test_foo).param_cases
    assert isinstance(raw, tuple), (
        "param_cases must be a tuple so stacked @parametrize decorators compose into a"
        " cross-product matrix"
    )
    assert len(raw) == 1, (
        "one decorator means one layer -- extra layers would create an unintended"
        " cross-product expansion"
    )
    param_cases = raw[0]
    assert isinstance(param_cases, DataclassCases), (
        "dataclass values must produce DataclassCases so the executor unpacks fields,"
        " not dict keys"
    )
    assert param_cases.param_type is AddCase, (
        "param_type must be inferred from the first case value so the executor can"
        " validate all cases share the same schema"
    )
    assert param_cases.cases == {"basic": AddCase(x=1, y=2, expected=3)}, (
        "case dataclass must round-trip unchanged so field values arrive at the test"
        " body exactly as the user declared them"
    )


def test_parametrize_rejects_invalid_case_type() -> None:
    """Non-dict, non-dataclass case value raises TypeError at decoration time."""
    with raises(
        TypeError, match="case values must be dicts, frozen dataclass instances"
    ):

        @parametrize(basic=42)
        def test_foo(x: int) -> None:
            pass
