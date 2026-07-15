"""Collection-time behavior of @oxi.arrange."""

from oxitest import TempDir, helpers
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
