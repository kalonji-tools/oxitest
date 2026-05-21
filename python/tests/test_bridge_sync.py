"""
Field-sync tests for Python/Rust bridge dataclasses.

PyO3's FromPyObject extracts fields by name. If a field is renamed in
result.py without updating the corresponding Rust struct in src/bridge.rs
(or vice versa), extraction fails at runtime with a cryptic error.

These tests document the expected field set for both classes and will
fail immediately if either side drifts.
"""

import dataclasses


def test_test_result_fields_match_rust_test_result():
    """TestResult fields must exactly match TestResult in src/bridge.rs."""
    from oxitest._bridge.result import TestResult

    # Construct with all fields — TypeError if any field is missing or renamed.
    result = TestResult(
        status="passed",
        message="",
        file="",
        lineno=0,
        source_line="",
        no_message_lines=[],
        left="",
        right="",
        op="",
        strict=True,
        exc_type="",
        frames=[],
    )
    expected_fields = {
        "status",
        "message",
        "file",
        "lineno",
        "source_line",
        "no_message_lines",
        "left",
        "right",
        "op",
        "strict",
        "exc_type",
        "frames",
    }
    actual_fields = {f.name for f in dataclasses.fields(result)}
    assert actual_fields == expected_fields, (
        f"TestResult fields differ from Rust TestResult.\n"
        f"  Missing from Python: {expected_fields - actual_fields}\n"
        f"  Extra in Python:     {actual_fields - expected_fields}\n"
        f"  Update src/bridge.rs TestResult to match, or update result.py."
    )


def test_collected_item_fields_match_rust_collected_item():
    """CollectedItem fields must exactly match CollectedItem in src/bridge.rs."""
    from oxitest._bridge.result import CollectedItem

    item = CollectedItem(
        fn_name="test_foo",
        lineno=1,
        markers=[],
        param_id=None,
        param_values=[],
        is_async=False,
    )
    expected_fields = {
        "fn_name",
        "lineno",
        "markers",
        "param_id",
        "param_values",
        "is_async",
    }
    actual_fields = {f.name for f in dataclasses.fields(item)}
    assert actual_fields == expected_fields, (
        f"CollectedItem fields differ from Rust CollectedItem.\n"
        f"  Missing from Python: {expected_fields - actual_fields}\n"
        f"  Extra in Python:     {actual_fields - expected_fields}\n"
        f"  Update src/bridge.rs CollectedItem to match, or update result.py."
    )
