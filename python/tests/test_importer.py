"""Tests for collect_module, item collection, module marks, and class propagation."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType, ModuleType
from typing import Any

import oxitest
from oxitest import CollectedItem, TempDir, WarnCapture, helpers, raises
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixture_type import Fixture
from oxitest._bridge._fn_metadata import _update, get_metadata
from oxitest._bridge._mark_api import MarkInfo
from oxitest._bridge._violation_checkers import check_fn_violations
from oxitest._bridge.importer import (
    PluginCollectorWarning,
    _apply_module_marks,
    _collect_items,
    _extract_module_marks,
    _get_fixture_deps,
    _module_members,
    _propagate_class_marks,
    collect_module,
)
from oxitest._bridge.parametrize import DictCases
from oxitest._bridge.result import ViolationKind


def test_collect_empty_module(tmp: TempDir) -> None:
    """An empty module yields no collected items."""
    path = helpers.common.write_test_module(tmp, "", name="test_empty.py")
    items, _ = collect_module(path)
    assert items == [], f"collecting an empty module should yield no items, got {items}"


def test_collect_single_test_function(tmp: TempDir) -> None:
    """A module with one test_ function produces a single CollectedItem."""
    path = helpers.common.write_test_module(
        tmp, "def test_bar(): pass\n", name="test_foo.py"
    )
    items, _ = collect_module(path)
    assert len(items) == 1, f"expected 1 collected item, got {len(items)}: {items}"
    assert items[0].fn_name == "test_bar", (
        f"expected fn_name='test_bar', got {items[0].fn_name!r}"
    )
    assert items[0].lineno == 1, (
        f"expected lineno=1 for first function, got {items[0].lineno}"
    )
    assert items[0].markers == (), (
        f"expected no markers on unmarked function, got {items[0].markers}"
    )


def test_collect_multiple_functions(tmp: TempDir) -> None:
    """All test_ functions in a module are collected as separate items."""
    path = helpers.common.write_test_module(
        tmp, "def test_one(): pass\ndef test_two(): pass\n", name="test_multi.py"
    )
    items, _ = collect_module(path)
    assert len(items) == 2, (
        f"expected 2 items, got {len(items)}: {[i.fn_name for i in items]}"
    )
    names = [item.fn_name for item in items]
    assert "test_one" in names, f"'test_one' should be collected, got names: {names}"
    assert "test_two" in names, f"'test_two' should be collected, got names: {names}"


def test_collect_ignores_non_test_functions(tmp: TempDir) -> None:
    """Functions not prefixed with test_ are silently excluded from collection."""
    path = helpers.common.write_test_module(
        tmp, "def helper(): pass\ndef test_real(): pass\n", name="test_foo.py"
    )
    items, _ = collect_module(path)
    assert len(items) == 1, (
        f"helper functions should not be collected, expected 1 item got {len(items)}: "
        f"{[i.fn_name for i in items]}"
    )
    assert items[0].fn_name == "test_real", (
        f"only 'test_real' should be collected, got {items[0].fn_name!r}"
    )


def test_collect_raises_on_missing_file() -> None:
    """collect_module raises an exception when the given path does not exist."""
    with raises(Exception):
        collect_module("/nonexistent/path/test_foo.py")


def test_collect_error_message_is_clean_traceback_not_testrepr(tmp: TempDir) -> None:
    """collect_module error: plain traceback, no TestResult repr, real newlines."""
    path = helpers.common.write_test_module(
        tmp, "import _oxitest_nonexistent_module_xyz\n", name="test_bad_import.py"
    )
    with oxitest.raises(ImportError) as exc_info:
        collect_module(path)
    msg = str(exc_info.value)
    assert "TestResult(" not in msg, (
        f"message must not be TestResult repr, got: {msg!r}"
    )
    assert "\n" in msg, f"traceback must contain real newlines, got: {msg!r}"
    assert "Traceback" in msg, f"traceback header missing, got: {msg!r}"
    assert "ModuleNotFoundError" in msg or "ImportError" in msg, (
        f"actual error type missing from message, got: {msg!r}"
    )


def test_collect_extracts_marker_names(tmp: TempDir) -> None:
    """Marker names from @mark decorators appear in the CollectedItem.markers tuple."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n@oxitest.mark.slow\ndef test_query(): pass\n",
        name="test_marked.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 1, f"expected 1 item, got {len(items)}"
    assert items[0].markers == ("slow",), (
        f"expected markers=('slow',), got {items[0].markers}"
    )


def test_collect_extracts_multiple_markers(tmp: TempDir) -> None:
    """All stacked @mark decorators appear in the CollectedItem.markers tuple."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "@oxitest.mark.slow\n"
        "@oxitest.mark.integration\n"
        "def test_query(): pass\n",
        name="test_multi_mark.py",
    )
    items, _ = collect_module(path)
    assert "slow" in items[0].markers, (
        f"'slow' marker should be collected, got markers: {items[0].markers}"
    )
    assert "integration" in items[0].markers, (
        f"'integration' marker should be collected, got markers: {items[0].markers}"
    )


def test_propagate_class_marks_copies_usefixtures() -> None:
    """_propagate_class_marks appends usefixtures marks from class to function."""

    @oxitest.mark.usefixtures("db")
    class FakeClass:
        pass

    def test_fn() -> None:
        pass

    _propagate_class_marks(test_fn, FakeClass)
    marks = get_metadata(test_fn).marks
    assert any(m.name == "usefixtures" for m in marks), (
        "usefixtures mark from class should be propagated to function"
    )


def test_propagate_class_marks_copies_all_marks() -> None:
    """_propagate_class_marks copies ALL marks from class to function."""

    @oxitest.mark.skip(reason="class skip")
    class FakeClass:
        pass

    def test_fn() -> None:
        pass

    _propagate_class_marks(test_fn, FakeClass)
    assert any(m.name == "skip" for m in get_metadata(test_fn).marks), (
        "skip mark from class should propagate to test_fn"
    )


def test_collect_class_methods_use_qualified_name(tmp: TempDir) -> None:
    """Class methods are returned as 'ClassName::method_name'."""
    path = helpers.common.write_test_module(
        tmp,
        "class TestSuite:\n"
        "    def test_foo(self): pass\n"
        "    def test_bar(self): pass\n",
        name="test_cls.py",
    )
    items, _ = collect_module(path)
    names = [item.fn_name for item in items]
    assert "TestSuite::test_foo" in names, (
        f"class method should be collected as 'TestSuite::test_foo', got names: {names}"
    )
    assert "TestSuite::test_bar" in names, (
        f"class method should be collected as 'TestSuite::test_bar', got names: {names}"
    )


def test_collect_class_methods_with_usefixtures_propagation(tmp: TempDir) -> None:
    """The usefixtures mark on a class propagates to each test method at collection."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "@oxitest.mark.usefixtures('db')\n"
        "class TestSuite:\n"
        "    def test_foo(self): pass\n"
        "    def test_bar(self): pass\n",
        name="test_cls.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 2, (
        f"expected 2 class methods, got {len(items)}: {[i.fn_name for i in items]}"
    )
    for item in items:
        assert "usefixtures" in item.markers, (
            f"class usefixtures should propagate to {item.fn_name!r}, got markers: "
            f"{item.markers}"
        )


def test_collect_class_skip_propagated(tmp: TempDir) -> None:
    """A skip mark on a class IS propagated to all test methods at collection time."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "@oxitest.mark.skip(reason='class skip')\n"
        "class TestSuite:\n"
        "    def test_foo(self): pass\n",
        name="test_cls_skip.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 1, f"expected 1 method to be collected, got {len(items)}"
    assert "skip" in items[0].markers, (
        f"skip mark should propagate from class to method, got markers: "
        f"{items[0].markers}"
    )


def test_collected_item_can_be_constructed() -> None:
    """CollectedItem can be constructed with all fields and stores each correctly."""
    item = CollectedItem(
        fn_name="test_foo",
        lineno=1,
        markers=(),
        param_id=None,
        param_values=(),
        is_async=False,
    )
    assert item.fn_name == "test_foo", (
        f"expected fn_name='test_foo', got {item.fn_name!r}"
    )
    assert item.lineno == 1, f"expected lineno=1, got {item.lineno}"
    assert item.markers == (), f"expected empty markers, got {item.markers}"
    assert item.param_id is None, f"expected param_id=None, got {item.param_id!r}"
    assert item.param_values == (), (
        f"expected empty param_values, got {item.param_values}"
    )
    assert item.is_async is False, f"expected is_async=False, got {item.is_async!r}"


def test_collected_item_with_markers_and_param() -> None:
    """CollectedItem stores non-default markers and param_id correctly."""
    item = CollectedItem(
        fn_name="test_bar",
        lineno=5,
        markers=("slow",),
        param_id="case_a",
        param_values=(("x", "1"), ("y", "2")),
        is_async=False,
    )
    assert item.markers == ("slow",), f"expected markers=('slow',), got {item.markers}"
    assert item.param_id == "case_a", (
        f"expected param_id='case_a', got {item.param_id!r}"
    )
    assert item.param_values == (("x", "1"), ("y", "2")), (
        f"expected param_values=(('x', '1'), ('y', '2')), got {item.param_values}"
    )


def test_collect_module_emits_violation_for_helpers_in_test_module(
    tmp: TempDir,
) -> None:
    """Helpers() instance without allow comment emits REGISTRAR_IN_TEST_MODULE."""
    path = helpers.common.write_test_module(
        tmp,
        "from oxitest import Helpers\n"
        "h = Helpers()\n"
        "@h.helper\n"
        "def my_helper():\n"
        "    return 42\n"
        "def test_foo(): pass\n",
        name="test_with_helpers.py",
    )
    _, violations = collect_module(path)
    registrar_viols = [
        v for v in violations if v.kind == ViolationKind.REGISTRAR_IN_TEST_MODULE
    ]
    assert len(registrar_viols) == 1, (
        f"expected 1 REGISTRAR_IN_TEST_MODULE violation for Helpers() in test module, "
        f"got {len(registrar_viols)}: {registrar_viols}"
    )
    v = registrar_viols[0]
    assert v.node_id == path, (
        f"violation node_id should be the module path {path!r}, got {v.node_id!r}"
    )
    assert "Helpers" in v.detail, (
        f"violation detail should mention Helpers, got {v.detail!r}"
    )
    assert "allow[registrar-in-test-module]" in v.detail, (
        f"violation detail should suggest allow comment, got {v.detail!r}"
    )


def _write_py(tmp_path: TempDir, src: str) -> str:
    """Write source code to a temp file and return its path as str."""
    return helpers.common.write_test_module(tmp_path, src, name="test_viol.py")


def test_collect_violations_bare_assert_now_rust_side(tmp: TempDir) -> None:
    """Bare-assert detection moved to Rust (bare_asserts.rs) — Python returns none."""
    path = _write_py(
        tmp,
        """
        def test_foo():
            x = 1
            assert x == 1
    """,
    )
    _, violations = collect_module(path, collect_violations=True)
    bare = [v for v in violations if v.kind == "bare_assert"]
    assert bare == [], (
        f"bare-assert violations should come from Rust, not Python: {bare}"
    )


def test_collect_violations_assert_with_message_no_violation(tmp: TempDir) -> None:
    """An assert statement with a message is not flagged as a violation."""
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


def test_collect_violations_nested_helper_no_false_positive(tmp: TempDir) -> None:
    """A bare assert in a nested helper does not violate the outer test function."""
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


def test_collect_violations_false_when_disabled(tmp: TempDir) -> None:
    """No violations when collect_violations=False, even for bare asserts."""
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


def test_check_fn_violations_class_method_dict_parametrize() -> None:
    """Class method with dict-parametrize produces DICT_PARAMETRIZE violation."""

    def test_method(self: object) -> None:
        pass

    _update(
        test_method,
        param_cases=(
            DictCases(cases=MappingProxyType({"basic": {"x": 1}, "extra": {"x": 2}})),
        ),
    )

    path = "tests/test_cls.py"
    fn_name = "TestSuite::test_method"
    violations = list(check_fn_violations(path, fn_name, test_method))

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


def test_check_fn_violations_class_method_missing_mark_reason() -> None:
    """Class method with skip missing reason= produces MISSING_MARK_REASON."""

    @oxitest.mark.skip
    def test_method(self: object) -> None:
        pass

    path = "tests/test_cls.py"
    fn_name = "TestSuite::test_method"
    violations = list(check_fn_violations(path, fn_name, test_method))

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


def test_collect_async_function_sets_is_async(tmp: TempDir) -> None:
    """An async def test_ function is collected with is_async=True."""
    path = helpers.common.write_test_module(
        tmp, "async def test_hello(): pass\n", name="test_async.py"
    )
    items, _ = collect_module(path)
    assert len(items) == 1, f"expected 1 item, got {len(items)}"
    assert items[0].is_async is True, (
        f"async def test should have is_async=True, got {items[0].is_async!r}"
    )


def test_collect_sync_function_sets_is_async_false(tmp: TempDir) -> None:
    """A synchronous def test_ function is collected with is_async=False."""
    path = helpers.common.write_test_module(
        tmp, "def test_hello(): pass\n", name="test_sync.py"
    )
    items, _ = collect_module(path)
    assert len(items) == 1, f"expected 1 item, got {len(items)}"
    assert items[0].is_async is False, (
        f"sync def test should have is_async=False, got {items[0].is_async!r}"
    )


def test_collect_mixed_sync_async(tmp: TempDir) -> None:
    """A module with both sync and async tests sets is_async correctly on each item."""
    path = helpers.common.write_test_module(
        tmp,
        "def test_sync(): pass\nasync def test_async(): pass\n",
        name="test_mixed.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 2, f"expected 2 items, got {len(items)}"
    by_name = {item.fn_name: item for item in items}
    assert by_name["test_sync"].is_async is False, "sync test should be is_async=False"
    assert by_name["test_async"].is_async is True, "async test should be is_async=True"


def test_fixtures_in_test_module_are_registered_with_allow(tmp: TempDir) -> None:
    """Fixtures() with allow comment registers silently — no violation."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "fixtures = oxitest.Fixtures()  "
        "# oxitest: allow[registrar-in-test-module]\n"
        "@fixtures.fixture\n"
        "async def async_val():\n"
        "    return 42\n"
        "@fixtures.fixture\n"
        "def sync_val():\n"
        "    return 1\n"
        "async def test_foo(async_val, sync_val): pass\n",
        name="test_async_fx.py",
    )
    registry = FixtureRegistry()

    class _FakeSession:
        _registry = registry
        _module_cache = None

    _, violations = collect_module(path, session=_FakeSession())
    registrar_viols = [
        v for v in violations if v.kind == ViolationKind.REGISTRAR_IN_TEST_MODULE
    ]
    assert len(registrar_viols) == 0, (
        "allow comment should suppress violation — "
        f"got {len(registrar_viols)}: {registrar_viols}"
    )
    assert registry.get("async_val") is not None, (
        "async_val should be registered — allow comment authorizes registration"
    )
    assert registry.get("sync_val") is not None, (
        "sync_val should be registered — allow comment authorizes registration"
    )


def test_collect_async_class_method_sets_is_async(tmp: TempDir) -> None:
    """Async and sync class methods each receive the correct is_async value."""
    path = helpers.common.write_test_module(
        tmp,
        "class TestSuite:\n"
        "    async def test_async_method(self): pass\n"
        "    def test_sync_method(self): pass\n",
        name="test_cls_async.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 2, f"expected 2 items, got {len(items)}"
    by_name = {item.fn_name: item for item in items}
    assert by_name["TestSuite::test_async_method"].is_async is True, (
        "async class method should be is_async=True"
    )
    assert by_name["TestSuite::test_sync_method"].is_async is False, (
        "sync class method should be is_async=False"
    )


def test_module_members_yields_test_functions_only() -> None:
    """_module_members yields only test_ prefixed callables, skipping helpers."""
    mod = ModuleType("fake")

    def test_one() -> None:
        pass

    def test_two() -> None:
        pass

    def helper() -> None:
        pass

    mod.__dict__["test_one"] = test_one
    mod.__dict__["test_two"] = test_two
    mod.__dict__["helper"] = helper
    members = list(_module_members(mod))
    names = [n for n, _ in members]
    assert "test_one" in names, f"'test_one' should be yielded, got {names}"
    assert "test_two" in names, f"'test_two' should be yielded, got {names}"
    assert "helper" not in names, f"'helper' should not be yielded, got {names}"


def test_collect_items_returns_collected_items() -> None:
    """_collect_items wraps member functions into CollectedItem values with metadata."""

    def fake_fn() -> None:
        pass

    lineno = fake_fn.__code__.co_firstlineno
    members = [("test_fake", fake_fn)]
    items, _ = _collect_items(members, "/fake.py", collect_violations=False)
    assert len(items) == 1, f"expected 1 item, got {len(items)}"
    assert items[0].fn_name == "test_fake", (
        f"expected fn_name='test_fake', got {items[0].fn_name!r}"
    )
    assert items[0].lineno == lineno, f"expected lineno={lineno}, got {items[0].lineno}"


# ── _extract_module_marks tests ───────────────────────────────────────────────


def test_extract_module_marks_none_returns_empty() -> None:
    """No oxi_mark attribute → empty list, no violations."""
    module = ModuleType("test_no_marks")
    marks, violations = _extract_module_marks(module, "/fake/test_no_marks.py")
    assert marks == [], f"expected no marks, got {marks}"
    assert violations == [], f"expected no violations, got {violations}"


def test_extract_module_marks_single_mark() -> None:
    """oxi_mark = oxi.mark.slow → list with one MarkInfo."""
    module = ModuleType("test_single")
    setattr(module, "oxi_mark", MarkInfo("slow", (), MappingProxyType({})))  # noqa: B010
    marks, violations = _extract_module_marks(module, "/fake/test_single.py")
    assert len(marks) == 1, f"expected 1 mark, got {len(marks)}"
    assert marks[0].name == "slow", f"expected mark name 'slow', got {marks[0].name!r}"
    assert violations == [], f"expected no violations, got {violations}"


def test_extract_module_marks_list() -> None:
    """oxi_mark = [mark.slow, mark.timeout(10)] → list with two MarkInfos."""
    module = ModuleType("test_list")
    setattr(  # noqa: B010 — dynamic module attr
        module,
        "oxi_mark",
        [
            MarkInfo("slow", (), MappingProxyType({})),
            MarkInfo("timeout", (), MappingProxyType({"seconds": 10})),
        ],
    )
    marks, violations = _extract_module_marks(module, "/fake/test_list.py")
    assert len(marks) == 2, f"expected 2 marks, got {len(marks)}"
    names = [m.name for m in marks]
    assert "slow" in names, f"'slow' should be in marks, got {names}"
    assert "timeout" in names, f"'timeout' should be in marks, got {names}"
    assert violations == [], f"expected no violations, got {violations}"


def test_extract_module_marks_tuple() -> None:
    """oxi_mark as tuple is accepted."""
    module = ModuleType("test_tuple")
    setattr(module, "oxi_mark", (MarkInfo("slow", (), MappingProxyType({})),))  # noqa: B010
    marks, _ = _extract_module_marks(module, "/fake/test_tuple.py")
    assert len(marks) == 1, f"expected 1 mark, got {len(marks)}"


def test_extract_module_marks_invalid_entry() -> None:
    """Non-MarkInfo entries produce violations, valid entries still collected."""
    module = ModuleType("test_invalid")
    setattr(  # noqa: B010 — dynamic module attr
        module,
        "oxi_mark",
        [
            MarkInfo("slow", (), MappingProxyType({})),
            42,
            "not_a_mark",
        ],
    )
    marks, violations = _extract_module_marks(module, "/fake/test_invalid.py")
    assert len(marks) == 1, f"expected 1 valid mark, got {len(marks)}"
    assert marks[0].name == "slow", f"expected 'slow', got {marks[0].name!r}"
    assert len(violations) == 2, f"expected 2 violations, got {len(violations)}"
    for v in violations:
        assert v.kind == ViolationKind.INVALID_MODULE_MARK, (
            f"expected INVALID_MODULE_MARK, got {v.kind}"
        )


# ── _apply_module_marks tests ─────────────────────────────────────────────────


def test_apply_module_marks_prepends_to_unmarked_fn() -> None:
    """Module marks are added to functions with no per-test marks."""

    def test_fn() -> None:
        pass

    module_marks = [MarkInfo("slow", (), MappingProxyType({}))]
    _apply_module_marks([("test_fn", test_fn)], module_marks)
    marks = get_metadata(test_fn).marks
    assert len(marks) == 1, f"expected 1 mark, got {len(marks)}"
    assert marks[0].name == "slow", f"expected 'slow', got {marks[0].name!r}"


def test_apply_module_marks_per_test_overrides_same_name() -> None:
    """Per-test mark overrides module mark of the same name."""

    @oxitest.mark.timeout(5)
    def test_fn() -> None:
        pass

    module_marks = [MarkInfo("timeout", (), MappingProxyType({"seconds": 120}))]
    _apply_module_marks([("test_fn", test_fn)], module_marks)
    marks = get_metadata(test_fn).marks
    timeout_marks = [m for m in marks if m.name == "timeout"]
    assert len(timeout_marks) == 1, (
        f"expected exactly 1 timeout mark, got {len(timeout_marks)}: {timeout_marks}"
    )
    assert timeout_marks[0].kwargs == {"seconds": 5}, (
        f"per-test timeout(5) should override module timeout(120), "
        f"got {timeout_marks[0].kwargs}"
    )


def test_apply_module_marks_non_conflicting_added() -> None:
    """Module marks with different names than per-test marks are added."""

    @oxitest.mark.timeout(5)
    def test_fn() -> None:
        pass

    module_marks = [MarkInfo("slow", (), MappingProxyType({}))]
    _apply_module_marks([("test_fn", test_fn)], module_marks)
    marks = get_metadata(test_fn).marks
    names = [m.name for m in marks]
    assert "slow" in names, f"module mark 'slow' should be added, got {names}"
    assert "timeout" in names, f"per-test mark 'timeout' should remain, got {names}"


def test_apply_module_marks_empty_list_is_noop() -> None:
    """Empty module_marks list does not modify functions."""

    def test_fn() -> None:
        pass

    _apply_module_marks([("test_fn", test_fn)], [])
    marks = get_metadata(test_fn).marks
    assert marks == (), f"expected no marks, got {marks}"


# ── collect_module oxi_mark integration tests ─────────────────────────────────


def test_collect_module_with_oxi_mark_single(tmp: TempDir) -> None:
    """oxi_mark = oxi.mark.slow applies 'slow' to all tests."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "oxi_mark = oxitest.mark.slow\n"
        "def test_a(): pass\n"
        "def test_b(): pass\n",
        name="test_mod_mark.py",
    )
    items, violations = collect_module(path)
    assert len(items) == 2, f"expected 2 items, got {len(items)}"
    for item in items:
        assert "slow" in item.markers, (
            f"module mark 'slow' should apply to {item.fn_name}, got {item.markers}"
        )
    assert violations == [], f"expected no violations, got {violations}"


def test_collect_module_with_oxi_mark_list(tmp: TempDir) -> None:
    """oxi_mark = [mark.slow, mark.timeout(10)] applies both marks to all tests."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "oxi_mark = [oxitest.mark.slow, oxitest.mark.timeout(10)]\n"
        "def test_a(): pass\n"
        "def test_b(): pass\n",
        name="test_mod_marks.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 2, f"expected 2 items, got {len(items)}"
    for item in items:
        assert "slow" in item.markers, (
            f"module mark 'slow' should apply to {item.fn_name}, got {item.markers}"
        )
        assert "timeout" in item.markers, (
            f"module mark 'timeout' should apply to {item.fn_name}, got {item.markers}"
        )


def test_collect_module_oxi_mark_per_test_override(tmp: TempDir) -> None:
    """Per-test mark overrides module mark of the same name."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "oxi_mark = [oxitest.mark.timeout(120)]\n"
        "@oxitest.mark.timeout(5)\n"
        "def test_fast(): pass\n"
        "def test_slow(): pass\n",
        name="test_override.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 2, f"expected 2 items, got {len(items)}"
    for item in items:
        assert "timeout" in item.markers, (
            f"timeout should be on {item.fn_name}, got {item.markers}"
        )


def test_collect_module_oxi_mark_with_parametrize(tmp: TempDir) -> None:
    """Module marks apply to each parametrize case."""
    path = helpers.common.write_test_module(
        tmp,
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "oxi_mark = [oxitest.mark.slow]\n"
        "@dataclass(frozen=True)\n"
        "class C:\n"
        "    x: int\n"
        "@oxitest.parametrize(case_a=C(1), case_b=C(2))\n"
        "def test_compute(c: C): pass\n",
        name="test_param.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 2, f"expected 2 parametrize cases, got {len(items)}"
    for item in items:
        assert "slow" in item.markers, (
            f"module mark 'slow' should apply to {item.fn_name}[{item.param_id}], "
            f"got {item.markers}"
        )


def test_collect_module_oxi_mark_applies_to_class_methods(tmp: TempDir) -> None:
    """Module marks apply to test methods inside Test* classes."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "oxi_mark = [oxitest.mark.slow]\n"
        "class TestSuite:\n"
        "    def test_foo(self): pass\n"
        "    def test_bar(self): pass\n",
        name="test_cls_mod.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 2, f"expected 2 items, got {len(items)}"
    for item in items:
        assert "slow" in item.markers, (
            f"module mark 'slow' should apply to class method {item.fn_name}, "
            f"got {item.markers}"
        )


def test_collect_module_oxi_mark_invalid_entry_violation(tmp: TempDir) -> None:
    """Invalid entries in oxi_mark produce violations."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\noxi_mark = [oxitest.mark.slow, 42]\ndef test_a(): pass\n",
        name="test_bad_mark.py",
    )
    items, violations = collect_module(path)
    assert len(items) == 1, f"expected 1 item (tests still collected), got {len(items)}"
    assert "slow" in items[0].markers, (
        f"valid mark 'slow' should still apply, got {items[0].markers}"
    )
    assert len(violations) == 1, (
        f"expected 1 violation for invalid entry, got {len(violations)}"
    )
    assert violations[0].kind == ViolationKind.INVALID_MODULE_MARK, (
        f"expected INVALID_MODULE_MARK, got {violations[0].kind}"
    )


# ── class-level mark propagation tests ─────────────────────────────────────


def test_propagate_class_marks_copies_skip(tmp: TempDir) -> None:
    """Class-level @mark.skip propagates to all methods."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "\n"
        "@oxitest.mark.skip(reason='class skip')\n"
        "class TestSkipped:\n"
        "    def test_a(self): pass\n"
        "    def test_b(self): pass\n",
        name="test_class_skip.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 2, f"expected 2 items, got {len(items)}"
    for item in items:
        assert "skip" in item.markers, (
            f"expected skip marker on {item.fn_name}, got {item.markers}"
        )


def test_propagate_class_marks_copies_timeout(tmp: TempDir) -> None:
    """Class-level @mark.timeout propagates to all methods."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "\n"
        "@oxitest.mark.timeout(10)\n"
        "class TestTimed:\n"
        "    def test_a(self): pass\n",
        name="test_class_timeout.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 1, f"expected 1 item, got {len(items)}"
    assert "timeout" in items[0].markers, (
        f"expected timeout marker, got {items[0].markers}"
    )


def test_propagate_class_marks_copies_custom_mark(tmp: TempDir) -> None:
    """Class-level custom mark propagates to methods."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "\n"
        "@oxitest.mark.slow\n"
        "class TestSlow:\n"
        "    def test_a(self): pass\n",
        name="test_class_custom.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 1, f"expected 1 item, got {len(items)}"
    assert "slow" in items[0].markers, f"expected slow marker, got {items[0].markers}"


# ── oxi_mark skip(when=False) no-op tests ──────────────────────────────────


def test_module_mark_skip_when_false_no_violation(tmp: TempDir) -> None:
    """oxi_mark = mark.skip(when=False) is a no-op, not a violation."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "\n"
        "oxi_mark = oxitest.mark.skip(when=False, reason='not skipped')\n"
        "\n"
        "def test_ok(): pass\n",
        name="test_skip_false.py",
    )
    items, violations = collect_module(path)
    assert len(items) == 1, f"expected 1 item, got {len(items)}"
    assert not any(v.kind == ViolationKind.INVALID_MODULE_MARK for v in violations), (
        f"skip(when=False) should not be a violation: {violations}"
    )
    # Test should NOT have a skip marker
    assert "skip" not in items[0].markers, (
        f"skip(when=False) should not apply skip marker, got {items[0].markers}"
    )


def test_module_mark_skip_when_true_applies(tmp: TempDir) -> None:
    """oxi_mark = mark.skip(when=True) applies skip to all tests."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "\n"
        "oxi_mark = oxitest.mark.skip(when=True, reason='always skip')\n"
        "\n"
        "def test_ok(): pass\n",
        name="test_skip_true.py",
    )
    items, violations = collect_module(path)
    assert len(items) == 1, f"expected 1 item, got {len(items)}"
    assert "skip" in items[0].markers, (
        f"skip(when=True) should apply skip marker, got {items[0].markers}"
    )
    assert not any(v.kind == ViolationKind.INVALID_MODULE_MARK for v in violations), (
        f"skip(when=True) should not be a violation: {violations}"
    )


def test_module_mark_skip_when_false_in_list_no_violation(tmp: TempDir) -> None:
    """oxi_mark list with skip(when=False) silently skips the no-op entry."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "\n"
        "oxi_mark = [oxitest.mark.skip(when=False), oxitest.mark.slow]\n"
        "\n"
        "def test_ok(): pass\n",
        name="test_skip_false_list.py",
    )
    items, violations = collect_module(path)
    assert len(items) == 1, f"expected 1 item, got {len(items)}"
    assert "slow" in items[0].markers, (
        f"slow mark should still apply, got {items[0].markers}"
    )
    assert "skip" not in items[0].markers, (
        f"skip(when=False) should not apply, got {items[0].markers}"
    )
    assert not any(v.kind == ViolationKind.INVALID_MODULE_MARK for v in violations), (
        f"skip(when=False) in list should not be a violation: {violations}"
    )


# ---------------------------------------------------------------------------
# Plugin collector warning tests
# ---------------------------------------------------------------------------


@dataclass
class _FakeRegistry:
    """Minimal stand-in for PluginRegistry with only collectors."""

    collectors: list[object] = field(default_factory=list)


@dataclass
class _FakeSession:
    """Minimal stand-in for a session object with a _plugin_registry."""

    _plugin_registry: _FakeRegistry = field(default_factory=_FakeRegistry)


class _RaisingCollector:
    """Collector that always raises."""

    def collect(self, **_: Any) -> list[object]:
        msg = "collector went boom"
        raise RuntimeError(msg)


class _BadReturnCollector:
    """Collector that returns non-CollectedItem values."""

    def collect(self, **_: Any) -> list[object]:
        return ["not-a-collected-item", 42]


class _GoodCollector:
    """Collector that returns a valid CollectedItem."""

    def collect(self, **_: Any) -> list[CollectedItem]:
        return [
            CollectedItem(
                fn_name="test_from_plugin",
                lineno=1,
                markers=(),
                param_id=None,
                param_values=(),
            )
        ]


def test_collector_error_emits_warning(tmp: TempDir, warn: WarnCapture) -> None:
    """A collector that raises emits PluginCollectorWarning."""
    path = helpers.common.write_test_module(
        tmp, "def test_ok(): pass\n", name="test_col_err.py"
    )
    session = _FakeSession(
        _plugin_registry=_FakeRegistry(collectors=[_RaisingCollector()])
    )

    items, _ = collect_module(path, session=session)

    # The regular test item should still be collected despite the collector error
    assert len(items) == 1, (
        "base test should still be collected when a plugin "
        f"collector fails, got {len(items)}"
    )

    collector_warnings = [
        w for w in warn.warnings if issubclass(w.category, PluginCollectorWarning)
    ]
    assert len(collector_warnings) == 1, (
        "expected exactly 1 PluginCollectorWarning, "
        f"got {len(collector_warnings)}: {warn.warnings}"
    )
    msg = str(collector_warnings[0].message)
    assert "_RaisingCollector" in msg, (
        f"warning should identify the collector class, got: {msg}"
    )
    assert "collector went boom" in msg, (
        f"warning should include the original error message, got: {msg}"
    )


def test_non_collected_item_emits_warning(tmp: TempDir, warn: WarnCapture) -> None:
    """A collector returning non-CollectedItem values emits a warning per bad item."""
    path = helpers.common.write_test_module(
        tmp, "def test_ok(): pass\n", name="test_col_bad.py"
    )
    session = _FakeSession(
        _plugin_registry=_FakeRegistry(collectors=[_BadReturnCollector()])
    )

    items, _ = collect_module(path, session=session)

    # Only the base test should be collected; bad returns are dropped
    assert len(items) == 1, (
        f"non-CollectedItem returns should be dropped, got {len(items)} items"
    )

    collector_warnings = [
        w for w in warn.warnings if issubclass(w.category, PluginCollectorWarning)
    ]
    assert len(collector_warnings) == 2, (
        "expected 2 warnings (one per bad item), "
        f"got {len(collector_warnings)}: {warn.warnings}"
    )
    msg0 = str(collector_warnings[0].message)
    assert "_BadReturnCollector" in msg0, (
        f"warning should identify the collector class, got: {msg0}"
    )
    assert "str" in msg0, f"warning should identify the unexpected type, got: {msg0}"
    msg1 = str(collector_warnings[1].message)
    assert "int" in msg1, f"second warning should identify 'int' type, got: {msg1}"


def test_good_collector_adds_items_no_warnings(tmp: TempDir, warn: WarnCapture) -> None:
    """A well-behaved collector adds items and emits no warnings."""
    path = helpers.common.write_test_module(
        tmp, "def test_ok(): pass\n", name="test_col_good.py"
    )
    session = _FakeSession(
        _plugin_registry=_FakeRegistry(collectors=[_GoodCollector()])
    )

    items, _ = collect_module(path, session=session)

    assert len(items) == 2, (
        f"expected 2 items (1 base + 1 from plugin), got {len(items)}"
    )
    plugin_item = [i for i in items if i.fn_name == "test_from_plugin"]
    assert len(plugin_item) == 1, (
        "plugin-collected item should be present, "
        f"got names: {[i.fn_name for i in items]}"
    )

    collector_warnings = [
        w for w in warn.warnings if issubclass(w.category, PluginCollectorWarning)
    ]
    assert len(collector_warnings) == 0, (
        f"a well-behaved collector should emit no warnings, got: {collector_warnings}"
    )


# ── _get_fixture_deps tests ────────────────────────────────────────────────────


@dataclass(frozen=True)
class _MyDB:
    """Dummy fixture type for tests."""

    host: str = "localhost"


def test_get_fixture_deps_includes_builtins() -> None:
    """_get_fixture_deps includes builtins (unlike old _get_fixture_names)."""
    # Use exec to avoid `from __future__ import annotations` stringification
    ns: dict[str, object] = {"Fixture": Fixture, "_MyDB": _MyDB, "TempDir": TempDir}
    exec(
        "def test_fn(db: Fixture[_MyDB], tmp: TempDir) -> None: ...",
        ns,
    )
    test_fn = ns["test_fn"]

    deps = _get_fixture_deps(test_fn)
    type_names = [t for _, t in deps]
    assert "_MyDB" in type_names, "should include user fixture type in deps"
    assert "TempDir" in type_names, (
        "should include builtin fixture type in deps — "
        "_get_fixture_deps does NOT exclude builtins"
    )


def test_get_fixture_deps_skips_non_fixture() -> None:
    """Plain-typed params are not included."""
    ns: dict[str, object] = {"Fixture": Fixture, "_MyDB": _MyDB}
    exec(
        "def test_fn(x: int, db: Fixture[_MyDB]) -> None: ...",
        ns,
    )
    test_fn = ns["test_fn"]

    deps = _get_fixture_deps(test_fn)
    assert len(deps) == 1, (
        f"should only include Fixture[T]-annotated params, got {deps}"
    )
    assert deps[0] == ("db", "_MyDB"), (
        f"should be (qualifier, type_name) tuple, got {deps[0]!r}"
    )


def test_get_fixture_deps_returns_qualifier_and_type() -> None:
    """Each dep is a (qualifier, type_name) tuple."""
    ns: dict[str, object] = {"Fixture": Fixture, "_MyDB": _MyDB, "TempDir": TempDir}
    exec(
        "def test_fn(db: Fixture[_MyDB], tmp: Fixture[TempDir]) -> None: ...",
        ns,
    )
    test_fn = ns["test_fn"]

    deps = _get_fixture_deps(test_fn)
    deps_dict = dict(deps)
    assert deps_dict.get("db") == "_MyDB", f"expected db -> _MyDB, got {deps_dict}"
    assert deps_dict.get("tmp") == "TempDir", (
        f"expected tmp -> TempDir, got {deps_dict}"
    )


def test_get_fixture_deps_skips_return_annotation() -> None:
    """Return annotation is not included in deps."""
    ns: dict[str, object] = {"Fixture": Fixture, "_MyDB": _MyDB}
    exec(
        "def test_fn(db: Fixture[_MyDB]) -> None: ...",
        ns,
    )
    test_fn = ns["test_fn"]

    deps = _get_fixture_deps(test_fn)
    qualifiers = [q for q, _ in deps]
    assert "return" not in qualifiers, (
        f"return annotation should not be in deps, got {deps}"
    )


# --- allow-comment gate tests for _check_module_registrars ---


def test_fixtures_without_allow_comment_blocked(tmp: TempDir) -> None:
    """Fixtures() without allow comment emits violation and does NOT register."""
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "fixtures = oxitest.Fixtures()\n"
        "@fixtures.fixture\n"
        "def local_val():\n"
        "    return 99\n"
        "def test_foo(local_val): pass\n",
        name="test_no_allow.py",
    )
    registry = FixtureRegistry()

    class _FakeSession:
        _registry = registry
        _module_cache = None

    _, violations = collect_module(path, session=_FakeSession())
    registrar_viols = [
        v for v in violations if v.kind == ViolationKind.REGISTRAR_IN_TEST_MODULE
    ]
    assert len(registrar_viols) == 1, (
        "Fixtures() without allow comment should emit a violation — "
        f"got {len(registrar_viols)}: {registrar_viols}"
    )
    assert "Fixtures" in registrar_viols[0].detail, (
        f"violation detail should mention Fixtures, got {registrar_viols[0].detail!r}"
    )
    assert registry.get("local_val") is None, (
        "local_val should NOT be registered — no allow comment means blocked"
    )


def test_helpers_with_allow_comment_suppressed(tmp: TempDir) -> None:
    """Helpers() with allow comment emits no violation."""
    path = helpers.common.write_test_module(
        tmp,
        "from oxitest import Helpers\n"
        "h = Helpers()  "
        "# oxitest: allow[registrar-in-test-module]\n"
        "@h.helper\n"
        "def my_helper():\n"
        "    return 42\n"
        "def test_foo(): pass\n",
        name="test_helpers_allow.py",
    )
    _, violations = collect_module(path)
    registrar_viols = [
        v for v in violations if v.kind == ViolationKind.REGISTRAR_IN_TEST_MODULE
    ]
    assert len(registrar_viols) == 0, (
        "allow comment should suppress Helpers() violation — "
        f"got {len(registrar_viols)}: {registrar_viols}"
    )
