"""Collection-time behavior of @oxi.arrange."""

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
