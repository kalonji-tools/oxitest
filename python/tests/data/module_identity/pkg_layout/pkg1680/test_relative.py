from .helper import HELPER_VALUE


def test_a_relative_import_resolves() -> None:
    """The module body's relative import must resolve at collection time."""
    assert HELPER_VALUE == "reached-through-a-relative-import", (
        "collection fails outright when __package__ is empty, so reaching "
        "this assertion at all is the result under test"
    )
