"""Tested examples for the use-raises-warns how-to guide."""

import oxitest


def validate_age(age: int) -> None:
    """Stub for docs — raises ValueError on negative input."""
    if age < 0:
        msg = "must be positive"
        raise ValueError(msg)


# fmt: off
# --8<-- [start:raises-basic]
def test_divide_by_zero():
    with oxitest.raises(ZeroDivisionError):
        result = 1 / 0  # noqa: F841
# --8<-- [end:raises-basic]

# --8<-- [start:raises-match]
def test_invalid_input():
    with oxitest.raises(ValueError, match="must be positive"):
        validate_age(-1)
# --8<-- [end:raises-match]

# --8<-- [start:raises-excinfo]
def test_error_code():
    with oxitest.raises(KeyError) as exc_info:
        d = {}
        _ = d["missing"]
    assert exc_info.value.args[0] == "missing", "KeyError should carry the missing key"
# --8<-- [end:raises-excinfo]

# --8<-- [start:raises-tuple]
def test_any_io_error():
    with oxitest.raises((OSError, IOError)):
        open("/nonexistent/path")  # noqa: SIM115, PTH123
# --8<-- [end:raises-tuple]
# fmt: on

import warnings  # noqa: E402


# fmt: off
# --8<-- [start:warns-basic]
def test_deprecation_warning():
    with oxitest.warns(DeprecationWarning):
        warnings.warn("old_function is deprecated", DeprecationWarning, stacklevel=1)
# --8<-- [end:warns-basic]

# --8<-- [start:warns-match]
def test_warning_message():
    with oxitest.warns(UserWarning, match="disk space"):
        warnings.warn("low disk space", UserWarning, stacklevel=1)
# --8<-- [end:warns-match]

# --8<-- [start:importorskip-basic]
def test_with_pandas():
    pd = oxitest.importorskip("pandas")
    df = pd.DataFrame({"a": [1, 2, 3]})
    assert len(df) == 3, "DataFrame should have 3 rows"
# --8<-- [end:importorskip-basic]

# --8<-- [start:importorskip-reason]
def test_importorskip_with_reason():
    pd = oxitest.importorskip("pandas", reason="pandas required for CSV tests")
    assert pd is not None, "importorskip should return the module"
# --8<-- [end:importorskip-reason]
# fmt: on
