"""Collection-time behavior of @oxi.arrange."""

import oxitest
from oxitest import StdCapture, TempDir, helpers
from oxitest._bridge._errors import CollectionError
from oxitest._bridge.importer import collect_module
from oxitest._bridge.result import CollectedItem


def test_collected_item_defaults_arranged_to_empty_tuple() -> None:
    """CollectedItem.arranged must default to () so undecorated tests need no init.

    Undecorated tests (the majority) get an empty tuple; @oxi.arrange populates
    it during collection (Task 7).
    """
    item = CollectedItem(
        fn_name="test_x",
        lineno=1,
        markers=(),
        param_id=None,
        param_values=(),
    )
    assert item.arranged == (), (
        "arranged must default to () — Task 7 populates it from"
        " FunctionMetadata.arranged"
    )


def test_importer_populates_arranged_from_decorator(tmp: TempDir) -> None:
    """collect_module must copy FunctionMetadata.arranged into CollectedItem.arranged.

    Without this, the executor's arrange phase (Task 12) has no data to iterate.
    """
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "from oxitest import TempDir\n"
        "\n"
        "@oxitest.arrange(TempDir)\n"
        "def test_x() -> None: ...\n",
    )
    items, _ = collect_module(path)
    assert len(items) == 1, f"expected exactly 1 collected item, got {len(items)}"
    assert items[0].arranged == (TempDir,), (
        "importer must copy FunctionMetadata.arranged into CollectedItem.arranged — "
        "the executor arrange phase (Task 12) reads this field"
    )


def test_importer_dedupes_stacked_arrange(tmp: TempDir) -> None:
    """Stacked @arrange decorators with the same fixture must dedupe to one entry.

    Preserves first-occurrence order via a stable dedupe.
    """
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "from oxitest import TempDir\n"
        "\n"
        "@oxitest.arrange(TempDir)\n"
        "@oxitest.arrange(TempDir)\n"
        "def test_x() -> None: ...\n",
    )
    items, _ = collect_module(path)
    assert len(items) == 1, f"expected exactly 1 collected item, got {len(items)}"
    assert items[0].arranged == (TempDir,), (
        "importer must dedupe identical arranged entries — stacked @arrange with "
        "the same fixture is idempotent, not a duplication bug"
    )


def test_importer_errors_on_type_collision(tmp: TempDir) -> None:
    """@arrange(TempDir) + param `foo: TempDir` — both would produce TempDir, error.

    Redundant declaration + potential double-instantiation risk. Fail loud at
    collection so user picks one form.
    """
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "from oxitest import TempDir\n"
        "\n"
        "@oxitest.arrange(TempDir)\n"
        "def test_x(foo: TempDir) -> None: ...\n",
    )
    with oxitest.raises(
        CollectionError,
        match=r"arranged .*TempDir.* also declared as parameter",
    ):
        collect_module(path)


def test_importer_errors_on_name_collision(tmp: TempDir) -> None:
    """@arrange("foo") + param named `foo` — same fixture requested twice.

    Named conftest fixture arranged AND injected as parameter — pick one.
    """
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "from oxitest import Fixture\n"
        "\n"
        "@oxitest.arrange('foo')\n"
        "def test_x(foo: Fixture[int]) -> None: ...\n",
    )
    with oxitest.raises(
        CollectionError,
        match=r"arranged 'foo' also declared as parameter",
    ):
        collect_module(path)


def test_arranged_fixtures_appear_in_fixture_deps(tmp: TempDir) -> None:
    """Arranged fixtures must be added to CollectedItem.fixture_deps at collection time.

    Without this, the Rust bridge's find_unused_fixtures builds its fixture_names
    dict solely from fixture_deps qualifiers — arranged fixtures would be invisible
    to FixtureValidator.find_unused_fixtures(), causing strict mode to flag them
    as unused and abort the run.
    """
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "from oxitest import TempDir\n"
        "\n"
        "@oxitest.arrange(TempDir, 'db')\n"
        "def test_x() -> None: ...\n",
    )
    items, _ = collect_module(path)
    assert len(items) == 1, f"expected exactly 1 collected item, got {len(items)}"
    qualifier_names = {q for q, _ in items[0].fixture_deps}
    assert "TempDir" in qualifier_names, (
        "arranged type TempDir must appear in fixture_deps as qualifier 'TempDir' so "
        "the Rust bridge includes it in fixture_names for the unused-fixture check"
    )
    assert "db" in qualifier_names, (
        "arranged string 'db' must appear in fixture_deps as qualifier 'db' so "
        "the Rust bridge includes it in fixture_names for the unused-fixture check"
    )


def test_class_arrange_propagates_to_methods(tmp: TempDir) -> None:
    """@oxi.arrange on a class propagates to every test_* method.

    Users write class-level arrange to share fixture side effects across
    multiple test methods without repeating the decorator on each.
    """
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "from oxitest import TempDir\n"
        "\n"
        "@oxitest.arrange(TempDir)\n"
        "class TestGroup:\n"
        "    def test_a(self) -> None: ...\n"
        "    def test_b(self) -> None: ...\n",
    )
    items, _ = collect_module(path)
    assert len(items) == 2, (
        f"expected exactly 2 collected items (test_a, test_b), got {len(items)}"
    )
    for item in items:
        assert item.arranged == (TempDir,), (
            f"class-level @arrange must propagate to {item.fn_name} — "
            "without propagation, TestGroup's shared side effects are lost"
        )


def test_class_and_method_arrange_merge_class_first(tmp: TempDir) -> None:
    """Class-level + method-level @arrange combine, class entries first.

    Method adds to (not replaces) class-level arrangement; the merged tuple
    preserves declaration semantics.
    """
    path = helpers.common.write_test_module(
        tmp,
        "import oxitest\n"
        "from oxitest import TempDir, StdCapture\n"
        "\n"
        "@oxitest.arrange(TempDir)\n"
        "class TestGroup:\n"
        "    @oxitest.arrange(StdCapture)\n"
        "    def test_x(self) -> None: ...\n",
    )
    items, _ = collect_module(path)
    assert len(items) == 1, f"expected exactly 1 collected item, got {len(items)}"
    assert items[0].arranged == (TempDir, StdCapture), (
        "class-level TempDir must come first, then method-level StdCapture — "
        "class-level = shared baseline, method-level = per-test additions"
    )
