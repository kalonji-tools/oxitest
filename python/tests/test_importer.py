"""Tests for collect_module, item collection, module marks, and class propagation."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType, ModuleType
from typing import Any

import oxitest
from oxitest import CollectedItem, TempDir, raises
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixture_type import Fixture
from oxitest._bridge._fn_metadata import _update, get_metadata
from oxitest._bridge._mark_api import MarkInfo
from oxitest._bridge._test_kind import Parametrized, Solitary
from oxitest._bridge._violation_checkers import check_fn_violations
from oxitest._bridge.importer import (
    _apply_module_marks,
    _collect_items,
    _extract_module_marks,
    _get_fixture_deps,
    _module_members,
    _propagate_class_metadata,
    collect_module,
)
from oxitest._bridge.parametrize import DictCases
from oxitest._bridge.result import Diagnostic, ViolationKind
from tests import helpers


def test_collect_empty_module(tmp: TempDir) -> None:
    """An empty module yields no collected items."""
    path = helpers.write_test_module(tmp, "", name="test_empty.py")
    items, _ = collect_module(path)
    assert items == [], (
        f"an empty module has no test_ functions -- the collector must return nothing"
        f" or the runner will try to schedule phantom tests: {items}"
    )


def test_collect_single_test_function(tmp: TempDir) -> None:
    """A module with one test_ function produces a single CollectedItem."""
    path = helpers.write_test_module(tmp, "def test_bar(): pass\n", name="test_foo.py")
    items, _ = collect_module(path)
    assert len(items) == 1, (
        f"each test_ function must map to exactly one CollectedItem for the runner to"
        f" schedule it: {items}"
    )
    assert items[0].fn_name == "test_bar", (
        f"fn_name drives test identification in reports and --lf reruns -- a wrong name"
        f" makes the test unlinkable: {items[0].fn_name!r}"
    )
    assert items[0].lineno == 1, (
        f"lineno anchors error messages and IDE jump-to-source -- an incorrect line"
        f" misleads developers during debugging: {items[0].lineno}"
    )
    assert items[0].markers == (), (
        f"unmarked functions must carry zero markers so the runner never applies"
        f" skip/xfail/timeout behavior by accident: {items[0].markers}"
    )


def test_collect_multiple_functions(tmp: TempDir) -> None:
    """All test_ functions in a module are collected as separate items."""
    path = helpers.write_test_module(
        tmp, "def test_one(): pass\ndef test_two(): pass\n", name="test_multi.py"
    )
    items, _ = collect_module(path)
    assert len(items) == 2, (
        f"every test_ function must produce exactly one CollectedItem -- missing items"
        f" silently skip tests: {[i.fn_name for i in items]}"
    )
    names = [item.fn_name for item in items]
    assert "test_one" in names, (
        f"the collector must discover all test_ functions or they will be silently"
        f" skipped by the runner: {names}"
    )
    assert "test_two" in names, (
        f"the collector must discover all test_ functions or they will be silently"
        f" skipped by the runner: {names}"
    )


def test_collect_ignores_non_test_functions(tmp: TempDir) -> None:
    """Functions not prefixed with test_ are silently excluded from collection."""
    path = helpers.write_test_module(
        tmp, "def helper(): pass\ndef test_real(): pass\n", name="test_foo.py"
    )
    items, _ = collect_module(path)
    assert len(items) == 1, (
        f"only test_-prefixed functions are collected -- non-prefixed helpers must"
        f" never leak into the schedule: "
        f"{[i.fn_name for i in items]}"
    )
    assert items[0].fn_name == "test_real", (
        f"non-test helpers leaking into the schedule would be run as tests and produce"
        f" false passes or cryptic failures: {items[0].fn_name!r}"
    )


def test_collect_raises_on_missing_file() -> None:
    """collect_module raises an exception when the given path does not exist."""
    with raises(Exception):
        collect_module("/nonexistent/path/test_foo.py")


def test_collect_error_message_is_clean_traceback_not_testrepr(tmp: TempDir) -> None:
    """collect_module error: plain traceback, no TestResult repr, real newlines."""
    path = helpers.write_test_module(
        tmp, "import _oxitest_nonexistent_module_xyz\n", name="test_bad_import.py"
    )
    with oxitest.raises(ImportError) as exc_info:
        collect_module(path)
    msg = str(exc_info.value)
    assert "TestResult(" not in msg, (
        f"users see this message raw -- TestResult repr is an internal data structure"
        f" that leaks implementation details and confuses debugging: {msg!r}"
    )
    assert "\n" in msg, (
        f"tracebacks need real newlines for readability -- escaped \\n renders as a"
        f" single unreadable line in terminals: {msg!r}"
    )
    assert "Traceback" in msg, (
        f"without the Traceback header users cannot identify the error as a Python"
        f" traceback or locate the call chain: {msg!r}"
    )
    assert "ModuleNotFoundError" in msg or "ImportError" in msg, (
        f"the concrete exception type tells the user whether the problem is a missing"
        f" module, a syntax error, or something else -- omitting it forces guesswork:"
        f" {msg!r}"
    )


def test_collect_extracts_marker_names(tmp: TempDir) -> None:
    """Marker names from @mark decorators appear in the CollectedItem.markers tuple."""
    path = helpers.write_test_module(
        tmp,
        "import oxitest\n@oxitest.mark.slow\ndef test_query(): pass\n",
        name="test_marked.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 1, (
        f"each test_ function must map to exactly one CollectedItem for the runner to"
        f" schedule it: {len(items)}"
    )
    assert items[0].markers == ("slow",), (
        f"@mark decorators must appear in CollectedItem.markers so the runner can apply"
        f" filtering (e.g. -m slow) and behavior (skip/xfail): {items[0].markers}"
    )


def test_collect_extracts_multiple_markers(tmp: TempDir) -> None:
    """All stacked @mark decorators appear in the CollectedItem.markers tuple."""
    path = helpers.write_test_module(
        tmp,
        "import oxitest\n"
        "@oxitest.mark.slow\n"
        "@oxitest.mark.integration\n"
        "def test_query(): pass\n",
        name="test_multi_mark.py",
    )
    items, _ = collect_module(path)
    assert "slow" in items[0].markers, (
        f"stacked @mark decorators must all appear in markers -- a missing marker"
        f" breaks -m filtering and causes silent test inclusion/exclusion:"
        f" {items[0].markers}"
    )
    assert "integration" in items[0].markers, (
        f"stacked @mark decorators must all appear in markers -- a missing marker"
        f" breaks -m filtering and causes silent test inclusion/exclusion:"
        f" {items[0].markers}"
    )


def test_propagate_class_metadata_copies_arranged() -> None:
    """_propagate_class_metadata prepends arranged entries from class to function."""

    @oxitest.arrange("db")
    class FakeClass:
        pass

    def test_fn() -> None:
        pass

    _propagate_class_metadata(test_fn, FakeClass)
    meta = get_metadata(test_fn)
    assert "db" in meta.arranged, (
        "class-level arranged must propagate to methods -- without propagation,"
        " fixture dependencies declared on the class silently disappear and methods run"
        " without required fixtures"
    )


def test_propagate_class_marks_copies_all_marks() -> None:
    """_propagate_class_metadata copies ALL marks from class to function."""

    @oxitest.mark.skip(reason="class skip")
    class FakeClass:
        pass

    def test_fn() -> None:
        pass

    _propagate_class_metadata(test_fn, FakeClass)
    assert any(m.name == "skip" for m in get_metadata(test_fn).marks), (
        "class-level marks propagate unconditionally -- skip on a class means all its"
        " methods are skipped, otherwise individual methods run when the author"
        " intended them skipped"
    )


def test_collect_class_methods_use_qualified_name(tmp: TempDir) -> None:
    """Class methods are returned as 'ClassName::method_name'."""
    path = helpers.write_test_module(
        tmp,
        "class TestSuite:\n"
        "    def test_foo(self): pass\n"
        "    def test_bar(self): pass\n",
        name="test_cls.py",
    )
    items, _ = collect_module(path)
    names = [item.fn_name for item in items]
    assert "TestSuite::test_foo" in names, (
        f"class methods use 'ClassName::method' qualified names so the runner can"
        f" disambiguate identically named methods across classes and route --lf reruns"
        f" correctly: {names}"
    )
    assert "TestSuite::test_bar" in names, (
        f"class methods use 'ClassName::method' qualified names so the runner can"
        f" disambiguate identically named methods across classes and route --lf reruns"
        f" correctly: {names}"
    )


def test_collect_class_methods_with_arrange_propagation(tmp: TempDir) -> None:
    """Class-level @oxi.arrange propagates to each test method at collection."""
    path = helpers.write_test_module(
        tmp,
        "import oxitest\n"
        "@oxitest.arrange('db')\n"
        "class TestSuite:\n"
        "    def test_foo(self): pass\n"
        "    def test_bar(self): pass\n",
        name="test_cls.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 2, (
        f"both test methods must be collected -- missing a method means it silently"
        f" never runs: {[i.fn_name for i in items]}"
    )
    for item in items:
        assert "db" in item.arranged, (
            f"class-level arranged must propagate to every method -- without it,"
            f" {item.fn_name!r} runs without the required fixture setup and produces"
            f" unreliable results: "
            f"arranged={item.arranged}"
        )


def test_collect_class_skip_propagated(tmp: TempDir) -> None:
    """A skip mark on a class IS propagated to all test methods at collection time."""
    path = helpers.write_test_module(
        tmp,
        "import oxitest\n"
        "@oxitest.mark.skip(reason='class skip')\n"
        "class TestSuite:\n"
        "    def test_foo(self): pass\n",
        name="test_cls_skip.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 1, (
        f"the single test method must be collected so propagation can be verified:"
        f" {len(items)}"
    )
    assert "skip" in items[0].markers, (
        f"class-level skip must propagate to every method -- without it the runner"
        f" executes tests the author intended to skip, producing misleading results: "
        f"{items[0].markers}"
    )


def test_collected_item_can_be_constructed() -> None:
    """CollectedItem can be constructed with all fields and stores each correctly."""
    item = CollectedItem(
        fn_name="test_foo",
        lineno=1,
        markers=(),
        kind=Solitary(),
        param_values=(),
        is_async=False,
    )
    assert item.fn_name == "test_foo", (
        f"fn_name is the primary key for test identification in reports and --lf -- it"
        f" must survive round-trip construction: {item.fn_name!r}"
    )
    assert item.lineno == 1, (
        f"lineno drives jump-to-source in reporters and IDEs -- a wrong value sends"
        f" developers to the wrong line: {item.lineno}"
    )
    assert item.markers == (), (
        f"default-constructed items must have empty markers so no skip/xfail/timeout"
        f" behavior is accidentally triggered: {item.markers}"
    )
    assert item.param_id is None, (
        f"kind=Solitary() means param_id is None -- a non-None default would"
        f" cause the runner to look up nonexistent parameter sets: {item.param_id!r}"
    )
    assert item.param_values == (), (
        f"param_values must default to empty -- stale values would inject unexpected"
        f" arguments into the test function call: {item.param_values}"
    )
    assert item.is_async is False, (
        f"is_async=False is the default -- a True default would cause the runner to"
        f" wrap synchronous functions in asyncio.run and break them: {item.is_async!r}"
    )


def test_collected_item_with_markers_and_param() -> None:
    """CollectedItem stores non-default markers and param_id correctly."""
    item = CollectedItem(
        fn_name="test_bar",
        lineno=5,
        markers=("slow",),
        kind=Parametrized(param_id="case_a"),
        param_values=(("x", "1"), ("y", "2")),
        is_async=False,
    )
    assert item.markers == ("slow",), (
        f"markers must be stored exactly as given -- lost markers break -m filtering"
        f" and mark-dependent behavior: {item.markers}"
    )
    assert item.param_id == "case_a", (
        f"param_id identifies the parametrize case in reports and --lf reruns -- a"
        f" wrong id makes individual cases unaddressable: {item.param_id!r}"
    )
    assert item.param_values == (("x", "1"), ("y", "2")), (
        f"param_values feed argument injection at runtime -- corrupted values silently"
        f" inject wrong data into the test function: {item.param_values}"
    )


def test_collect_module_emits_violation_for_helpers_in_test_module(
    tmp: TempDir,
) -> None:
    """Helpers() instance without allow comment emits REGISTRAR_IN_TEST_MODULE."""
    path = helpers.write_test_module(
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
        f"Helpers() in a test module without an allow comment must emit exactly one"
        f" violation -- zero means the guard is broken, more than one means duplicate"
        f" detection: {registrar_viols}"
    )
    v = registrar_viols[0]
    assert v.node_id == path, (
        f"node_id must point to the offending module so reporters can show the file"
        f" path in diagnostics -- a wrong path sends users to the wrong file:"
        f" {v.node_id!r}"
    )
    assert "Helpers" in v.detail, (
        f"the detail must name the registrar class so the user knows which instance"
        f" triggered the violation and can locate it in the source: {v.detail!r}"
    )
    assert "allow[registrar-in-test-module]" in v.detail, (
        f"the detail must include the allow comment syntax so the user knows exactly"
        f" how to suppress the violation if intentional: {v.detail!r}"
    )


def _write_py(tmp_path: TempDir, src: str) -> str:
    """Write source code to a temp file and return its path as str."""
    return helpers.write_test_module(tmp_path, src, name="test_viol.py")


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
        f"bare-assert detection lives in Rust (bare_asserts.rs) -- if Python also emits"
        f" these, violations get double-reported and strict mode double-counts: {bare}"
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
        f"asserts with messages satisfy the strict-mode contract -- flagging them as"
        f" violations would force users to add redundant messages and erode trust in"
        f" the linter: {violations}"
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
    assert bare == [], (
        f"bare asserts inside nested helpers must not be attributed to the enclosing"
        f" test_ function -- false positives train users to ignore real violations:"
        f" {bare}"
    )


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
        f"collect_violations=False must suppress all violation checks -- emitting"
        f" violations when disabled breaks the opt-in contract and slows collection"
        f" unnecessarily: {violations}"
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
        f"dict-parametrize on a class method must produce exactly one violation -- zero"
        f" means the checker ignores class methods, more means duplicate detection:"
        f" {violations}"
    )
    v = violations[0]
    assert v.kind == ViolationKind.DICT_PARAMETRIZE, (
        f"the violation must be typed as DICT_PARAMETRIZE so reporters can distinguish"
        f" it from other violation kinds and apply the correct diagnostic message:"
        f" {v.kind!r}"
    )
    assert v.node_id == f"{path}::{fn_name}", (
        f"node_id must include the full qualified path so reporters can pinpoint the"
        f" exact method in diagnostics and IDE integrations: {v.node_id!r}"
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
        f"skip without reason= on a class method must produce exactly one violation --"
        f" missing it lets undocumented skips persist indefinitely: {violations}"
    )
    v = violations[0]
    assert v.kind == ViolationKind.MISSING_MARK_REASON, (
        f"the violation must be typed as MISSING_MARK_REASON so the reporter can"
        f" suggest adding reason= in its diagnostic: {v.kind!r}"
    )
    assert v.node_id == f"{path}::{fn_name}", (
        f"node_id must carry the full qualified path so the reporter can pinpoint the"
        f" exact method in its output: {v.node_id!r}"
    )
    assert v.detail == "skip", (
        f"detail must name the mark ('skip') so the diagnostic tells the user which"
        f" decorator needs a reason= argument: {v.detail!r}"
    )


def test_collect_async_function_sets_is_async(tmp: TempDir) -> None:
    """An async def test_ function is collected with is_async=True."""
    path = helpers.write_test_module(
        tmp, "async def test_hello(): pass\n", name="test_async.py"
    )
    items, _ = collect_module(path)
    assert len(items) == 1, (
        f"each test_ function must map to exactly one CollectedItem for the runner to"
        f" schedule it: {len(items)}"
    )
    assert items[0].is_async is True, (
        f"is_async=True tells the runner to wrap execution in asyncio.run -- missing it"
        f" causes a coroutine-never-awaited error: {items[0].is_async!r}"
    )


def test_collect_sync_function_sets_is_async_false(tmp: TempDir) -> None:
    """A synchronous def test_ function is collected with is_async=False."""
    path = helpers.write_test_module(
        tmp, "def test_hello(): pass\n", name="test_sync.py"
    )
    items, _ = collect_module(path)
    assert len(items) == 1, (
        f"each test_ function must map to exactly one CollectedItem for the runner to"
        f" schedule it: {len(items)}"
    )
    assert items[0].is_async is False, (
        f"is_async=False tells the runner to call the function directly -- a wrong True"
        f" would wrap a sync function in asyncio.run and break it:"
        f" {items[0].is_async!r}"
    )


def test_collect_mixed_sync_async(tmp: TempDir) -> None:
    """A module with both sync and async tests sets is_async correctly on each item."""
    path = helpers.write_test_module(
        tmp,
        "def test_sync(): pass\nasync def test_async(): pass\n",
        name="test_mixed.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 2, (
        f"both sync and async test_ functions must be collected -- missing one silently"
        f" drops a test from the schedule: {len(items)}"
    )
    by_name = {item.fn_name: item for item in items}
    assert by_name["test_sync"].is_async is False, (
        "sync functions must be is_async=False -- a wrong True wraps them in"
        " asyncio.run and breaks execution"
    )
    assert by_name["test_async"].is_async is True, (
        "async functions must be is_async=True -- a wrong False calls them without"
        " await, producing a coroutine-never-awaited error"
    )


def test_fixtures_in_test_module_are_registered_with_allow(tmp: TempDir) -> None:
    """Fixtures() with allow comment registers silently — no violation."""
    path = helpers.write_test_module(
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
        "the allow comment is the opt-in gate for registrars in test modules --"
        " emitting a violation despite the comment breaks the suppression contract and"
        " forces users to move fixtures elsewhere: "
        f"{registrar_viols}"
    )
    assert registry.get("async_val") is not None, (
        "the allow comment authorizes fixture registration -- if fixtures are not"
        " registered despite the comment, the test will fail at runtime with 'unknown"
        " fixture' errors"
    )
    assert registry.get("sync_val") is not None, (
        "the allow comment authorizes fixture registration -- if fixtures are not"
        " registered despite the comment, the test will fail at runtime with 'unknown"
        " fixture' errors"
    )


def test_collect_async_class_method_sets_is_async(tmp: TempDir) -> None:
    """Async and sync class methods each receive the correct is_async value."""
    path = helpers.write_test_module(
        tmp,
        "class TestSuite:\n"
        "    async def test_async_method(self): pass\n"
        "    def test_sync_method(self): pass\n",
        name="test_cls_async.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 2, (
        f"both class methods must be collected -- missing one means a test silently"
        f" never runs: {len(items)}"
    )
    by_name = {item.fn_name: item for item in items}
    assert by_name["TestSuite::test_async_method"].is_async is True, (
        "async class methods need is_async=True so the runner wraps them in asyncio.run"
        " -- without it the coroutine is never awaited"
    )
    assert by_name["TestSuite::test_sync_method"].is_async is False, (
        "sync class methods need is_async=False -- a wrong True wraps a regular"
        " function in asyncio.run and breaks execution"
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
    assert "test_one" in names, (
        f"_module_members must yield all test_-prefixed functions -- missing one"
        f" silently drops a test from the schedule: {names}"
    )
    assert "test_two" in names, (
        f"_module_members must yield all test_-prefixed functions -- missing one"
        f" silently drops a test from the schedule: {names}"
    )
    assert "helper" not in names, (
        f"non-test helpers must be excluded -- yielding them would inject non-test"
        f" callables into the schedule and produce false passes or type errors: {names}"
    )


def test_collect_items_returns_collected_items() -> None:
    """_collect_items wraps member functions into CollectedItem values with metadata."""

    def fake_fn() -> None:
        pass

    lineno = fake_fn.__code__.co_firstlineno
    members = [("test_fake", fake_fn)]
    items, _ = _collect_items(members, "/fake.py", collect_violations=False)
    assert len(items) == 1, (
        f"each member function must produce exactly one CollectedItem for the runner to"
        f" schedule it: {len(items)}"
    )
    assert items[0].fn_name == "test_fake", (
        f"fn_name must match the member name so reports and --lf reruns can identify"
        f" the test: {items[0].fn_name!r}"
    )
    assert items[0].lineno == lineno, (
        f"lineno must come from the function's code object so reporters and IDEs can"
        f" jump to the correct source line: {items[0].lineno}"
    )


# ── _extract_module_marks tests ───────────────────────────────────────────────


def test_extract_module_marks_none_returns_empty() -> None:
    """No oxi_mark attribute → empty list, no violations."""
    module = ModuleType("test_no_marks")
    marks, violations = _extract_module_marks(module, "/fake/test_no_marks.py")
    assert marks == [], (
        f"modules without oxi_mark must produce no marks -- phantom marks would apply"
        f" unintended behavior (skip/timeout) to every test in the module: {marks}"
    )
    assert violations == [], (
        f"a module with no oxi_mark attribute is valid -- emitting violations for its"
        f" absence would be a false positive: {violations}"
    )


def test_extract_module_marks_single_mark() -> None:
    """oxi_mark = oxi.mark.slow → list with one MarkInfo."""
    module = ModuleType("test_single")
    setattr(module, "oxi_mark", MarkInfo("slow", (), MappingProxyType({})))  # noqa: B010
    marks, violations = _extract_module_marks(module, "/fake/test_single.py")
    assert len(marks) == 1, (
        f"a single MarkInfo in oxi_mark must produce exactly one extracted mark -- zero"
        f" means it was silently dropped: {len(marks)}"
    )
    assert marks[0].name == "slow", (
        f"the extracted mark name must match the MarkInfo -- a wrong name applies the"
        f" wrong behavior (e.g. skip instead of slow): {marks[0].name!r}"
    )
    assert violations == [], (
        f"a valid MarkInfo is not a violation -- false positives would train users to"
        f" ignore real violations: {violations}"
    )


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
    assert len(marks) == 2, (
        f"every MarkInfo in the oxi_mark list must be extracted -- missing marks"
        f" silently drop module-wide behavior: {len(marks)}"
    )
    names = [m.name for m in marks]
    assert "slow" in names, (
        f"each mark in the list must be preserved -- dropping 'slow' means -m slow"
        f" filtering will not match this module's tests: {names}"
    )
    assert "timeout" in names, (
        f"each mark in the list must be preserved -- dropping 'timeout' means"
        f" module-wide timeout enforcement silently disappears: {names}"
    )
    assert violations == [], (
        f"a list of valid MarkInfos is not a violation -- false positives erode trust"
        f" in the linter: {violations}"
    )


def test_extract_module_marks_tuple() -> None:
    """oxi_mark as tuple is accepted."""
    module = ModuleType("test_tuple")
    setattr(module, "oxi_mark", (MarkInfo("slow", (), MappingProxyType({})),))  # noqa: B010
    marks, _ = _extract_module_marks(module, "/fake/test_tuple.py")
    assert len(marks) == 1, (
        f"oxi_mark as a tuple must be accepted like a list -- rejecting it would break"
        f" users who prefer tuple syntax for immutability: {len(marks)}"
    )


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
    assert len(marks) == 1, (
        f"valid MarkInfo entries must still be extracted even when invalid entries are"
        f" present -- dropping them penalizes correct entries for sibling errors:"
        f" {len(marks)}"
    )
    assert marks[0].name == "slow", (
        f"the valid mark must preserve its name so -m filtering and mark-dependent"
        f" behavior work correctly: {marks[0].name!r}"
    )
    assert len(violations) == 2, (
        f"each non-MarkInfo entry must produce exactly one violation -- missing"
        f" violations let invalid config pass silently: {len(violations)}"
    )
    for v in violations:
        assert v.kind == ViolationKind.INVALID_MODULE_MARK, (
            f"invalid oxi_mark entries must be typed as INVALID_MODULE_MARK so the"
            f" reporter can show the correct diagnostic and suggest MarkInfo usage:"
            f" {v.kind}"
        )


# ── _apply_module_marks tests ─────────────────────────────────────────────────


def test_apply_module_marks_prepends_to_unmarked_fn() -> None:
    """Module marks are added to functions with no per-test marks."""

    def test_fn() -> None:
        pass

    module_marks = [MarkInfo("slow", (), MappingProxyType({}))]
    _apply_module_marks([("test_fn", test_fn)], module_marks)
    marks = get_metadata(test_fn).marks
    assert len(marks) == 1, (
        f"module marks must be applied to unmarked functions -- without them,"
        f" module-wide behavior (skip/timeout/slow) silently disappears: {len(marks)}"
    )
    assert marks[0].name == "slow", (
        f"the applied mark must match the module mark name so -m filtering and"
        f" mark-dependent behavior activate correctly: {marks[0].name!r}"
    )


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
        f"per-test and module marks of the same name must not stack -- duplicates would"
        f" cause the runner to apply the behavior twice (e.g. double timeout):"
        f" {timeout_marks}"
    )
    assert timeout_marks[0].kwargs == {"seconds": 5}, (
        f"per-test marks must override module marks of the same name -- the"
        f" function-level decorator is more specific and must win, otherwise authors"
        f" cannot customize individual tests: "
        f"{timeout_marks[0].kwargs}"
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
    assert "slow" in names, (
        f"non-conflicting module marks must be added alongside per-test marks --"
        f" dropping them silently removes module-wide behavior from individually marked"
        f" tests: {names}"
    )
    assert "timeout" in names, (
        f"existing per-test marks must survive module mark application -- losing them"
        f" means function-level decorators have no effect: {names}"
    )


def test_apply_module_marks_empty_list_is_noop() -> None:
    """Empty module_marks list does not modify functions."""

    def test_fn() -> None:
        pass

    _apply_module_marks([("test_fn", test_fn)], [])
    marks = get_metadata(test_fn).marks
    assert marks == (), (
        f"applying an empty module_marks list must be a no-op -- injecting phantom"
        f" marks would apply unintended behavior to all tests: {marks}"
    )


# ── collect_module oxi_mark integration tests ─────────────────────────────────


def test_collect_module_with_oxi_mark_single(tmp: TempDir) -> None:
    """oxi_mark = oxi.mark.slow applies 'slow' to all tests."""
    path = helpers.write_test_module(
        tmp,
        "import oxitest\n"
        "oxi_mark = oxitest.mark.slow\n"
        "def test_a(): pass\n"
        "def test_b(): pass\n",
        name="test_mod_mark.py",
    )
    items, violations = collect_module(path)
    assert len(items) == 2, (
        f"all test_ functions must be collected for module marks to have any effect:"
        f" {len(items)}"
    )
    for item in items:
        assert "slow" in item.markers, (
            f"oxi_mark = mark.slow must propagate to every test -- without it, -m slow"
            f" filtering excludes {item.fn_name} from slow-only runs: {item.markers}"
        )
    assert violations == [], (
        f"a valid single MarkInfo in oxi_mark must not produce violations -- false"
        f" positives erode trust in the linter: {violations}"
    )


def test_collect_module_with_oxi_mark_list(tmp: TempDir) -> None:
    """oxi_mark = [mark.slow, mark.timeout(10)] applies both marks to all tests."""
    path = helpers.write_test_module(
        tmp,
        "import oxitest\n"
        "oxi_mark = [oxitest.mark.slow, oxitest.mark.timeout(10)]\n"
        "def test_a(): pass\n"
        "def test_b(): pass\n",
        name="test_mod_marks.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 2, (
        f"all test_ functions must be collected for module marks to have any effect:"
        f" {len(items)}"
    )
    for item in items:
        assert "slow" in item.markers, (
            f"every mark in the oxi_mark list must propagate to every test -- dropping"
            f" 'slow' means -m slow filtering silently excludes {item.fn_name}:"
            f" {item.markers}"
        )
        assert "timeout" in item.markers, (
            f"every mark in the oxi_mark list must propagate to every test -- dropping"
            f" 'timeout' means module-wide timeout enforcement silently disappears for"
            f" {item.fn_name}: {item.markers}"
        )


def test_collect_module_oxi_mark_per_test_override(tmp: TempDir) -> None:
    """Per-test mark overrides module mark of the same name."""
    path = helpers.write_test_module(
        tmp,
        "import oxitest\n"
        "oxi_mark = [oxitest.mark.timeout(120)]\n"
        "@oxitest.mark.timeout(5)\n"
        "def test_fast(): pass\n"
        "def test_slow(): pass\n",
        name="test_override.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 2, (
        f"both functions must be collected so per-test vs module mark override can be"
        f" verified on each: {len(items)}"
    )
    for item in items:
        assert "timeout" in item.markers, (
            f"timeout must appear on {item.fn_name} -- the per-test override replaces"
            f" the module mark, but the mark name must still be present for the runner"
            f" to enforce the timeout: {item.markers}"
        )


def test_collect_module_oxi_mark_with_parametrize(tmp: TempDir) -> None:
    """Module marks apply to each parametrize case."""
    path = helpers.write_test_module(
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
    assert len(items) == 2, (
        f"each parametrize case must produce a separate CollectedItem -- missing cases"
        f" silently skip parameter combinations: {len(items)}"
    )
    for item in items:
        assert "slow" in item.markers, (
            f"module marks must propagate to every parametrize case -- without it, -m"
            f" slow filtering excludes {item.fn_name}[{item.param_id}] from slow-only"
            f" runs: "
            f"{item.markers}"
        )


def test_collect_module_oxi_mark_applies_to_class_methods(tmp: TempDir) -> None:
    """Module marks apply to test methods inside Test* classes."""
    path = helpers.write_test_module(
        tmp,
        "import oxitest\n"
        "oxi_mark = [oxitest.mark.slow]\n"
        "class TestSuite:\n"
        "    def test_foo(self): pass\n"
        "    def test_bar(self): pass\n",
        name="test_cls_mod.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 2, (
        f"both class methods must be collected for module mark propagation to be"
        f" verified: {len(items)}"
    )
    for item in items:
        assert "slow" in item.markers, (
            f"module marks must propagate to class methods the same as top-level"
            f" functions -- without it, class methods in this module are excluded from"
            f" -m slow runs: "
            f"{item.markers}"
        )


def test_collect_module_oxi_mark_invalid_entry_violation(tmp: TempDir) -> None:
    """Invalid entries in oxi_mark produce violations."""
    path = helpers.write_test_module(
        tmp,
        "import oxitest\noxi_mark = [oxitest.mark.slow, 42]\ndef test_a(): pass\n",
        name="test_bad_mark.py",
    )
    items, violations = collect_module(path)
    assert len(items) == 1, (
        f"test collection must succeed even when oxi_mark has invalid entries --"
        f" blocking collection penalizes the entire module for one bad entry:"
        f" {len(items)}"
    )
    assert "slow" in items[0].markers, (
        f"valid marks must still be applied even when sibling entries are invalid --"
        f" dropping them punishes correct usage: {items[0].markers}"
    )
    assert len(violations) == 1, (
        f"each invalid oxi_mark entry must produce exactly one violation -- zero means"
        f" invalid config passes silently, more than one means duplicate detection:"
        f" {len(violations)}"
    )
    assert violations[0].kind == ViolationKind.INVALID_MODULE_MARK, (
        f"the violation must be typed as INVALID_MODULE_MARK so the reporter can show"
        f" the correct diagnostic and guide the user to fix the entry:"
        f" {violations[0].kind}"
    )


# ── class-level mark propagation tests ─────────────────────────────────────


def test_propagate_class_marks_copies_skip(tmp: TempDir) -> None:
    """Class-level @mark.skip propagates to all methods."""
    path = helpers.write_test_module(
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
    assert len(items) == 2, (
        f"both class methods must be collected so skip propagation can be verified on"
        f" each: {len(items)}"
    )
    for item in items:
        assert "skip" in item.markers, (
            f"class-level skip must propagate to every method -- without it,"
            f" {item.fn_name} runs when the author intended the entire class skipped:"
            f" {item.markers}"
        )


def test_propagate_class_marks_copies_timeout(tmp: TempDir) -> None:
    """Class-level @mark.timeout propagates to all methods."""
    path = helpers.write_test_module(
        tmp,
        "import oxitest\n"
        "\n"
        "@oxitest.mark.timeout(10)\n"
        "class TestTimed:\n"
        "    def test_a(self): pass\n",
        name="test_class_timeout.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 1, (
        f"the class method must be collected so timeout propagation can be verified:"
        f" {len(items)}"
    )
    assert "timeout" in items[0].markers, (
        f"class-level timeout must propagate to methods -- without it, methods run"
        f" without time limits and can hang indefinitely: {items[0].markers}"
    )


def test_propagate_class_marks_copies_custom_mark(tmp: TempDir) -> None:
    """Class-level custom mark propagates to methods."""
    path = helpers.write_test_module(
        tmp,
        "import oxitest\n"
        "\n"
        "@oxitest.mark.slow\n"
        "class TestSlow:\n"
        "    def test_a(self): pass\n",
        name="test_class_custom.py",
    )
    items, _ = collect_module(path)
    assert len(items) == 1, (
        f"the class method must be collected so custom mark propagation can be"
        f" verified: {len(items)}"
    )
    assert "slow" in items[0].markers, (
        f"class-level custom marks must propagate to methods -- without it, -m slow"
        f" filtering excludes these methods from slow-only runs: {items[0].markers}"
    )


# ── oxi_mark skip(when=False) no-op tests ──────────────────────────────────


def test_module_mark_skip_when_false_no_violation(tmp: TempDir) -> None:
    """oxi_mark = mark.skip(when=False) is a no-op, not a violation."""
    path = helpers.write_test_module(
        tmp,
        "import oxitest\n"
        "\n"
        "oxi_mark = oxitest.mark.skip(when=False, reason='not skipped')\n"
        "\n"
        "def test_ok(): pass\n",
        name="test_skip_false.py",
    )
    items, violations = collect_module(path)
    assert len(items) == 1, (
        f"the test function must be collected so skip(when=False) behavior can be"
        f" verified: {len(items)}"
    )
    assert not any(v.kind == ViolationKind.INVALID_MODULE_MARK for v in violations), (
        f"skip(when=False) is a valid no-op, not an error -- flagging it as a violation"
        f" penalizes correct conditional-skip usage: {violations}"
    )
    # Test should NOT have a skip marker
    assert "skip" not in items[0].markers, (
        f"skip(when=False) must be a no-op -- applying the skip marker when the"
        f" condition is False would skip tests the author explicitly wants to run:"
        f" {items[0].markers}"
    )


def test_module_mark_skip_when_true_applies(tmp: TempDir) -> None:
    """oxi_mark = mark.skip(when=True) applies skip to all tests."""
    path = helpers.write_test_module(
        tmp,
        "import oxitest\n"
        "\n"
        "oxi_mark = oxitest.mark.skip(when=True, reason='always skip')\n"
        "\n"
        "def test_ok(): pass\n",
        name="test_skip_true.py",
    )
    items, violations = collect_module(path)
    assert len(items) == 1, (
        f"the test function must be collected so skip(when=True) behavior can be"
        f" verified: {len(items)}"
    )
    assert "skip" in items[0].markers, (
        f"skip(when=True) must apply the skip marker -- without it the runner executes"
        f" tests the author conditionally wants skipped: {items[0].markers}"
    )
    assert not any(v.kind == ViolationKind.INVALID_MODULE_MARK for v in violations), (
        f"skip(when=True) is a valid conditional skip, not an error -- flagging it as a"
        f" violation penalizes correct usage: {violations}"
    )


def test_module_mark_skip_when_false_in_list_no_violation(tmp: TempDir) -> None:
    """oxi_mark list with skip(when=False) silently skips the no-op entry."""
    path = helpers.write_test_module(
        tmp,
        "import oxitest\n"
        "\n"
        "oxi_mark = [oxitest.mark.skip(when=False), oxitest.mark.slow]\n"
        "\n"
        "def test_ok(): pass\n",
        name="test_skip_false_list.py",
    )
    items, violations = collect_module(path)
    assert len(items) == 1, (
        f"the test function must be collected so mixed oxi_mark list behavior can be"
        f" verified: {len(items)}"
    )
    assert "slow" in items[0].markers, (
        f"valid marks in the list must still be applied even when a no-op"
        f" skip(when=False) is present -- dropping them penalizes correct entries:"
        f" {items[0].markers}"
    )
    assert "skip" not in items[0].markers, (
        f"skip(when=False) is a no-op even when inside a list -- applying skip would"
        f" incorrectly skip tests the author wants to run: {items[0].markers}"
    )
    assert not any(v.kind == ViolationKind.INVALID_MODULE_MARK for v in violations), (
        f"skip(when=False) in a list is valid no-op usage -- flagging it as a violation"
        f" penalizes correct conditional-skip patterns: {violations}"
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
    """Minimal stand-in for a session object with a plugin_registry."""

    plugin_registry: _FakeRegistry = field(default_factory=_FakeRegistry)


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
                kind=Solitary(),
                param_values=(),
            )
        ]


def test_collector_error_emits_diagnostic(
    tmp: TempDir,
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """A collector that raises emits a plugin collector diagnostic."""
    path = helpers.write_test_module(
        tmp, "def test_ok(): pass\n", name="test_col_err.py"
    )
    session = _FakeSession(
        plugin_registry=_FakeRegistry(collectors=[_RaisingCollector()])
    )
    items, _ = collect_module(path, session=session)

    # The regular test item should still be collected despite the collector error
    assert len(items) == 1, (
        "a plugin collector error must not block base test collection -- one broken"
        " plugin must not prevent the entire module from being tested: "
        f"{len(items)}"
    )

    collector_diags = [d for d in diag_collector if d.context == "plugin collector"]
    assert len(collector_diags) == 1, (
        "a failing collector must emit exactly one diagnostic so users know the plugin"
        " is broken -- zero means silent failure, more than one means duplicate"
        f" reporting: {diag_collector}"
    )
    msg = collector_diags[0].message
    assert "_RaisingCollector" in msg, (
        f"the diagnostic must identify the collector class so the user knows which"
        f" plugin to fix or disable: {msg}"
    )
    assert "collector went boom" in msg, (
        f"the diagnostic must include the original error message so users can diagnose"
        f" the root cause without re-running with debug logging: {msg}"
    )


def test_non_collected_item_emits_diagnostic(
    tmp: TempDir,
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """Collector returning non-CollectedItem emits a diagnostic per bad item."""
    path = helpers.write_test_module(
        tmp, "def test_ok(): pass\n", name="test_col_bad.py"
    )
    session = _FakeSession(
        plugin_registry=_FakeRegistry(collectors=[_BadReturnCollector()])
    )
    items, _ = collect_module(path, session=session)

    # Only the base test should be collected; bad returns are dropped
    assert len(items) == 1, (
        f"non-CollectedItem returns must be silently dropped so invalid plugin output"
        f" cannot corrupt the test schedule: {len(items)}"
    )

    collector_diags = [d for d in diag_collector if d.context == "plugin collector"]
    assert len(collector_diags) == 2, (
        "each non-CollectedItem return value must produce a separate diagnostic so the"
        " plugin author can identify every invalid item: "
        f"{diag_collector}"
    )
    msg0 = collector_diags[0].message
    assert "_BadReturnCollector" in msg0, (
        f"the diagnostic must name the collector class so the plugin author knows which"
        f" collector returned invalid data: {msg0}"
    )
    assert "str" in msg0, (
        f"the diagnostic must name the unexpected return type so the plugin author"
        f" knows what to fix (expected CollectedItem, got str): {msg0}"
    )
    msg1 = collector_diags[1].message
    assert "int" in msg1, (
        f"each bad return must name its specific type so the plugin author can fix each"
        f" one individually: {msg1}"
    )


def test_good_collector_adds_items_no_diagnostics(
    tmp: TempDir,
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """A well-behaved collector adds items and emits no diagnostics."""
    path = helpers.write_test_module(
        tmp, "def test_ok(): pass\n", name="test_col_good.py"
    )
    session = _FakeSession(plugin_registry=_FakeRegistry(collectors=[_GoodCollector()]))
    items, _ = collect_module(path, session=session)

    assert len(items) == 2, (
        f"plugin-collected items must be merged with base items -- missing the plugin"
        f" item means the plugin's tests silently never run: {len(items)}"
    )
    plugin_item = [i for i in items if i.fn_name == "test_from_plugin"]
    assert len(plugin_item) == 1, (
        "the plugin-collected item must appear in the final item list so the runner"
        " schedules it -- otherwise the plugin's test contribution is silently lost: "
        f"{[i.fn_name for i in items]}"
    )

    collector_diags = [d for d in diag_collector if d.context == "plugin collector"]
    assert len(collector_diags) == 0, (
        f"a well-behaved collector returning valid CollectedItems must not trigger"
        f" diagnostics -- false diagnostics would train plugin authors to ignore real"
        f" problems: {collector_diags}"
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
    assert "_MyDB" in type_names, (
        "user-defined Fixture[T] params must appear in deps so the fixture resolver can"
        " look them up and inject the correct instance at runtime"
    )
    assert "TempDir" in type_names, (
        "builtin fixture types must appear in deps -- _get_fixture_deps must not"
        " exclude them because the resolver needs to see all fixture dependencies to"
        " build the correct injection order"
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
        f"only Fixture[T]-annotated params are fixture dependencies -- including"
        f" plain-typed params like 'int' would cause the resolver to look up"
        f" nonexistent fixtures and fail: {deps}"
    )
    assert deps[0] == ("db", "_MyDB"), (
        f"each dep must be a (qualifier, type_name) tuple so the resolver can match the"
        f" parameter name to the correct fixture definition: {deps[0]!r}"
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
    assert deps_dict.get("db") == "_MyDB", (
        f"the qualifier must map to its Fixture[T] type name so the resolver can look"
        f" up the correct fixture definition by type: {deps_dict}"
    )
    assert deps_dict.get("tmp") == "TempDir", (
        f"the qualifier must map to its Fixture[T] type name so the resolver can look"
        f" up the correct fixture definition by type: {deps_dict}"
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
        f"the return annotation is not a parameter and must be excluded -- including it"
        f" would cause the resolver to look up a phantom fixture named 'return': {deps}"
    )


# --- allow-comment gate tests for _check_module_registrars ---


def test_fixtures_without_allow_comment_blocked(tmp: TempDir) -> None:
    """Fixtures() without allow comment emits violation and does NOT register."""
    path = helpers.write_test_module(
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
        "Fixtures() without an allow comment must emit exactly one violation -- the"
        " allow-comment gate exists to prevent accidental registrar usage in test"
        " modules, and zero violations means the gate is broken: "
        f"{registrar_viols}"
    )
    assert "Fixtures" in registrar_viols[0].detail, (
        f"the violation detail must name the registrar class so the user knows which"
        f" instance triggered the violation and can decide whether to add an allow"
        f" comment: {registrar_viols[0].detail!r}"
    )
    assert registry.get("local_val") is None, (
        "fixtures must NOT be registered when the allow comment is absent --"
        " registering them anyway bypasses the safety gate and lets accidental"
        " registrars silently take effect"
    )


def test_helpers_with_allow_comment_suppressed(tmp: TempDir) -> None:
    """Helpers() with allow comment emits no violation."""
    path = helpers.write_test_module(
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
        "the allow comment is the opt-in gate for Helpers() in test modules -- emitting"
        " a violation despite the comment breaks the suppression contract and forces"
        " users to move helpers elsewhere: "
        f"{registrar_viols}"
    )
