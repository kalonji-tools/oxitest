from __future__ import annotations

from dataclasses import dataclass

from oxitest import Fixture, TempDir, parametrize, raises
from oxitest._bridge.conftest_loader import create_session, load_fixtures_from_conftest
from oxitest._bridge.executor import run_test as executor_run_test
from oxitest._bridge.fixtures import FixtureDef, FixtureRegistry, FixtureSession
from oxitest._bridge.importer import collect_module
from oxitest._bridge.parametrize import _DataclassCases, _DictCases


@dataclass(frozen=True)
class AddCase:
    x: int
    y: int
    expected: int


def test_parametrize_stamps_function():
    @parametrize(basic=AddCase(x=1, y=2, expected=3))
    def test_foo(x, y, expected):
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    param_cases = get_metadata(test_foo).param_cases
    assert isinstance(param_cases, _DataclassCases), (
        "parametrize decorator should stamp '_oxitest_param_cases' as _DataclassCases"
    )
    assert "basic" in param_cases.cases, (
        f"'basic' case should be in param_cases.cases, got {list(param_cases.cases)}"
    )
    assert param_cases.cases["basic"] == AddCase(x=1, y=2, expected=3), (
        f"'basic' case value should be AddCase(x=1, y=2, expected=3), "
        f"got {param_cases.cases['basic']!r}"
    )
    assert param_cases.param_type is AddCase, (
        f"param_type should be AddCase, got {param_cases.param_type!r}"
    )


def test_parametrize_multiple_cases():
    @parametrize(
        basic=AddCase(x=1, y=2, expected=3),
        zero_sum=AddCase(x=0, y=0, expected=0),
    )
    def test_foo(x, y, expected):
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    param_cases = get_metadata(test_foo).param_cases
    assert isinstance(param_cases, _DataclassCases), (
        "decorator should stamp _DataclassCases"
    )
    assert len(param_cases.cases) == 2, (
        f"expected 2 parametrize cases, got {len(param_cases.cases)}"
    )
    assert "zero_sum" in param_cases.cases, (
        f"'zero_sum' case should be in param_cases.cases, got {list(param_cases.cases)}"
    )


def test_parametrize_rejects_non_dataclass():
    with raises(
        TypeError, match="case values must be dicts or frozen dataclass instances"
    ):

        @parametrize(basic=object())
        def test_foo(x):
            pass


def test_parametrize_rejects_non_frozen_dataclass():
    @dataclass
    class Mutable:
        x: int

    with raises(TypeError, match="frozen=True"):

        @parametrize(basic=Mutable(x=1))
        def test_foo(x):
            pass


def test_parametrize_rejects_empty_cases():
    with raises(TypeError, match="at least one case"):

        @parametrize()
        def test_foo(x):
            pass


def test_parametrize_rejects_wrong_instance_type():
    @dataclass(frozen=True)
    class OtherCase:
        z: int

    with raises(TypeError, match="instance of 'AddCase'"):

        @parametrize(
            good=AddCase(x=1, y=2, expected=3),
            bad=OtherCase(z=1),
        )
        def test_foo(x):
            pass


def test_collect_parametrize_expands_to_n_items(tmp: TempDir):
    f = tmp / "test_add.py"
    f.write_text(
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "@dataclass(frozen=True)\n"
        "class AddCase:\n"
        "    x: int\n"
        "    y: int\n"
        "@oxitest.parametrize("
        "basic=AddCase(x=1, y=2), neg=AddCase(x=-1, y=2))\n"
        "def test_add(x, y):\n"
        "    assert x + y > 0\n"
    )
    items, _ = collect_module(str(f))
    assert len(items) == 2, (
        f"parametrize with 2 cases should yield 2 items, got {len(items)}: "
        f"{[i.fn_name for i in items]}"
    )
    fn_names = [i.fn_name for i in items]
    assert "test_add" in fn_names, (
        f"'test_add' should appear in collected items, got {fn_names}"
    )
    param_ids = [i.param_id for i in items]
    assert "basic" in param_ids, (
        f"'basic' param_id should be collected, got {param_ids}"
    )
    assert "neg" in param_ids, f"'neg' param_id should be collected, got {param_ids}"


def test_collect_parametrize_item_has_param_values(tmp: TempDir):
    f = tmp / "test_add.py"
    f.write_text(
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "@dataclass(frozen=True)\n"
        "class AddCase:\n"
        "    x: int\n"
        "    y: int\n"
        "@oxitest.parametrize(basic=AddCase(x=1, y=2))\n"
        "def test_add(x, y):\n"
        "    pass\n"
    )
    items, _ = collect_module(str(f))
    assert len(items) == 1, f"expected 1 parametrized item, got {len(items)}"
    assert items[0].fn_name == "test_add", (
        f"expected fn_name='test_add', got {items[0].fn_name!r}"
    )
    assert items[0].param_id == "basic", (
        f"expected param_id='basic', got {items[0].param_id!r}"
    )
    assert ("x", "1") in items[0].param_values, (
        f"('x', '1') should be in param_values, got {items[0].param_values}"
    )
    assert ("y", "2") in items[0].param_values, (
        f"('y', '2') should be in param_values, got {items[0].param_values}"
    )


def test_collect_non_parametrize_has_none_param_id(tmp: TempDir):
    f = tmp / "test_foo.py"
    f.write_text("def test_foo(): pass\n")
    items, _ = collect_module(str(f))
    assert len(items) == 1, f"expected 1 item, got {len(items)}"
    assert items[0].param_id is None, (
        f"non-parametrized test should have param_id=None, got {items[0].param_id!r}"
    )
    assert items[0].param_values == [], (
        f"non-parametrized test should have empty param_values, got "
        f"{items[0].param_values}"
    )


def test_plain_typed_param_not_resolved_as_fixture():
    """Plain-typed params (no Fixture[T] annotation) must not be resolved."""
    registry = FixtureRegistry()
    session = FixtureSession(registry)
    session.begin_module("/fake/test_foo.py")

    def test_fn(x: int, y: int):
        pass

    # x and y are NOT annotated with Fixture[T] — should not raise FixtureNotFoundError
    kwargs, _ = session.resolve_for_test(test_fn, "/fake/test_foo.py")
    assert kwargs == {}, (
        f"plain-typed params should not be resolved as fixtures, got kwargs={kwargs!r}"
    )


def test_fixture_annotated_param_resolved_alongside_plain_param():
    """Fixture[T]-annotated params are resolved; plain-typed params are skipped."""
    registry = FixtureRegistry()

    def my_fixture():
        return 42

    registry.register(
        FixtureDef(
            name="db",
            func=my_fixture,
            autouse=False,
            params=None,
            conftest_path="",
        )
    )
    session = FixtureSession(registry)
    session.begin_module("/fake/test_foo.py")

    def test_fn(x: int, db: Fixture[int]) -> None:  # type: ignore[type-arg]
        pass

    kwargs, _ = session.resolve_for_test(test_fn, "/fake/test_foo.py")
    assert kwargs == {"db": 42}, (
        f"only Fixture[T]-annotated param 'db' should be resolved, got "
        f"kwargs={kwargs!r}"
    )


def test_plain_typed_param_matching_fixture_raises_unannotated_error():
    """A param with a wrong annotation (e.g. int instead of Fixture[int]) whose name
    matches a registered fixture raises UnannotatedFixtureParamError — the check covers
    both no-annotation and wrong-annotation cases."""
    from oxitest._bridge._errors import UnannotatedFixtureParamError

    registry = FixtureRegistry()

    def x_fixture():
        return 99

    registry.register(
        FixtureDef(
            name="x",
            func=x_fixture,
            autouse=False,
            params=None,
            conftest_path="",
        )
    )
    session = FixtureSession(registry)
    session.begin_module("/fake/test_foo.py")

    def test_fn(x: int):
        pass

    with raises(UnannotatedFixtureParamError) as exc_info:
        session.resolve_for_test(test_fn, "/fake/test_foo.py")

    msg = str(exc_info.value)
    assert "x" in msg, (
        f"UnannotatedFixtureParamError message should mention param name 'x', got "
        f"{msg!r}"
    )
    assert "Fixture[" in msg, (
        f"UnannotatedFixtureParamError message should suggest Fixture[...] annotation, "
        f"got {msg!r}"
    )


def test_executor_runs_parametrize_case(tmp: TempDir):
    f = tmp / "test_add.py"
    f.write_text(
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "@dataclass(frozen=True)\n"
        "class AddCase:\n"
        "    x: int\n"
        "    y: int\n"
        "    expected: int\n"
        "@oxitest.parametrize(basic=AddCase(x=1, y=2, expected=3))\n"
        "def test_add(x, y, expected):\n"
        "    assert x + y == expected\n"
    )
    result = executor_run_test(str(f), "test_add", session=None, param_id="basic")
    assert result.status == "passed", (
        f"parametrize case 'basic' should pass, got status={result.status!r}, "
        f"msg={result.message!r}"
    )


def test_executor_parametrize_failure(tmp: TempDir):
    f = tmp / "test_add.py"
    f.write_text(
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "@dataclass(frozen=True)\n"
        "class AddCase:\n"
        "    x: int\n"
        "    y: int\n"
        "    expected: int\n"
        "@oxitest.parametrize(wrong=AddCase(x=1, y=2, expected=99))\n"
        "def test_add(x, y, expected):\n"
        "    assert x + y == expected\n"
    )
    result = executor_run_test(str(f), "test_add", session=None, param_id="wrong")
    assert result.status == "failed", (
        f"wrong expected value should produce status='failed', got {result.status!r}"
    )


def test_executor_parametrize_case_with_fixture(tmp: TempDir):
    """param_id + session: param values injected, fixture resolved, no collision."""
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "import oxitest\n"
        "fixtures = oxitest.Fixtures()\n"
        "@fixtures.fixture\n"
        "def multiplier():\n"
        "    return 10\n"
    )
    f = tmp / "test_mixed.py"
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture\n"
        "@dataclass(frozen=True)\n"
        "class MulCase:\n"
        "    x: int\n"
        "    expected: int\n"
        "@oxitest.parametrize(double=MulCase(x=2, expected=20))\n"
        "def test_mul(x: int, expected: int, multiplier: Fixture[int]) -> None:\n"
        "    assert x * multiplier == expected\n"
    )
    session = create_session([str(conftest)])
    session.begin_module(str(f))
    result = executor_run_test(str(f), "test_mul", session=session, param_id="double")
    assert result.status == "passed", (
        f"parametrize with fixture should pass, got status={result.status!r}, "
        f"msg={result.message!r}"
    )


def test_fixture_ref_in_parametrize_resolves_fixture(tmp: TempDir):
    """FixtureRef[T] field is resolved via the fixture session per case."""
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "import oxitest\n"
        "fixtures = oxitest.Fixtures()\n"
        "\n"
        "@fixtures.fixture\n"
        "def pg_db():\n"
        "    return 'postgres'\n"
        "\n"
        "@fixtures.fixture\n"
        "def sqlite_db():\n"
        "    return 'sqlite'\n"
    )
    f = tmp / "test_db.py"
    # The test file defines local stubs decorated with Fixtures() so they get
    # _oxitest_fixture_name stamped. The session resolves by name from conftest.
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef\n"
        "\n"
        "_fixtures = oxitest.Fixtures()\n"
        "\n"
        "@_fixtures.fixture\n"
        "def pg_db(): return 'postgres'\n"
        "\n"
        "@_fixtures.fixture\n"
        "def sqlite_db(): return 'sqlite'\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class DbCase:\n"
        "    db: FixtureRef[str]\n"
        "    expected: str\n"
        "\n"
        "@oxitest.parametrize(\n"
        "    pg=DbCase(db=pg_db, expected='postgres'),\n"
        "    sq=DbCase(db=sqlite_db, expected='sqlite'),\n"
        ")\n"
        "def test_db(db: Fixture[str], expected: str) -> None:\n"
        "    assert db == expected\n"
    )
    session = create_session([str(conftest)])
    session.begin_module(str(f))
    result_pg = executor_run_test(str(f), "test_db", session=session, param_id="pg")
    result_sq = executor_run_test(str(f), "test_db", session=session, param_id="sq")
    assert result_pg.status == "passed", result_pg.message
    assert result_sq.status == "passed", result_sq.message


def test_fixture_ref_compact_mode_raises(tmp: TempDir):
    """FixtureRef fields are incompatible with compact mode — must return error."""
    f = tmp / "test_bad.py"
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef\n"
        "@dataclass(frozen=True)\n"
        "class DbCase:\n"
        "    db: FixtureRef[str]\n"
        "def my_db(): return 'x'\n"
        "@oxitest.parametrize(pg=DbCase(db=my_db))\n"
        "def test_db(case: DbCase) -> None:\n"  # compact mode: single DbCase param
        "    pass\n"
    )
    result = executor_run_test(str(f), "test_db", session=None, param_id="pg")
    assert result.status == "error", (
        f"FixtureRef in compact mode should produce status='error', got "
        f"{result.status!r}"
    )
    assert "compact mode" in result.message, (
        f"error message should mention 'compact mode', got {result.message!r}"
    )


def test_fixture_ref_unregistered_fixture_errors(tmp: TempDir):
    """Passing an unregistered fixture function as FixtureRef value → error result."""
    conftest = tmp / "conftest.py"
    conftest.write_text("import oxitest\nfixtures = oxitest.Fixtures()\n")
    f = tmp / "test_bad.py"
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef\n"
        "@dataclass(frozen=True)\n"
        "class DbCase:\n"
        "    db: FixtureRef[str]\n"
        "def unknown_db(): return 'x'\n"
        "@oxitest.parametrize(pg=DbCase(db=unknown_db))\n"
        "def test_db(db: Fixture[str]) -> None:\n"
        "    pass\n"
    )
    session = create_session([str(conftest)])
    session.begin_module(str(f))
    result = executor_run_test(str(f), "test_db", session=session, param_id="pg")
    assert result.status == "error", (
        f"unregistered FixtureRef should produce status='error', got {result.status!r}"
    )
    assert "unknown_db" in result.message, (
        f"error message should mention 'unknown_db', got {result.message!r}"
    )


def test_fixture_ref_no_session_returns_error(tmp: TempDir):
    """FixtureRef field with session=None returns error result, not None injection."""
    f = tmp / "test_bad.py"
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef\n"
        "@dataclass(frozen=True)\n"
        "class DbCase:\n"
        "    db: FixtureRef[str]\n"
        "def my_db(): return 'x'\n"
        "@oxitest.parametrize(pg=DbCase(db=my_db))\n"
        "def test_db(db: Fixture[str]) -> None:\n"  # expanded mode
        "    pass\n"
    )
    result = executor_run_test(str(f), "test_db", session=None, param_id="pg")
    assert result.status == "error", (
        f"FixtureRef with session=None should produce status='error', got "
        f"{result.status!r}"
    )
    assert "my_db" in result.message, (
        f"error message should mention 'my_db', got {result.message!r}"
    )


def test_parametrize_rejects_non_callable_for_fixture_ref_field():
    """FixtureRef[T] fields must hold callables — non-callable raises TypeError."""
    from oxitest import FixtureRef

    @dataclass(frozen=True)
    class RefCase:
        db: FixtureRef[int]

    with raises(TypeError, match="FixtureRef"):

        @parametrize(bad=RefCase(db=42))  # type: ignore[arg-type]
        def test_foo(db: Fixture[int]) -> None:
            pass


def test_parametrize_dict_mode_stamps_function():
    @parametrize(basic=dict(x=1, y=2, expected=3))
    def test_foo(x: int, y: int, expected: int) -> None:
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    param_cases = get_metadata(test_foo).param_cases
    assert isinstance(param_cases, _DictCases), (
        f"dict mode should stamp _DictCases, got {type(param_cases)!r}"
    )
    assert param_cases.cases == {"basic": {"x": 1, "y": 2, "expected": 3}}, (
        f"dict mode should store cases correctly, got {param_cases.cases!r}"
    )


def test_parametrize_dict_mode_multiple_cases():
    @parametrize(
        basic=dict(x=1, y=2, expected=3),
        zero_sum=dict(x=0, y=0, expected=0),
    )
    def test_foo(x: int, y: int, expected: int) -> None:
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    param_cases = get_metadata(test_foo).param_cases
    assert isinstance(param_cases, _DictCases), (
        f"dict mode should stamp _DictCases, got {type(param_cases)!r}"
    )
    assert len(param_cases.cases) == 2, (
        f"dict mode with 2 cases should produce 2 entries, got {len(param_cases.cases)}"
    )


def test_parametrize_dict_mode_rejects_extra_key():
    with raises(TypeError, match="unexpected key"):

        @parametrize(basic=dict(x=1, y=2, expeced=3))  # codespell:ignore expeced
        def test_foo(x: int, y: int, expected: int) -> None:
            pass


def test_parametrize_dict_mode_rejects_missing_key():
    with raises(TypeError, match="missing key"):

        @parametrize(basic=dict(x=1, y=2))
        def test_foo(x: int, y: int, expected: int) -> None:
            pass


def test_parametrize_dict_mode_rejects_non_dict_case():
    with raises(TypeError, match="must be a dict"):

        @parametrize(
            basic=dict(x=1),
            bad=42,
        )
        def test_foo(x: int) -> None:
            pass


def test_parametrize_dict_mode_excludes_fixture_params_from_schema():
    """Fixture[T] params are not required in the dict — session resolves them."""

    @parametrize(basic=dict(x=2, expected=20))
    def test_foo(x: int, expected: int, multiplier: Fixture[int]) -> None:
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    param_cases = get_metadata(test_foo).param_cases
    assert isinstance(param_cases, _DictCases), (
        f"dict mode should stamp _DictCases, got {type(param_cases)!r}"
    )
    assert param_cases.cases == {"basic": {"x": 2, "expected": 20}}, (
        f"dict mode should not include Fixture params in schema,"
        f" got {param_cases.cases!r}"
    )


def test_executor_dict_mode_passes(tmp: TempDir):
    """Dict mode: case values injected as individual kwargs, test passes."""
    f = tmp / "test_add.py"
    f.write_text(
        "import oxitest\n"
        "@oxitest.parametrize(\n"
        "    basic=dict(x=1, y=2, expected=3),\n"
        "    negative=dict(x=-1, y=5, expected=4),\n"
        ")\n"
        "def test_add(x: int, y: int, expected: int) -> None:\n"
        "    assert x + y == expected\n"
    )
    result_basic = executor_run_test(str(f), "test_add", session=None, param_id="basic")
    result_neg = executor_run_test(
        str(f), "test_add", session=None, param_id="negative"
    )
    assert result_basic.status == "passed", result_basic.message
    assert result_neg.status == "passed", result_neg.message


def test_executor_dict_mode_failure(tmp: TempDir):
    """Dict mode: failing assertion produces 'failed' status."""
    f = tmp / "test_add.py"
    f.write_text(
        "import oxitest\n"
        "@oxitest.parametrize(wrong=dict(x=1, y=2, expected=99))\n"
        "def test_add(x: int, y: int, expected: int) -> None:\n"
        "    assert x + y == expected\n"
    )
    result = executor_run_test(str(f), "test_add", session=None, param_id="wrong")
    assert result.status == "failed", (
        f"wrong expected value in dict mode should produce status='failed', got "
        f"{result.status!r}"
    )


def test_executor_dict_mode_with_fixture(tmp: TempDir):
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
    session = create_session([str(conftest)])
    session.begin_module(str(f))
    result = executor_run_test(str(f), "test_mul", session=session, param_id="double")
    assert result.status == "passed", result.message


def test_collect_dict_parametrize_expands_to_n_items(tmp: TempDir):
    f = tmp / "test_add.py"
    f.write_text(
        "import oxitest\n"
        "@oxitest.parametrize(\n"
        "    basic=dict(x=1, y=2),\n"
        "    neg=dict(x=-1, y=2),\n"
        ")\n"
        "def test_add(x: int, y: int) -> None:\n"
        "    assert x + y > 0\n"
    )
    items, _ = collect_module(str(f))
    assert len(items) == 2, (
        f"dict parametrize with 2 cases should yield 2 items, got {len(items)}"
    )
    param_ids = [i.param_id for i in items]
    assert "basic" in param_ids, (
        f"'basic' param_id should be collected, got {param_ids}"
    )
    assert "neg" in param_ids, f"'neg' param_id should be collected, got {param_ids}"


def test_collect_dict_parametrize_item_has_param_values(tmp: TempDir):
    f = tmp / "test_add.py"
    f.write_text(
        "import oxitest\n"
        "@oxitest.parametrize(basic=dict(x=1, y=2))\n"
        "def test_add(x: int, y: int) -> None:\n"
        "    pass\n"
    )
    items, _ = collect_module(str(f))
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


def test_parametrize_inferred_type_stamps_function():
    """Dataclass mode: type inferred from first case value, no explicit type arg."""

    @parametrize(basic=AddCase(x=1, y=2, expected=3))
    def test_foo(x, y, expected):
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    param_cases = get_metadata(test_foo).param_cases
    assert isinstance(param_cases, _DataclassCases), (
        f"dataclass mode should stamp _DataclassCases, got {type(param_cases)!r}"
    )
    assert param_cases.param_type is AddCase, (
        f"param_type should be inferred as AddCase, got {param_cases.param_type!r}"
    )
    assert param_cases.cases == {"basic": AddCase(x=1, y=2, expected=3)}, (
        f"cases should store the case value, got {param_cases.cases!r}"
    )


def test_parametrize_rejects_invalid_case_type():
    """Non-dict, non-dataclass case value raises TypeError at decoration time."""
    with raises(
        TypeError, match="case values must be dicts or frozen dataclass instances"
    ):

        @parametrize(basic=42)
        def test_foo(x: int) -> None:
            pass


# ── FixtureRef namespace-aware resolution ─────────────────────────────────────


def test_fixture_ref_uses_namespace_qualified_lookup_when_namespace_present(
    tmp: TempDir,
):
    """FixtureRef function with FixtureDef.namespace uses namespace-qualified lookup.

    Two namespaces 'db' and 'http' both define a fixture named 'conn'.  'http'
    is registered after 'db', so flat lookup would return the http version.
    The FixtureRef holds the db-namespace function, whose FixtureDef.namespace
    is "db", so the result must be the db value.
    """
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "import oxitest\n"
        "db = oxitest.Fixtures()\n"
        "http = oxitest.Fixtures()\n"
        "\n"
        "@db.fixture\n"
        "def conn():\n"
        "    return 'db-conn'\n"
        "\n"
        "@http.fixture\n"
        "def conn():  # same name, different namespace\n"
        "    return 'http-conn'\n"
    )
    # Test file imports the db-namespace conn from conftest. The registry
    # resolves the namespace via FixtureDef.namespace (matched by func identity).
    # Without namespace-aware lookup the flat registry returns the http version
    # (last registered wins).
    f = tmp / "test_ns.py"
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef\n"
        "from conftest import db as _db_ns\n"
        "\n"
        "_conn_ref = None\n"
        "for _defn in _db_ns._defs:\n"
        "    if _defn.name == 'conn':\n"
        "        _conn_ref = _defn.func\n"
        "        break\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class StoreCase:\n"
        "    store: FixtureRef[str]\n"
        "\n"
        "@oxitest.parametrize(prod=StoreCase(store=_conn_ref))\n"
        "def test_query(store: Fixture[str]) -> None:\n"
        "    assert store == 'db-conn'\n"
    )
    session = create_session([str(conftest)])
    session.begin_module(str(f))
    result = executor_run_test(str(f), "test_query", session=session, param_id="prod")
    assert result.status == "passed", result.message


def test_fixture_ref_falls_back_to_flat_lookup_when_no_namespace(tmp: TempDir):
    """FixtureRef function without a namespace falls back to flat get_fixture.

    The fixture function is not registered in the session's registry (defined
    locally in the test file), so the registry returns no namespace and the
    executor falls back to the flat get_fixture call.
    """
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "import oxitest\n"
        "fixtures = oxitest.Fixtures()\n"
        "\n"
        "@fixtures.fixture\n"
        "def pg_db():\n"
        "    return 'flat-pg'\n"
    )
    # The test file defines a local stub (NOT imported from conftest) so it will
    # NOT be in the session registry.  The flat lookup must still resolve it.
    f = tmp / "test_flat.py"
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef\n"
        "\n"
        "_fixtures = oxitest.Fixtures()\n"
        "\n"
        "@_fixtures.fixture\n"
        "def pg_db(): return 'flat-pg'\n"
        "# pg_db defined locally: not in conftest registry\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class DbCase:\n"
        "    db: FixtureRef[str]\n"
        "\n"
        "@oxitest.parametrize(pg=DbCase(db=pg_db))\n"
        "def test_db(db: Fixture[str]) -> None:\n"
        "    assert db == 'flat-pg'\n"
    )
    session = create_session([str(conftest)])
    session.begin_module(str(f))
    result = executor_run_test(str(f), "test_db", session=session, param_id="pg")
    assert result.status == "passed", result.message


# ── Mode class unit tests ──────────────────────────────────────────────────────


def test_dict_cases_items_yields_repr_pairs():
    dc = _DictCases(cases={"basic": {"x": 1, "y": 2}})
    result = list(dc.items())
    assert result == [("basic", [("x", "1"), ("y", "2")])], (
        f"_DictCases.items() should yield (case_id, [(key, repr(val))...]),"
        f" got {result}"
    )


def test_dict_cases_resolve_returns_kwargs_and_empty_fixrefs():
    dc = _DictCases(cases={"basic": {"x": 1, "y": 2}})
    kwargs, fixrefs = dc.resolve(lambda x, y: None, "basic")
    assert kwargs == {"x": 1, "y": 2}, f"resolve should return case dict, got {kwargs}"
    assert fixrefs == frozenset(), f"dict mode fixrefs should be empty, got {fixrefs}"


def test_dataclass_cases_items_yields_field_repr_pairs():
    dc = _DataclassCases(
        cases={"basic": AddCase(x=1, y=2, expected=3)},
        param_type=AddCase,
        fixref_fields=[],
    )
    result = list(dc.items())
    assert result == [("basic", [("x", "1"), ("y", "2"), ("expected", "3")])], (
        f"_DataclassCases.items() should yield (case_id, [(field, repr(val))...]),"
        f" got {result}"
    )


def test_dataclass_cases_resolve_expanded_mode():
    dc = _DataclassCases(
        cases={"basic": AddCase(x=1, y=2, expected=3)},
        param_type=AddCase,
        fixref_fields=[],
    )

    def test_fn(x: int, y: int, expected: int) -> None:
        pass

    kwargs, fixrefs = dc.resolve(test_fn, "basic")
    assert kwargs == {"x": 1, "y": 2, "expected": 3}, (
        f"expanded mode should spread fields as kwargs, got {kwargs}"
    )
    assert fixrefs == frozenset(), f"no FixtureRef fields, got {fixrefs}"


def test_dataclass_cases_resolve_compact_mode():
    dc = _DataclassCases(
        cases={"basic": AddCase(x=1, y=2, expected=3)},
        param_type=AddCase,
        fixref_fields=[],
    )

    def test_fn(params: AddCase) -> None:
        pass

    kwargs, fixrefs = dc.resolve(test_fn, "basic")
    assert kwargs == {"params": AddCase(x=1, y=2, expected=3)}, (
        f"compact mode should pass whole instance as single param, got {kwargs}"
    )
    assert fixrefs == frozenset(), f"no FixtureRef fields, got {fixrefs}"


def test_build_dict_cases_validates_extra_key():
    from oxitest import raises
    from oxitest._bridge.parametrize import _build_dict_cases

    def test_fn(x: int, y: int) -> None:
        pass

    with raises(TypeError, match="unexpected key"):
        _build_dict_cases({"basic": {"x": 1, "y": 2, "z": 3}}, test_fn)


def test_build_dict_cases_validates_missing_key():
    from oxitest import raises
    from oxitest._bridge.parametrize import _build_dict_cases

    def test_fn(x: int, y: int) -> None:
        pass

    with raises(TypeError, match="missing key"):
        _build_dict_cases({"basic": {"x": 1}}, test_fn)


def test_build_dataclass_cases_rejects_non_frozen():
    from oxitest import raises
    from oxitest._bridge.parametrize import _build_dataclass_cases

    @dataclass
    class Mutable:
        x: int

    with raises(TypeError, match="frozen=True"):
        _build_dataclass_cases({"basic": Mutable(x=1)})


def test_build_dataclass_cases_rejects_mixed_types():
    from oxitest import raises
    from oxitest._bridge.parametrize import _build_dataclass_cases

    @dataclass(frozen=True)
    class OtherCase:
        z: int

    with raises(TypeError, match="instance of 'AddCase'"):
        _build_dataclass_cases(
            {"a": AddCase(x=1, y=2, expected=3), "b": OtherCase(z=1)}
        )


def test_fixture_ref_no_session_with_namespace_returns_error(tmp: TempDir):
    """FixtureRef with namespace and session=None returns error.

    When no session is available (session=None), the executor uses a
    _NullFixtureSession which has no registry. Loading the conftest via
    create_session provides the registry and namespace. Without it, the
    fixture resolution fails.
    """
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "import oxitest\n"
        "db = oxitest.Fixtures()\n"
        "\n"
        "@db.fixture\n"
        "def conn():\n"
        "    return 'db-conn'\n"
    )
    # Load conftest so sys.modules["conftest"] has db available for import
    load_fixtures_from_conftest(str(conftest))
    f = tmp / "test_ns_err.py"
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef\n"
        "from conftest import db as _db_ns\n"
        "\n"
        "_conn_ref = None\n"
        "for _defn in _db_ns._defs:\n"
        "    if _defn.name == 'conn':\n"
        "        _conn_ref = _defn.func\n"
        "        break\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class StoreCase:\n"
        "    store: FixtureRef[str]\n"
        "\n"
        "@oxitest.parametrize(prod=StoreCase(store=_conn_ref))\n"
        "def test_query(store: Fixture[str]) -> None:\n"
        "    assert store == 'db-conn'\n"
    )
    result = executor_run_test(str(f), "test_query", session=None, param_id="prod")
    assert result.status == "error", (
        f"FixtureRef with namespace and session=None should produce status='error', "
        f"got {result.status!r}"
    )
    assert "conn" in result.message, (
        f"error message should mention fixture name 'conn', got {result.message!r}"
    )


def test_parametrize_rejects_empty_cases_direct():
    from oxitest import raises
    from oxitest._bridge.parametrize import parametrize

    with raises(TypeError, match="at least one case"):
        parametrize()


def test_parametrize_rejects_non_dataclass_non_dict_direct():
    from oxitest import raises
    from oxitest._bridge.parametrize import parametrize

    with raises(
        TypeError, match="case values must be dicts or frozen dataclass instances"
    ):

        @parametrize(basic=42)
        def test_fn(x: int) -> None:
            pass
