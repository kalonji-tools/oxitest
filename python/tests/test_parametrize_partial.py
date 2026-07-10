"""Tests for parametrize composition via partial().

Stacking, cartesian product, validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from oxitest import FixtureRef, TempDir, helpers, parametrize, partial, raises
from oxitest._bridge._fn_metadata import get_metadata
from oxitest._bridge.conftest_loader import create_session
from oxitest._bridge.importer import collect_module
from oxitest._bridge.parametrize import ComposedCases, DataclassCases, DictCases


@dataclass(frozen=True)
class AddCase:
    """Frozen dataclass used as parametrize case type in addition tests."""

    x: int
    y: int
    expected: int


@dataclass
class MathCase:
    """Mutable dataclass used to test partial() composition with non-frozen types."""

    x: int
    y: int
    expected: int


def test_partial_stores_target_type_and_fields() -> None:
    """partial() stores the target type, provided field values, and field name set."""
    p = partial(MathCase, x=1, y=2)

    assert p.target_type is MathCase, (
        f"partial should store target type, got {p.target_type!r}"
    )
    assert p.fields == {"x": 1, "y": 2}, (
        f"partial should store provided fields, got {p.fields!r}"
    )
    assert p.provided_fields == frozenset({"x", "y"}), (
        f"partial should store provided field names, got {p.provided_fields!r}"
    )


def test_partial_rejects_non_dataclass() -> None:
    """partial() rejects a target type that is not a dataclass."""
    with raises(TypeError, match="must be a dataclass"):
        partial(int, x=1)


def test_partial_rejects_empty_fields() -> None:
    """partial() with no field kwargs raises TypeError."""
    with raises(TypeError, match="at least one field"):
        partial(MathCase)


def test_partial_rejects_unknown_field() -> None:
    """partial() raises TypeError when a kwarg name is not a field on the dataclass."""
    with raises(TypeError, match="unknown field"):
        partial(MathCase, x=1, typo=2)


def test_partial_detects_fixref_fields() -> None:
    """partial() identifies FixtureRef fields and stores them in fixref_fields."""

    @dataclass
    class DbCase:
        db: FixtureRef[str]
        label: str

    def my_db() -> str:
        return "pg"

    p = partial(DbCase, db=my_db)
    assert p.fixref_fields == ("db",), (
        f"partial should detect FixtureRef fields, got {p.fixref_fields!r}"
    )


def test_partial_rejects_non_callable_fixref() -> None:
    """partial() raises TypeError when a FixtureRef field gets a non-callable value."""

    @dataclass
    class DbCase:
        db: FixtureRef[str]

    with raises(TypeError, match="FixtureRef"):
        partial(DbCase, db=42)


# ── ResolvedCases (partial/composed) tests ───────────────────────────────────


def test_partial_cases_items_yields_field_repr_pairs() -> None:
    """ComposedCases.items() yields (case_id, [(field, repr(val))...]) per layer."""
    p = partial(MathCase, x=1, y=2, expected=3)
    pc = ComposedCases(
        cases=MappingProxyType({"add": p}),
        param_type=MathCase,
        provided_fields=frozenset({"x", "y", "expected"}),
        fixref_fields=(),
    )

    result = list(pc.items())
    assert result == [("add", [("x", "1"), ("y", "2"), ("expected", "3")])], (
        "ResolvedCases.items() (composed mode) should"
        f" yield (case_id, [(field, repr(val))...]),"
        f" got {result}"
    )


def test_parametrize_stacks_partial_layers() -> None:
    """Two @parametrize(partial) decorators produce a 2-tuple of ComposedCases."""

    @parametrize(pg=partial(MathCase, x=1))
    @parametrize(add=partial(MathCase, y=2, expected=3))
    def test_fn(x: int, y: int, expected: int) -> None:
        pass

    meta = get_metadata(test_fn)
    assert isinstance(meta.param_cases, tuple), (
        f"stacked parametrize should produce a tuple, got {type(meta.param_cases)!r}"
    )
    assert len(meta.param_cases) == 2, (
        f"two stacked decorators should produce 2-tuple, got {len(meta.param_cases)}"
    )
    assert all(isinstance(layer, ComposedCases) for layer in meta.param_cases), (
        f"all layers should be ComposedCases,"
        f" got {[type(layer).__name__ for layer in meta.param_cases]}"
    )


def test_parametrize_single_full_dataclass_is_1_tuple() -> None:
    """A single @parametrize with full dataclass cases yields a 1-tuple."""

    @parametrize(basic=AddCase(x=1, y=2, expected=3))
    def test_fn(x: int, y: int, expected: int) -> None:
        pass

    meta = get_metadata(test_fn)
    assert isinstance(meta.param_cases, tuple), (
        f"single parametrize should produce a tuple, got {type(meta.param_cases)!r}"
    )
    assert len(meta.param_cases) == 1, (
        f"single decorator should produce 1-tuple, got {len(meta.param_cases)}"
    )
    assert isinstance(meta.param_cases[0], DataclassCases), (
        "layer should be DataclassCases (dataclass mode),"
        f" got {type(meta.param_cases[0]).__name__}"
    )


def test_parametrize_single_dict_is_1_tuple() -> None:
    """A single @parametrize with a dict case produces a 1-tuple of DictCases."""

    @parametrize(basic={"x": 1, "y": 2, "expected": 3})
    def test_fn(x: int, y: int, expected: int) -> None:
        pass

    meta = get_metadata(test_fn)
    assert isinstance(meta.param_cases, tuple), (
        "single dict parametrize should produce a tuple,"
        f" got {type(meta.param_cases)!r}"
    )
    assert len(meta.param_cases) == 1, (
        f"single dict decorator should produce 1-tuple, got {len(meta.param_cases)}"
    )
    assert isinstance(meta.param_cases[0], DictCases), (
        "layer should be DictCases (dict mode),"
        f" got {type(meta.param_cases[0]).__name__}"
    )


def test_parametrize_rejects_mixing_partial_and_full() -> None:
    """Stacking partial and full dataclass @parametrize raises TypeError."""
    with raises(TypeError, match="cannot mix"):

        @parametrize(pg=partial(MathCase, x=1))
        @parametrize(basic=AddCase(x=1, y=2, expected=3))
        def test_fn(x: int, y: int, expected: int) -> None:
            pass


def test_parametrize_rejects_partial_different_target_type() -> None:
    """Stacking partial() layers with different dataclass types raises TypeError."""

    @dataclass
    class OtherCase:
        z: int

    with raises(TypeError, match="same dataclass type"):

        @parametrize(pg=partial(MathCase, x=1))
        @parametrize(add=partial(OtherCase, z=2))
        def test_fn(x: int, z: int) -> None:
            pass


def test_parametrize_rejects_overlapping_fields() -> None:
    """Stacking partial() layers that assign the same field raises TypeError."""
    with raises(TypeError, match="overlap"):

        @parametrize(pg=partial(MathCase, x=1, y=2))
        @parametrize(add=partial(MathCase, y=3, expected=4))
        def test_fn(x: int, y: int, expected: int) -> None:
            pass


# ── Cartesian product expansion tests ────────────────────────────────────────


def test_collect_composed_parametrize_expands_cartesian_product(tmp: TempDir) -> None:
    """Two stacked @parametrize layers produce the full cartesian product of cases."""
    path = helpers.common.write_test_module(
        tmp,
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import partial\n"
        "@dataclass\n"
        "class Case:\n"
        "    x: int\n"
        "    y: int\n"
        "@oxitest.parametrize(a=partial(Case, x=1), b=partial(Case, x=2))\n"
        "@oxitest.parametrize(c=partial(Case, y=10), d=partial(Case, y=20))\n"
        "def test_math(x: int, y: int) -> None:\n"
        "    pass\n",
    )
    items, _ = collect_module(path)
    assert len(items) == 4, (
        f"2x2 composition should yield 4 items, got {len(items)}: "
        f"{[i.param_id for i in items]}"
    )
    param_ids = sorted(i.param_id for i in items)
    assert param_ids == ["a-c", "a-d", "b-c", "b-d"], (
        f"expected cartesian product IDs, got {param_ids}"
    )


def test_collect_composed_parametrize_has_merged_param_values(tmp: TempDir) -> None:
    """Composed cases carry merged param_values from all contributing partial layers."""
    path = helpers.common.write_test_module(
        tmp,
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import partial\n"
        "@dataclass\n"
        "class Case:\n"
        "    x: int\n"
        "    y: int\n"
        "@oxitest.parametrize(a=partial(Case, x=1))\n"
        "@oxitest.parametrize(c=partial(Case, y=10))\n"
        "def test_math(x: int, y: int) -> None:\n"
        "    pass\n",
    )
    items, _ = collect_module(path)
    assert len(items) == 1, f"1x1 composition should yield 1 item, got {len(items)}"
    item = items[0]
    assert item.param_id == "a-c", f"expected 'a-c', got {item.param_id!r}"
    assert ("x", "1") in item.param_values, (
        f"('x', '1') should be in param_values, got {item.param_values}"
    )
    assert ("y", "10") in item.param_values, (
        f"('y', '10') should be in param_values, got {item.param_values}"
    )


def test_collect_composed_rejects_single_partial_layer(tmp: TempDir) -> None:
    """A single partial() layer with unfilled fields raises TypeError at collect."""
    path = helpers.common.write_test_module(
        tmp,
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import partial\n"
        "@dataclass\n"
        "class Case:\n"
        "    x: int\n"
        "    y: int\n"
        "@oxitest.parametrize(a=partial(Case, x=1))\n"
        "def test_fn(x: int, y: int) -> None:\n"
        "    pass\n",
    )
    with raises(TypeError, match="requires at least 2"):
        collect_module(path)


def test_collect_composed_rejects_incomplete_fields(tmp: TempDir) -> None:
    """Composed partial layers that leave a field unset raise TypeError at collect."""
    path = helpers.common.write_test_module(
        tmp,
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import partial\n"
        "@dataclass\n"
        "class Case:\n"
        "    x: int\n"
        "    y: int\n"
        "    z: int\n"
        "@oxitest.parametrize(a=partial(Case, x=1))\n"
        "@oxitest.parametrize(c=partial(Case, y=10))\n"
        "def test_fn(x: int, y: int, z: int) -> None:\n"
        "    pass\n",
    )
    with raises(TypeError, match="missing field"):
        collect_module(path)


def test_collect_composed_3_layers(tmp: TempDir) -> None:
    """Three stacked partial() layers correctly compose into one combined case."""
    path = helpers.common.write_test_module(
        tmp,
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import partial\n"
        "@dataclass\n"
        "class Case:\n"
        "    x: int\n"
        "    y: int\n"
        "    z: int\n"
        "@oxitest.parametrize(a=partial(Case, x=1))\n"
        "@oxitest.parametrize(b=partial(Case, y=2))\n"
        "@oxitest.parametrize(c=partial(Case, z=3))\n"
        "def test_fn(x: int, y: int, z: int) -> None:\n"
        "    pass\n",
    )
    items, _ = collect_module(path)
    assert len(items) == 1, f"1x1x1 should yield 1 item, got {len(items)}"
    assert items[0].param_id == "a-b-c", f"expected 'a-b-c', got {items[0].param_id!r}"


# ── Composed resolution tests ─────────────────────────────────────────────────


def test_executor_composed_parametrize_passes(tmp: TempDir) -> None:
    """Executor correctly injects merged field values from composed partial layers."""
    result = helpers.common.exec_inline(
        tmp,
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import partial\n"
        "@dataclass\n"
        "class Case:\n"
        "    x: int\n"
        "    y: int\n"
        "    expected: int\n"
        "@oxitest.parametrize(a=partial(Case, x=1))\n"
        "@oxitest.parametrize(c=partial(Case, y=2, expected=3))\n"
        "def test_add(x: int, y: int, expected: int) -> None:\n"
        "    assert x + y == expected\n",
        "test_add",
        param_id="a-c",
    )
    assert result.status == "passed", (
        f"composed parametrize should pass, got status={result.status!r}, "
        f"msg={result.message!r}"
    )


def test_executor_composed_parametrize_failure(tmp: TempDir) -> None:
    """A wrong expected value in composed parametrize produces a failed result."""
    result = helpers.common.exec_inline(
        tmp,
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import partial\n"
        "@dataclass\n"
        "class Case:\n"
        "    x: int\n"
        "    y: int\n"
        "    expected: int\n"
        "@oxitest.parametrize(a=partial(Case, x=1))\n"
        "@oxitest.parametrize(c=partial(Case, y=2, expected=99))\n"
        "def test_add(x: int, y: int, expected: int) -> None:\n"
        "    assert x + y == expected\n",
        "test_add",
        param_id="a-c",
    )
    assert result.status == "failed", (
        f"wrong expected should fail, got status={result.status!r}"
    )


def test_executor_composed_with_fixture(tmp: TempDir) -> None:
    """Composed partial cases combine correctly with injected Fixture[T] parameters."""
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
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, partial\n"
        "@dataclass\n"
        "class Case:\n"
        "    x: int\n"
        "    expected: int\n"
        "@oxitest.parametrize(a=partial(Case, x=2))\n"
        "@oxitest.parametrize(c=partial(Case, expected=20))\n"
        "def test_mul(x: int, expected: int, multiplier: Fixture[int]) -> None:\n"
        "    assert x * multiplier == expected\n"
    )
    session, _ = create_session([str(conftest)])
    result = helpers.common.run_test(
        str(f), "test_mul", session=session, param_id="a-c"
    )
    assert result.status == "passed", result.message


def test_executor_composed_compact_mode(tmp: TempDir) -> None:
    """Compact-mode composed parametrize injects the assembled dataclass instance."""
    result = helpers.common.exec_inline(
        tmp,
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import partial\n"
        "@dataclass\n"
        "class Case:\n"
        "    x: int\n"
        "    y: int\n"
        "@oxitest.parametrize(a=partial(Case, x=1))\n"
        "@oxitest.parametrize(c=partial(Case, y=2))\n"
        "def test_compact(case: Case) -> None:\n"
        "    assert case.x + case.y == 3\n",
        "test_compact",
        param_id="a-c",
    )
    assert result.status == "passed", (
        f"compact mode with composition should pass, got status={result.status!r}, "
        f"msg={result.message!r}"
    )


def test_executor_composed_with_fixture_ref(tmp: TempDir) -> None:
    """FixtureRef inside a composed partial case resolves the fixture at execution."""
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "import oxitest\n"
        "fixtures = oxitest.Fixtures()\n"
        "@fixtures.fixture\n"
        "def pg_db():\n"
        "    return 'postgres'\n"
    )
    f = tmp / "test_db.py"
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef, partial\n"
        "_fixtures = oxitest.Fixtures()\n"
        "@_fixtures.fixture\n"
        "def pg_db(): return 'postgres'\n"
        "@dataclass\n"
        "class Case:\n"
        "    db: FixtureRef[str]\n"
        "    expected: str\n"
        "@oxitest.parametrize(pg=partial(Case, db=pg_db))\n"
        "@oxitest.parametrize(check=partial(Case, expected='postgres'))\n"
        "def test_db(db: Fixture[str], expected: str) -> None:\n"
        "    assert db == expected\n"
    )
    session, _ = create_session([str(conftest)])
    result = helpers.common.run_test(
        str(f), "test_db", session=session, param_id="pg-check"
    )
    assert result.status == "passed", result.message
