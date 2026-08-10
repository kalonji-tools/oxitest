from pkg1680 import probe


def test_the_caller_module_name_is_the_real_one() -> None:
    """A library reading the caller's __name__ must see the real dotted name."""
    assert probe.caller_module_name() == "pkg1680.test_identity", (
        "a library that silences or filters by caller module cannot match a "
        "synthetic _oxitest_collect_<digest> name"
    )
