"""Tests for parametrize collection: expansion to items, collection-time validation."""

from __future__ import annotations

from oxitest import TempDir, helpers, raises
from oxitest._bridge.conftest_loader import load_fixtures_from_conftest
from oxitest._bridge.importer import collect_module

# ── Group B: Collection expansion ─────────────────────────────────────────────


def test_collect_parametrize_expands_to_n_items(tmp: TempDir) -> None:
    """collect_module expands an N-case @parametrize into N CollectedItems."""
    path = helpers.common.write_test_module(
        tmp,
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "@dataclass(frozen=True)\n"
        "class AddCase:\n"
        "    x: int\n"
        "    y: int\n"
        "@oxitest.parametrize("
        "basic=AddCase(x=1, y=2), neg=AddCase(x=-1, y=2))\n"
        "def test_add(x, y):\n"
        "    assert x + y > 0\n",
    )
    items, _ = collect_module(path)
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


def test_collect_parametrize_item_has_param_values(tmp: TempDir) -> None:
    """Each collected parametrize item carries field values as param_values pairs."""
    path = helpers.common.write_test_module(
        tmp,
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "@dataclass(frozen=True)\n"
        "class AddCase:\n"
        "    x: int\n"
        "    y: int\n"
        "@oxitest.parametrize(basic=AddCase(x=1, y=2))\n"
        "def test_add(x, y):\n"
        "    pass\n",
    )
    items, _ = collect_module(path)
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


def test_collect_non_parametrize_has_none_param_id(tmp: TempDir) -> None:
    """Non-parametrized tests have param_id=None and empty param_values."""
    path = helpers.common.write_test_module(tmp, "def test_foo(): pass\n")
    items, _ = collect_module(path)
    assert len(items) == 1, f"expected 1 item, got {len(items)}"
    assert items[0].param_id is None, (
        f"non-parametrized test should have param_id=None, got {items[0].param_id!r}"
    )
    assert items[0].param_values == (), (
        f"non-parametrized test should have empty param_values, got "
        f"{items[0].param_values}"
    )


# ── Group G: Collection-time validation ───────────────────────────────────────


def test_dict_parametrize_rejects_extra_key(tmp: TempDir) -> None:
    """collect_module raises ImportError when a dict case has an unexpected key."""
    code = (
        "import oxitest\n"
        "\n"
        "@oxitest.parametrize(basic={'x': 1, 'y': 2, 'z': 3})\n"
        "def test_fn(x: int, y: int) -> None:\n"
        "    pass\n"
    )
    with raises(ImportError, match="unexpected key"):
        collect_module(helpers.common.write_test_module(tmp, code))


def test_dict_parametrize_rejects_missing_key(tmp: TempDir) -> None:
    """collect_module raises ImportError when a dict case is missing a required key."""
    code = (
        "import oxitest\n"
        "\n"
        "@oxitest.parametrize(basic={'x': 1})\n"
        "def test_fn(x: int, y: int) -> None:\n"
        "    pass\n"
    )
    with raises(ImportError, match="missing key"):
        collect_module(helpers.common.write_test_module(tmp, code))


def test_dataclass_parametrize_rejects_non_frozen(tmp: TempDir) -> None:
    """collect_module raises ImportError for a case that is a mutable dataclass."""
    code = (
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "\n"
        "@dataclass\n"
        "class Mutable:\n"
        "    x: int\n"
        "\n"
        "@oxitest.parametrize(basic=Mutable(x=1))\n"
        "def test_fn(x: int) -> None:\n"
        "    pass\n"
    )
    with raises(ImportError, match="frozen=True"):
        collect_module(helpers.common.write_test_module(tmp, code))


def test_dataclass_parametrize_rejects_mixed_types(tmp: TempDir) -> None:
    """collect_module raises ImportError when cases have mixed dataclass types."""
    code = (
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class CaseA:\n"
        "    x: int\n"
        "    y: int\n"
        "    expected: int\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class CaseB:\n"
        "    z: int\n"
        "\n"
        "@oxitest.parametrize(a=CaseA(x=1, y=2, expected=3), b=CaseB(z=1))\n"
        "def test_fn(x: int, y: int, expected: int) -> None:\n"
        "    pass\n"
    )
    with raises(ImportError, match="instance of 'CaseA'"):
        collect_module(helpers.common.write_test_module(tmp, code))


def test_fixture_ref_no_session_with_namespace_returns_error(tmp: TempDir) -> None:
    """FixtureRef with namespace and session=None produces an error result."""
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
    result = helpers.common.run_test(
        str(f), "test_query", session=None, param_id="prod"
    )
    assert result.status == "error", (
        f"FixtureRef with namespace and session=None should produce status='error', "
        f"got {result.status!r}"
    )
    assert "conn" in result.message, (
        f"error message should mention fixture name 'conn', got {result.message!r}"
    )
