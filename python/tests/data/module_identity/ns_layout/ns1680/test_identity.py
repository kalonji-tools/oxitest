from ns1680 import probe


def test_the_caller_module_name_is_the_real_one() -> None:
    """A namespace-package layout must get the same identity as a package."""
    assert probe.caller_module_name() == "ns1680.test_identity", (
        "PEP 420 namespace packages support this too, so a fix keyed on "
        "__init__.py would leave this layout unfixed"
    )
