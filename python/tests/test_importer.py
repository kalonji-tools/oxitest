from __future__ import annotations

import textwrap

from oxitest import TempDir, raises
from oxitest._bridge.importer import (
    _check_fn_violations,
    _propagate_class_marks,
    collect_module,
)
from oxitest._bridge.parametrize import _DictCases
from oxitest._bridge.result import CollectedItem, ViolationKind


def test_collect_empty_module(tmp: TempDir):
    f = tmp / "test_empty.py"
    f.write_text("")
    items, _ = collect_module(str(f))
    assert items == [], f"collecting an empty module should yield no items, got {items}"


def test_collect_single_test_function(tmp: TempDir):
    f = tmp / "test_foo.py"
    f.write_text("def test_bar(): pass\n")
    items, _ = collect_module(str(f))
    assert len(items) == 1, f"expected 1 collected item, got {len(items)}: {items}"
    assert items[0].fn_name == "test_bar", (
        f"expected fn_name='test_bar', got {items[0].fn_name!r}"
    )
    assert items[0].lineno == 1, (
        f"expected lineno=1 for first function, got {items[0].lineno}"
    )
    assert items[0].markers == [], (
        f"expected no markers on unmarked function, got {items[0].markers}"
    )


def test_collect_multiple_functions(tmp: TempDir):
    f = tmp / "test_multi.py"
    f.write_text("def test_one(): pass\ndef test_two(): pass\n")
    items, _ = collect_module(str(f))
    assert len(items) == 2, (
        f"expected 2 items, got {len(items)}: {[i.fn_name for i in items]}"
    )
    names = [item.fn_name for item in items]
    assert "test_one" in names, f"'test_one' should be collected, got names: {names}"
    assert "test_two" in names, f"'test_two' should be collected, got names: {names}"


def test_collect_ignores_non_test_functions(tmp: TempDir):
    f = tmp / "test_foo.py"
    f.write_text("def helper(): pass\ndef test_real(): pass\n")
    items, _ = collect_module(str(f))
    assert len(items) == 1, (
        f"helper functions should not be collected, expected 1 item got {len(items)}: "
        f"{[i.fn_name for i in items]}"
    )
    assert items[0].fn_name == "test_real", (
        f"only 'test_real' should be collected, got {items[0].fn_name!r}"
    )


def test_collect_raises_on_missing_file():
    with raises(Exception):
        collect_module("/nonexistent/path/test_foo.py")


def test_collect_error_message_is_clean_traceback_not_testrepr(tmp: TempDir):
    """collect_module error: plain traceback, no TestResult repr, real newlines."""
    f = tmp / "test_bad_import.py"
    f.write_text("import _oxitest_nonexistent_module_xyz\n")
    try:
        collect_module(str(f))
        assert False, "expected ImportError"  # noqa: PT015
    except ImportError as e:
        msg = str(e)
        assert "TestResult(" not in msg, (
            f"message must not be TestResult repr, got: {msg!r}"
        )
        assert "\n" in msg, f"traceback must contain real newlines, got: {msg!r}"
        assert "Traceback" in msg, f"traceback header missing, got: {msg!r}"
        assert "ModuleNotFoundError" in msg or "ImportError" in msg, (
            f"actual error type missing from message, got: {msg!r}"
        )


def test_collect_extracts_marker_names(tmp: TempDir):
    f = tmp / "test_marked.py"
    f.write_text("import oxitest\n@oxitest.mark.slow\ndef test_query(): pass\n")
    items, _ = collect_module(str(f))
    assert len(items) == 1, f"expected 1 item, got {len(items)}"
    assert items[0].markers == ["slow"], (
        f"expected markers=['slow'], got {items[0].markers}"
    )


def test_collect_extracts_multiple_markers(tmp: TempDir):
    f = tmp / "test_multi_mark.py"
    f.write_text(
        "import oxitest\n"
        "@oxitest.mark.slow\n"
        "@oxitest.mark.integration\n"
        "def test_query(): pass\n"
    )
    items, _ = collect_module(str(f))
    assert "slow" in items[0].markers, (
        f"'slow' marker should be collected, got markers: {items[0].markers}"
    )
    assert "integration" in items[0].markers, (
        f"'integration' marker should be collected, got markers: {items[0].markers}"
    )


def test_propagate_class_marks_copies_usefixtures():
    """_propagate_class_marks appends usefixtures marks from class to function."""
    import oxitest

    @oxitest.mark.usefixtures("db")
    class FakeClass:
        pass

    def test_fn():
        pass

    _propagate_class_marks(test_fn, FakeClass)
    marks = getattr(test_fn, "_oxitest_marks", [])
    assert any(m.name == "usefixtures" for m in marks), (
        "usefixtures mark from class should be propagated to function"
    )


def test_propagate_class_marks_ignores_non_usefixtures():
    """_propagate_class_marks does NOT copy skip/xfail from class to function."""
    import oxitest

    @oxitest.mark.skip(reason="class skip")
    class FakeClass:
        pass

    def test_fn():
        pass

    _propagate_class_marks(test_fn, FakeClass)
    assert not hasattr(test_fn, "_oxitest_marks") or all(
        m.name != "skip" for m in getattr(test_fn, "_oxitest_marks", [])
    ), "skip mark from class with skip_class=False should not propagate to test_fn"


def test_collect_class_methods_use_qualified_name(tmp: TempDir):
    """Class methods are returned as 'ClassName::method_name'."""
    f = tmp / "test_cls.py"
    f.write_text(
        "class TestSuite:\n    def test_foo(self): pass\n    def test_bar(self): pass\n"
    )
    items, _ = collect_module(str(f))
    names = [item.fn_name for item in items]
    assert "TestSuite::test_foo" in names, (
        f"class method should be collected as 'TestSuite::test_foo', got names: {names}"
    )
    assert "TestSuite::test_bar" in names, (
        f"class method should be collected as 'TestSuite::test_bar', got names: {names}"
    )


def test_collect_class_methods_with_usefixtures_propagation(tmp: TempDir):
    """usefixtures on a class is propagated to each test method at collection time."""
    f = tmp / "test_cls.py"
    f.write_text(
        "import oxitest\n"
        "@oxitest.mark.usefixtures('db')\n"
        "class TestSuite:\n"
        "    def test_foo(self): pass\n"
        "    def test_bar(self): pass\n"
    )
    items, _ = collect_module(str(f))
    assert len(items) == 2, (
        f"expected 2 class methods, got {len(items)}: {[i.fn_name for i in items]}"
    )
    for item in items:
        assert "usefixtures" in item.markers, (
            f"class usefixtures should propagate to {item.fn_name!r}, got markers: "
            f"{item.markers}"
        )


def test_collect_class_skip_not_propagated(tmp: TempDir):
    """skip on a class is NOT propagated to test methods."""
    f = tmp / "test_cls_skip.py"
    f.write_text(
        "import oxitest\n"
        "@oxitest.mark.skip(reason='class skip')\n"
        "class TestSuite:\n"
        "    def test_foo(self): pass\n"
    )
    items, _ = collect_module(str(f))
    # method collected but skip mark not propagated
    assert len(items) == 1, f"expected 1 method to be collected, got {len(items)}"
    assert "skip" not in items[0].markers, (
        f"skip mark should NOT propagate from class to method, got markers: "
        f"{items[0].markers}"
    )


def test_collected_item_can_be_constructed():
    item = CollectedItem(
        fn_name="test_foo",
        lineno=1,
        markers=[],
        param_id=None,
        param_values=[],
        is_async=False,
    )
    assert item.fn_name == "test_foo", (
        f"expected fn_name='test_foo', got {item.fn_name!r}"
    )
    assert item.lineno == 1, f"expected lineno=1, got {item.lineno}"
    assert item.markers == [], f"expected empty markers, got {item.markers}"
    assert item.param_id is None, f"expected param_id=None, got {item.param_id!r}"
    assert item.param_values == [], (
        f"expected empty param_values, got {item.param_values}"
    )
    assert item.is_async is False, f"expected is_async=False, got {item.is_async!r}"


def test_collected_item_with_markers_and_param():
    item = CollectedItem(
        fn_name="test_bar",
        lineno=5,
        markers=["slow"],
        param_id="case_a",
        param_values=[("x", "1"), ("y", "2")],
        is_async=False,
    )
    assert item.markers == ["slow"], f"expected markers=['slow'], got {item.markers}"
    assert item.param_id == "case_a", (
        f"expected param_id='case_a', got {item.param_id!r}"
    )
    assert item.param_values == [("x", "1"), ("y", "2")], (
        f"expected param_values=[('x', '1'), ('y', '2')], got {item.param_values}"
    )


def test_collect_module_registers_fixtures_from_test_module(tmp: TempDir) -> None:
    f = tmp / "test_self_contained.py"
    f.write_text(
        "import oxitest\n"
        "fixtures = oxitest.Fixtures()\n"
        "@fixtures.fixture\n"
        "def my_val():\n"
        "    return 42\n"
        "def test_foo(my_val): pass\n"
    )
    from oxitest._bridge.fixtures import FixtureRegistry

    registry = FixtureRegistry()

    class _FakeSession:
        _registry = registry
        _module_cache = None

    collect_module(str(f), session=_FakeSession())  # type: ignore[arg-type]
    defn = registry.get("my_val")
    assert defn is not None, (
        "collect_module should register fixtures from test module's Fixtures() instance"
    )
    assert defn.conftest_path == str(f), (
        f"registered fixture conftest_path should be {str(f)!r}, got "
        f"{defn.conftest_path!r}"
    )


def _write_py(tmp_path: TempDir, src: str) -> str:
    """Write source code to a temp file and return its path as str."""
    p = tmp_path / "test_viol.py"
    p.write_text(textwrap.dedent(src))
    return str(p)


def test_collect_violations_bare_assert(tmp: TempDir):
    path = _write_py(
        tmp,
        """
        def test_foo():
            x = 1
            assert x == 1
    """,
    )
    _, violations = collect_module(path, collect_violations=True)
    assert len(violations) == 1, (
        f"expected 1 bare-assert violation, got {len(violations)}: {violations}"
    )
    assert violations[0].kind == "bare_assert", (
        f"violation kind should be 'bare_assert', got {violations[0].kind!r}"
    )
    assert "test_foo" in violations[0].node_id, (
        f"violation node_id should contain 'test_foo', got {violations[0].node_id!r}"
    )


def test_collect_violations_assert_with_message_no_violation(tmp: TempDir):
    path = _write_py(
        tmp,
        """
        def test_foo():
            assert 1 == 1, "one equals one"
    """,
    )
    _, violations = collect_module(path, collect_violations=False)
    assert violations == [], (
        f"assert with message should not produce violations, got {violations}"
    )


def test_collect_violations_nested_helper_no_false_positive(tmp: TempDir):
    path = _write_py(
        tmp,
        """
        def test_foo():
            def helper():
                assert True
            helper()
            assert True, "outer assert has message"
    """,
    )
    _, violations = collect_module(path, collect_violations=True)
    # The bare assert is inside a nested helper — must NOT be attributed to test_foo
    bare = [v for v in violations if v.kind == "bare_assert"]
    assert bare == [], f"expected no bare-assert violations, got {bare}"


def test_collect_violations_false_when_disabled(tmp: TempDir):
    path = _write_py(
        tmp,
        """
        def test_foo():
            assert 1 == 1
    """,
    )
    _, violations = collect_module(path, collect_violations=False)
    assert violations == [], (
        f"violations should be empty when collect_violations=False, got {violations}"
    )


def test_check_fn_violations_class_method_dict_parametrize():
    """Class method with dict-parametrize produces DICT_PARAMETRIZE violation."""

    def test_method(self):
        pass

    setattr(test_method, "_oxitest_param_cases", _DictCases(cases={"basic": {"x": 1}}))

    path = "tests/test_cls.py"
    fn_name = "TestSuite::test_method"
    violations = _check_fn_violations(path, fn_name, test_method)

    assert len(violations) == 1, (
        f"expected 1 DICT_PARAMETRIZE violation, got {len(violations)}: {violations}"
    )
    v = violations[0]
    assert v.kind == ViolationKind.DICT_PARAMETRIZE, (
        f"violation kind should be DICT_PARAMETRIZE, got {v.kind!r}"
    )
    assert v.node_id == f"{path}::{fn_name}", (
        f"violation node_id should be '{path}::{fn_name}', got {v.node_id!r}"
    )


def test_check_fn_violations_class_method_missing_mark_reason():
    """Class method with skip missing reason= produces MISSING_MARK_REASON."""
    import oxitest

    @oxitest.mark.skip
    def test_method(self):
        pass

    path = "tests/test_cls.py"
    fn_name = "TestSuite::test_method"
    violations = _check_fn_violations(path, fn_name, test_method)

    assert len(violations) == 1, (
        f"expected 1 MISSING_MARK_REASON violation, got {len(violations)}: {violations}"
    )
    v = violations[0]
    assert v.kind == ViolationKind.MISSING_MARK_REASON, (
        f"violation kind should be MISSING_MARK_REASON, got {v.kind!r}"
    )
    assert v.node_id == f"{path}::{fn_name}", (
        f"violation node_id should be '{path}::{fn_name}', got {v.node_id!r}"
    )
    assert v.detail == "skip", f"violation detail should be 'skip', got {v.detail!r}"


def test_collect_async_function_sets_is_async(tmp: TempDir):
    f = tmp / "test_async.py"
    f.write_text("async def test_hello(): pass\n")
    items, _ = collect_module(str(f))
    assert len(items) == 1, f"expected 1 item, got {len(items)}"
    assert items[0].is_async is True, (
        f"async def test should have is_async=True, got {items[0].is_async!r}"
    )


def test_collect_sync_function_sets_is_async_false(tmp: TempDir):
    f = tmp / "test_sync.py"
    f.write_text("def test_hello(): pass\n")
    items, _ = collect_module(str(f))
    assert len(items) == 1, f"expected 1 item, got {len(items)}"
    assert items[0].is_async is False, (
        f"sync def test should have is_async=False, got {items[0].is_async!r}"
    )


def test_collect_mixed_sync_async(tmp: TempDir):
    f = tmp / "test_mixed.py"
    f.write_text("def test_sync(): pass\nasync def test_async(): pass\n")
    items, _ = collect_module(str(f))
    assert len(items) == 2, f"expected 2 items, got {len(items)}"
    by_name = {item.fn_name: item for item in items}
    assert by_name["test_sync"].is_async is False, "sync test should be is_async=False"
    assert by_name["test_async"].is_async is True, "async test should be is_async=True"


def test_async_fixture_flagged_during_registration(tmp: TempDir):
    """Async fixtures should have is_async=True on their FixtureDef."""
    f = tmp / "test_async_fx.py"
    f.write_text(
        "import oxitest\n"
        "fixtures = oxitest.Fixtures()\n"
        "@fixtures.fixture\n"
        "async def async_val():\n"
        "    return 42\n"
        "@fixtures.fixture\n"
        "def sync_val():\n"
        "    return 1\n"
        "async def test_foo(async_val, sync_val): pass\n"
    )
    from oxitest._bridge.fixtures import FixtureRegistry

    registry = FixtureRegistry()

    class _FakeSession:
        _registry = registry
        _module_cache = None

    collect_module(str(f), session=_FakeSession())  # type: ignore[arg-type]
    async_defn = registry.get("async_val")
    sync_defn = registry.get("sync_val")
    assert async_defn is not None, "async_val fixture should be registered"
    assert sync_defn is not None, "sync_val fixture should be registered"
    assert async_defn.is_async is True, (
        f"async fixture should have is_async=True, got {async_defn.is_async!r}"
    )
    assert sync_defn.is_async is False, (
        f"sync fixture should have is_async=False, got {sync_defn.is_async!r}"
    )


def test_collect_async_class_method_sets_is_async(tmp: TempDir):
    f = tmp / "test_cls_async.py"
    f.write_text(
        "class TestSuite:\n"
        "    async def test_async_method(self): pass\n"
        "    def test_sync_method(self): pass\n"
    )
    items, _ = collect_module(str(f))
    assert len(items) == 2, f"expected 2 items, got {len(items)}"
    by_name = {item.fn_name: item for item in items}
    assert by_name["TestSuite::test_async_method"].is_async is True, (
        "async class method should be is_async=True"
    )
    assert by_name["TestSuite::test_sync_method"].is_async is False, (
        "sync class method should be is_async=False"
    )
