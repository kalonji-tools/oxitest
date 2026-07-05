"""Cross-language sync test: Rust BUILTIN_MARKERS >= Python _MARK_REGISTRY keys."""

from __future__ import annotations

from oxitest._bridge._mark_registry import _BUILTIN_HANDLER_NAMES
from oxitest._oxitest import builtin_markers


def test_python_markers_are_subset_of_rust() -> None:
    """Every Python builtin marker handler must appear in the Rust constant."""
    rust_markers = set(builtin_markers())
    python_markers = _BUILTIN_HANDLER_NAMES
    missing_in_rust = python_markers - rust_markers
    assert not missing_in_rust, (
        f"Python defines handlers not in Rust BUILTIN_MARKERS: {missing_in_rust}"
    )


def test_no_unexpected_rust_only_markers() -> None:
    """Document which Rust markers have no Python handler.

    'inprocess' is Rust-only (no-op sentinel).
    'usefixtures' is handled inline in evaluate_marks, not via a registry handler.
    """
    rust_markers = set(builtin_markers())
    python_markers = _BUILTIN_HANDLER_NAMES
    rust_only = rust_markers - python_markers
    expected_rust_only = {"inprocess", "usefixtures"}
    assert rust_only == expected_rust_only, (
        f"Unexpected Rust-only markers: {rust_only - expected_rust_only}"
    )
