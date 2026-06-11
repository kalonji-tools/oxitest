# benchmarks/test_compare.py
"""Tests for benchmarks/compare.py helper functions."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarks.compare import (
    LAZY_RATIO_THRESHOLD,
    check_regression,
    dogfood_summary,
    find_commands,
    lazy_summary,
    net_overhead,
    realistic_summary,
    speedup_ratio,
    tier_summary,
)


def test_check_regression_improvement() -> None:
    is_reg, pct = check_regression(current=0.042, baseline=0.045, threshold=0.10)
    assert not is_reg, ""
    assert abs(pct - (-0.0667)) < 0.001, ""


def test_check_regression_at_threshold_is_not_regression() -> None:
    is_reg, _ = check_regression(current=0.0495, baseline=0.045, threshold=0.10)
    assert not is_reg, ""


def test_check_regression_exceeds_threshold() -> None:
    is_reg, pct = check_regression(current=0.052, baseline=0.045, threshold=0.10)
    assert is_reg, ""
    assert abs(pct - 0.1556) < 0.001, ""


def test_find_commands_filters_by_tier() -> None:
    results = [
        {
            "command": "oxitest --serial benchmarks/generated/s/oxitest/",
            "mean": 0.1,
            "tier": "s",
        },
        {
            "command": "oxitest benchmarks/generated/s/oxitest/",
            "mean": 0.08,
            "tier": "s",
        },
        {"command": "pytest benchmarks/generated/s/pytest/", "mean": 0.2, "tier": "s"},
        {
            "command": "oxitest --serial benchmarks/generated/m/oxitest/",
            "mean": 0.5,
            "tier": "m",
        },
    ]
    s_results = find_commands(results, tier="s")
    assert len(s_results) == 3, ""
    assert all(r["tier"] == "s" for r in s_results), ""


def test_speedup_ratio() -> None:
    ratio = speedup_ratio(fast=0.042, slow=0.180)
    assert abs(ratio - 4.286) < 0.001, ""


def test_net_overhead_subtracts_startup() -> None:
    result = net_overhead(full_mean=0.186, startup_mean=0.162)
    assert abs(result - 0.024) < 1e-9, ""


def test_net_overhead_clamps_to_zero() -> None:
    result = net_overhead(full_mean=0.100, startup_mean=0.150)
    assert result == 0.0, ""


def test_tier_summary_serial_parallel() -> None:
    tier_results = [
        {"command": "oxitest --serial benchmarks/generated/s/oxitest/", "mean": 0.15},
        {"command": "oxitest benchmarks/generated/s/oxitest/", "mean": 0.08},
        {"command": "pytest benchmarks/generated/s/pytest/", "mean": 0.30},
    ]
    summary = tier_summary("s", tier_results)
    assert summary["tier"] == "s", ""
    assert abs(summary["oxitest_serial"] - 0.15) < 1e-9, ""
    assert abs(summary["oxitest_parallel"] - 0.08) < 1e-9, ""
    assert abs(summary["pytest"] - 0.30) < 1e-9, ""
    assert abs(summary["speedup_serial"] - 2.0) < 0.01, ""
    assert abs(summary["speedup_parallel"] - 3.75) < 0.01, ""


def test_tier_summary_serial_only() -> None:
    tier_results = [
        {
            "command": "oxitest --serial benchmarks/generated/below_threshold/oxitest/",
            "mean": 0.07,
        },
        {
            "command": "pytest benchmarks/generated/below_threshold/pytest/",
            "mean": 0.16,
        },
    ]
    summary = tier_summary("below_threshold", tier_results)
    assert summary["oxitest_serial"] is not None, ""
    assert summary["oxitest_parallel"] is None, ""
    assert abs(summary["speedup_serial"] - 2.286) < 0.01, ""


def test_lazy_summary_under_threshold() -> None:
    lazy_results = [
        {
            "command": "oxitest benchmarks/generated/l/oxitest/test_gen_0.py::test_trivial_0",
            "mean": 0.08,
        }
    ]
    summary = lazy_summary("lazy_node_id", lazy_results, l_parallel_mean=1.0)
    assert summary is not None, ""
    assert summary["tier"] == "lazy_node_id", ""
    assert abs(summary["mean"] - 0.08) < 1e-9, ""
    assert abs(summary["ratio"] - 0.08) < 1e-9, ""
    assert not summary["is_regression"], ""


def test_lazy_summary_over_threshold() -> None:
    lazy_results = [
        {
            "command": "oxitest benchmarks/generated/l/oxitest/ -E 'name(test_trivial_0)'",
            "mean": 0.40,
        }
    ]
    summary = lazy_summary("lazy_name", lazy_results, l_parallel_mean=1.0)
    assert summary is not None, ""
    assert abs(summary["ratio"] - 0.40) < 1e-9, ""
    assert summary["is_regression"], ""


def test_lazy_summary_empty_results() -> None:
    summary = lazy_summary("lazy_mark", [], l_parallel_mean=1.0)
    assert summary is None, ""


def test_lazy_summary_zero_l_parallel() -> None:
    lazy_results = [{"command": "oxitest ...", "mean": 0.08}]
    summary = lazy_summary("lazy_node_id", lazy_results, l_parallel_mean=0.0)
    assert summary is None, ""


def test_lazy_ratio_threshold_value() -> None:
    assert LAZY_RATIO_THRESHOLD == 0.35, ""


def test_realistic_summary_full() -> None:
    tier_results = [
        {
            "command": "oxitest --serial benchmarks/generated/realistic/oxitest/",
            "mean": 9.0,
        },
        {"command": "oxitest benchmarks/generated/realistic/oxitest/", "mean": 3.0},
        {
            "command": "oxitest --workers 1 benchmarks/generated/realistic/oxitest/",
            "mean": 9.5,
        },
        {
            "command": "oxitest --workers 2 benchmarks/generated/realistic/oxitest/",
            "mean": 5.0,
        },
        {
            "command": "oxitest --workers 4 benchmarks/generated/realistic/oxitest/",
            "mean": 3.2,
        },
    ]
    summary = realistic_summary(tier_results)
    assert summary is not None, ""
    assert len(summary["entries"]) == 5, ""
    serial_entry = summary["entries"][0]
    assert serial_entry["label"] == "serial", ""
    assert abs(serial_entry["mean"] - 9.0) < 1e-9, ""
    assert serial_entry["speedup"] is None, ""
    auto_entry = summary["entries"][1]
    assert auto_entry["label"] == "auto", ""
    assert abs(auto_entry["speedup"] - 3.0) < 0.01, ""
    w4_entry = summary["entries"][4]
    assert w4_entry["label"] == "--workers 4", ""
    assert abs(w4_entry["speedup"] - 2.8125) < 0.01, ""


def test_realistic_summary_empty() -> None:
    summary = realistic_summary([])
    assert summary is None, ""


def test_dogfood_summary_serial_and_parallel() -> None:
    tier_results = [
        {"command": "oxitest --serial python/tests/", "mean": 5.0},
        {"command": "oxitest python/tests/", "mean": 2.0},
    ]
    summary = dogfood_summary(tier_results)
    assert summary is not None, ""
    assert abs(summary["serial"] - 5.0) < 1e-9, ""
    assert abs(summary["parallel"] - 2.0) < 1e-9, ""
    assert abs(summary["speedup"] - 2.5) < 0.01, ""


def test_dogfood_summary_serial_only() -> None:
    tier_results = [
        {"command": "oxitest --serial python/tests/", "mean": 5.0},
    ]
    summary = dogfood_summary(tier_results)
    assert summary is not None, ""
    assert abs(summary["serial"] - 5.0) < 1e-9, ""
    assert summary["parallel"] is None, ""
    assert summary["speedup"] is None, ""


def test_dogfood_summary_empty() -> None:
    summary = dogfood_summary([])
    assert summary is None, ""
