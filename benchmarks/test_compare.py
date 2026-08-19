# benchmarks/test_compare.py
"""Tests for benchmarks/compare.py helper functions."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from oxitest import TempDir

from benchmarks.compare import (
    LAZY_RATIO_THRESHOLD,
    RegressionVerdict,
    baseline_verdict,
    check_regression,
    dogfood_summary,
    final_sentence,
    find_commands,
    lazy_summary,
    net_overhead,
    realistic_summary,
    speedup_ratio,
    tier_summary,
)


def test_check_regression_improvement() -> None:
    """A current time faster than baseline is not a regression."""
    is_reg, pct = check_regression(current=0.042, baseline=0.045, threshold=0.10)
    assert not is_reg, (
        "A run faster than its baseline must never be flagged, or the gate "
        "refuses the improvements it exists to encourage."
    )
    assert abs(pct - (-0.0667)) < 0.001, (
        "The percentage is what the report prints next to the verdict. A wrong "
        "sign or magnitude tells a reader the opposite of what was measured."
    )


def test_check_regression_at_threshold_is_not_regression() -> None:
    """A time exactly at the threshold boundary is not flagged as a regression."""
    is_reg, _ = check_regression(current=0.0495, baseline=0.045, threshold=0.10)
    assert not is_reg, (
        "The threshold is the highest passing value, not the lowest failing one. "
        "A strict comparison here makes the gate fire on ordinary timing noise."
    )


def test_check_regression_exceeds_threshold() -> None:
    """A current time more than threshold percent above baseline is a regression."""
    is_reg, pct = check_regression(current=0.052, baseline=0.045, threshold=0.10)
    assert is_reg, (
        "Detecting a slowdown past the threshold is the single job of this "
        "function. If it stays silent the whole Performance Gate is decorative."
    )
    assert abs(pct - 0.1556) < 0.001, (
        "The percentage is the evidence a reader uses to judge whether a "
        "regression is worth acting on, so it must match the measurement."
    )


def test_find_commands_filters_by_tier() -> None:
    """find_commands returns only entries matching the requested tier."""
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
    assert len(s_results) == 3, (
        "Every entry of a tier must reach that tier's summary. A short result "
        "silently drops a measurement, and the mean then describes a subset."
    )
    assert all(r["tier"] == "s" for r in s_results), (
        "An entry from another tier would be averaged into this tier's mean and "
        "move a number that nothing actually changed."
    )


def test_speedup_ratio() -> None:
    """speedup_ratio divides slow by fast to produce a times-faster multiplier."""
    ratio = speedup_ratio(fast=0.042, slow=0.180)
    assert abs(ratio - 4.286) < 0.001, (
        "The speedup multiplier is the headline number of the benchmark report. "
        "Inverting the division turns a 4x win into a 0.23x loss."
    )


def test_net_overhead_subtracts_startup() -> None:
    """net_overhead returns the difference between full run and startup cost."""
    result = net_overhead(full_mean=0.186, startup_mean=0.162)
    assert abs(result - 0.024) < 1e-9, (
        "Net overhead separates the cost of running tests from the cost of "
        "starting the interpreter, which is the only way to compare runners "
        "whose startup differs."
    )


def test_net_overhead_clamps_to_zero() -> None:
    """net_overhead clamps to 0.0 when startup exceeds full run time."""
    result = net_overhead(full_mean=0.100, startup_mean=0.150)
    assert result == 0.0, (
        "Startup can measure slower than the whole run under timing noise. "
        "Clamping at zero keeps a negative cost out of the report, where it "
        "would read as the runner giving time back."
    )


def test_tier_summary_serial_parallel() -> None:
    """tier_summary extracts serial/parallel/pytest means and computes speedups."""
    tier_results = [
        {"command": "oxitest --serial benchmarks/generated/s/oxitest/", "mean": 0.15},
        {"command": "oxitest benchmarks/generated/s/oxitest/", "mean": 0.08},
        {"command": "pytest benchmarks/generated/s/pytest/", "mean": 0.30},
    ]
    summary = tier_summary("s", tier_results)
    assert summary["tier"] == "s", (
        "The tier name labels the row in the report. A wrong label attributes a "
        "measurement to the wrong workload size."
    )
    assert abs(summary["oxitest_serial"] - 0.15) < 1e-9, (
        "Serial and parallel are told apart by the --serial flag in the command "
        "string. Matching the wrong entry swaps the two columns."
    )
    assert abs(summary["oxitest_parallel"] - 0.08) < 1e-9, (
        "The parallel mean is the denominator of the speedup below, so an entry "
        "picked from the wrong command corrupts every derived number."
    )
    assert abs(summary["pytest"] - 0.30) < 1e-9, (
        "The pytest mean is the comparison this project publishes. Reading it "
        "from an oxitest entry would compare the runner against itself."
    )
    assert abs(summary["speedup_serial"] - 2.0) < 0.01, (
        "Speedup is derived rather than measured, so it is the first thing to "
        "break silently when the extraction above picks a wrong entry."
    )
    assert abs(summary["speedup_parallel"] - 3.75) < 0.01, (
        "The parallel speedup is the number that justifies the scheduler. It "
        "must be derived from the parallel mean, not the serial one."
    )


def test_tier_summary_serial_only() -> None:
    """tier_summary sets oxitest_parallel to None when no parallel command is found."""
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
    assert summary["oxitest_serial"] is not None, (
        "A tier that ran serially must report its serial mean. Losing it would "
        "leave the tier with no oxitest measurement at all."
    )
    assert summary["oxitest_parallel"] is None, (
        "A tier below the parallel threshold never runs in parallel. It must "
        "report None so the report can tell 'not measured' from 'took no time'."
    )
    assert abs(summary["speedup_serial"] - 2.286) < 0.01, (
        "The serial speedup must still be computed when the parallel arm is "
        "absent, or a missing measurement suppresses a present one."
    )


def test_lazy_summary_under_threshold() -> None:
    """lazy_summary marks the result as not a regression when ratio is below threshold."""
    lazy_results = [
        {
            "command": "oxitest benchmarks/generated/l/oxitest/test_gen_0.py::test_0",
            "mean": 0.08,
        }
    ]
    summary = lazy_summary("lazy_node_id", lazy_results, l_parallel_mean=1.0)
    assert summary is not None, (
        "A tier with results must produce a summary. Returning None here would "
        "drop the lazy tier out of the report without any refusal."
    )
    assert summary["tier"] == "lazy_node_id", (
        "The three lazy tiers select tests by different means, so the label is "
        "what tells a reader which selection route was measured."
    )
    assert abs(summary["mean"] - 0.08) < 1e-9, (
        "The mean is the measurement; the ratio below is derived from it. An "
        "error here propagates into the regression verdict."
    )
    assert abs(summary["ratio"] - 0.08) < 1e-9, (
        "The ratio compares a single-test run against a full parallel run. It is "
        "the quantity the threshold is defined against."
    )
    assert not summary["is_regression"], (
        "A ratio far below the threshold means lazy collection is working. "
        "Flagging it would make the gate refuse correct behaviour."
    )


def test_lazy_summary_over_threshold() -> None:
    """lazy_summary flags is_regression when single-test mean exceeds LAZY_RATIO_THRESHOLD."""
    lazy_results = [
        {
            "command": "oxitest benchmarks/generated/l/oxitest/ -E 'name(test_0)'",
            "mean": 0.40,
        }
    ]
    summary = lazy_summary("lazy_name", lazy_results, l_parallel_mean=1.0)
    assert summary is not None, (
        "A tier with results must produce a summary even when that summary "
        "reports a regression, or the failure is lost rather than raised."
    )
    assert abs(summary["ratio"] - 0.40) < 1e-9, (
        "The ratio is what the threshold is compared against, so the verdict "
        "below is only as trustworthy as this number."
    )
    assert summary["is_regression"], (
        "A lazy run above the ratio threshold means lazy collection stopped "
        "being lazy, which is the whole property this tier exists to protect."
    )


def test_lazy_summary_empty_results() -> None:
    """lazy_summary returns None when no results are provided."""
    summary = lazy_summary("lazy_mark", [], l_parallel_mean=1.0)
    assert summary is None, (
        "A tier that produced no results must report None rather than a summary "
        "of nothing, which would render as a measured zero in the report."
    )


def test_lazy_summary_zero_l_parallel() -> None:
    """lazy_summary returns None when the parallel baseline mean is zero."""
    lazy_results = [{"command": "oxitest ...", "mean": 0.08}]
    summary = lazy_summary("lazy_node_id", lazy_results, l_parallel_mean=0.0)
    assert summary is None, (
        "The parallel mean is the divisor of the ratio. Refusing a zero here is "
        "what stops a ZeroDivisionError from ending the whole comparison run."
    )


def test_lazy_ratio_threshold_value() -> None:
    """LAZY_RATIO_THRESHOLD is pinned to 0.35 so accidental changes are caught."""
    assert LAZY_RATIO_THRESHOLD == 0.35, (
        "The threshold is a published contract of the lazy tier. Moving it "
        "silently re-scores every historical result against a new bar."
    )


def test_realistic_summary_full() -> None:
    """realistic_summary produces entries for serial, auto, and fixed-worker runs."""
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
    assert summary is not None, (
        "The realistic tier is the one that models a real suite. Losing its "
        "summary removes the only workload a user would recognise."
    )
    assert len(summary["entries"]) == 5, (
        "Each worker count is a separate row in the scaling table. A missing "
        "entry hides the point where adding workers stops helping."
    )
    serial_entry = summary["entries"][0]
    assert serial_entry["label"] == "serial", (
        "The entries are ordered so a reader sees the baseline first. Reordering "
        "them makes every speedup below read against the wrong row."
    )
    assert abs(serial_entry["mean"] - 9.0) < 1e-9, (
        "The serial mean is the baseline every other entry divides into, so an "
        "error here rescales the entire table."
    )
    assert serial_entry["speedup"] is None, (
        "The baseline has no speedup against itself. Reporting 1.0 would imply a "
        "measurement was taken where none exists."
    )
    auto_entry = summary["entries"][1]
    assert auto_entry["label"] == "auto", (
        "The automatic worker count is what a user gets without flags, so it "
        "must be identified separately from the fixed counts below it."
    )
    assert abs(auto_entry["speedup"] - 3.0) < 0.01, (
        "This is the speedup a user sees by default. It is the number the "
        "project quotes, so it must come from the auto entry and no other."
    )
    w4_entry = summary["entries"][4]
    assert w4_entry["label"] == "--workers 4", (
        "The fixed-worker labels carry the flag that produced them, so a reader "
        "can reproduce any row directly."
    )
    assert abs(w4_entry["speedup"] - 2.8125) < 0.01, (
        "Four workers scoring below auto is the evidence that the scheduler "
        "picks a better count than a user guessing, so the value must be exact."
    )


def test_realistic_summary_empty() -> None:
    """realistic_summary returns None when no benchmark results are provided."""
    summary = realistic_summary([])
    assert summary is None, (
        "An absent tier must produce None, not an empty summary. An empty "
        "summary renders as a measured zero in the report."
    )


def test_dogfood_summary_serial_and_parallel() -> None:
    """dogfood_summary returns serial, parallel, and speedup when both modes present."""
    tier_results = [
        {"command": "oxitest --serial python/tests/", "mean": 5.0},
        {"command": "oxitest python/tests/", "mean": 2.0},
    ]
    summary = dogfood_summary(tier_results)
    assert summary is not None, (
        "The dogfood tier measures oxitest running its own suite. Losing it "
        "removes the only benchmark taken over real, non-generated tests."
    )
    assert abs(summary["serial"] - 5.0) < 1e-9, (
        "Serial and parallel are told apart by the --serial flag alone, so "
        "matching the wrong command swaps the two values silently."
    )
    assert abs(summary["parallel"] - 2.0) < 1e-9, (
        "The parallel mean is the divisor of the speedup below, so an entry read "
        "from the wrong command corrupts the derived number too."
    )
    assert abs(summary["speedup"] - 2.5) < 0.01, (
        "This speedup is measured on a real suite rather than a generated one, "
        "which is what makes it the honest figure to publish."
    )


def test_dogfood_summary_serial_only() -> None:
    """dogfood_summary sets parallel and speedup to None when only serial is present."""
    tier_results = [
        {"command": "oxitest --serial python/tests/", "mean": 5.0},
    ]
    summary = dogfood_summary(tier_results)
    assert summary is not None, (
        "A tier with one measurement still has something to report. Returning "
        "None would discard the serial run that did happen."
    )
    assert abs(summary["serial"] - 5.0) < 1e-9, (
        "The serial mean is present and must survive, or an absent parallel arm "
        "would suppress a measurement that was actually taken."
    )
    assert summary["parallel"] is None, (
        "An absent parallel run must report None rather than zero, so the report "
        "can tell 'not run' from 'took no time'."
    )
    assert summary["speedup"] is None, (
        "A speedup needs both arms. Deriving one from a missing value would "
        "publish a ratio against nothing."
    )


def test_dogfood_summary_empty() -> None:
    """dogfood_summary returns None when no benchmark results are provided."""
    summary = dogfood_summary([])
    assert summary is None, (
        "An absent tier must produce None, not an empty summary, or the report "
        "shows a dogfood row for a run that never happened."
    )


def test_final_sentence_reports_a_regression() -> None:
    """A regression outranks every other state, so the caller sees it first."""
    sentence = final_sentence(
        has_regression=True, verdict=RegressionVerdict.NO_REGRESSION
    )
    assert sentence == "Regression detected.", (
        "A run that found a regression must say so, or CI reports a green "
        "benchmark over a real slowdown."
    )


def test_final_sentence_measured_and_found_nothing() -> None:
    """A completed comparison that found nothing keeps the original sentence."""
    sentence = final_sentence(
        has_regression=False, verdict=RegressionVerdict.NO_REGRESSION
    )
    assert sentence == "No regression detected.", (
        "The measured-clean sentence is the one a reader already knows; changing "
        "it would make every historical benchmark log ambiguous."
    )


def test_final_sentence_separates_not_measured_from_no_regression() -> None:
    """ADR-0019 forbids one sentence for 'measured nothing' and 'did not measure'."""
    measured = final_sentence(
        has_regression=False, verdict=RegressionVerdict.NO_REGRESSION
    )
    not_measured = final_sentence(
        has_regression=False, verdict=RegressionVerdict.NOT_MEASURED
    )
    assert measured != not_measured, (
        "A missing baseline printed 'No regression detected.' for months, so a "
        "silent instrument was indistinguishable from a passing one (#2166)."
    )
    assert "did not run" in not_measured, (
        "The unmeasured sentence must name what did not happen, or a reader "
        "cannot tell which of the three failure modes occurred."
    )


def test_baseline_verdict_zero_comparisons_is_not_a_pass() -> None:
    """A baseline that exists but compares nothing did not measure anything."""
    verdict = baseline_verdict(compared=0, has_regression=False)
    assert verdict is RegressionVerdict.NOT_MEASURED, (
        "A baseline sharing no tier with the run skips every comparison, so "
        "reporting NO_REGRESSION would repeat #2166 with the file present."
    )


def test_baseline_verdict_reports_a_measured_regression() -> None:
    """A comparison that ran and found a regression reports one."""
    verdict = baseline_verdict(compared=4, has_regression=True)
    assert verdict is RegressionVerdict.REGRESSION, (
        "A measured regression must reach the caller, or the workflow cannot "
        "ever refuse a slowdown."
    )


def test_baseline_verdict_reports_a_measured_pass() -> None:
    """A comparison that ran over at least one tier and found nothing passes."""
    verdict = baseline_verdict(compared=1, has_regression=False)
    assert verdict is RegressionVerdict.NO_REGRESSION, (
        "One compared tier is a real measurement, so it must not be downgraded "
        "to NOT_MEASURED alongside the zero-comparison case."
    )


def _write_bench(
    root: Path, *, results: list[dict], baseline: list[dict] | None = None
) -> None:
    """Write the two files ``compare.py`` reads, relative to ``root``."""
    bench = root / "benchmarks"
    bench.mkdir(parents=True)
    (bench / "results.json").write_text(
        json.dumps({"results": results}), encoding="utf-8"
    )
    if baseline is not None:
        (bench / "baseline.json").write_text(
            json.dumps({"results": baseline}), encoding="utf-8"
        )


def _lazy_entries(lazy_mean: float) -> list[dict]:
    """Return results reaching the lazy arm, with ``lazy_mean`` as the ratio.

    The ``l`` entry fixes the denominator at 1.0, so the caller sets the ratio
    that ``LAZY_RATIO_THRESHOLD`` is compared against directly.
    """
    return [
        {"command": "oxitest l", "tier": "l", "mean": 1.0},
        {"command": "oxitest lazy", "tier": "lazy_node_id", "mean": lazy_mean},
    ]


def _tier_entries(serial_mean: float) -> list[dict]:
    """Return results reaching the baseline arm, and no other arm.

    The lazy tiers are absent, so ``_print_lazy`` reports nothing and the
    verdict can only come from the comparison against ``baseline.json``.
    """
    return [{"command": "oxitest --serial s", "tier": "s", "mean": serial_mean}]


def _run_compare(cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run ``compare.py`` as the Performance Gate runs it, from ``cwd``."""
    script = Path(__file__).resolve().parent / "compare.py"
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def test_compare_refuses_a_lazy_regression(tmp: TempDir) -> None:
    """The detector refuses a lazy-tier regression and accepts a clean run.

    This is the ``tooling`` obligation of ADR-0019: a test with that attribute
    makes its tool fail.
    """
    # Arrange -- 0.90 is far above LAZY_RATIO_THRESHOLD, 0.10 far below it.
    regressed_root = tmp.path / "regressed"
    clean_root = tmp.path / "clean"
    _write_bench(regressed_root, results=_lazy_entries(0.90))
    _write_bench(clean_root, results=_lazy_entries(0.10))

    # Act
    regressed = _run_compare(regressed_root)
    clean = _run_compare(clean_root)

    # Assert
    assert regressed.returncode == 1, (
        "compare.py is the only refusal the Performance Gate has. If it exits 0 "
        "on a regression the gate reports a pass it never measured. "
        f"stdout was:\n{regressed.stdout}"
    )
    assert clean.returncode == 0, (
        "A detector that always exits non-zero would satisfy the assertion above "
        "while detecting nothing. This control is what separates the two. "
        f"stdout was:\n{clean.stdout}"
    )


def test_compare_refuses_a_baseline_regression(tmp: TempDir) -> None:
    """The detector refuses a tier that got slower than its recorded baseline.

    The lazy arm and the baseline arm are the only two that reach the exit
    code. This pins the second one, so a change that breaks only the baseline
    comparison cannot pass by way of the lazy arm.
    """
    # Arrange -- 0.20 against a 0.10 baseline is 100% slower, far past the 10%
    # threshold. 0.10 against 0.10 has not moved at all.
    regressed_root = tmp.path / "regressed"
    clean_root = tmp.path / "clean"
    baseline = _tier_entries(0.10)
    _write_bench(regressed_root, results=_tier_entries(0.20), baseline=baseline)
    _write_bench(clean_root, results=_tier_entries(0.10), baseline=baseline)

    # Act
    regressed = _run_compare(regressed_root)
    clean = _run_compare(clean_root)

    # Assert
    assert regressed.returncode == 1, (
        "A tier slower than its baseline is the regression the Performance Gate "
        "was built to catch, and it reaches the exit code by a different route "
        f"than the lazy arm. stdout was:\n{regressed.stdout}"
    )
    assert clean.returncode == 0, (
        "An unchanged tier must compare and pass. If this exits non-zero the "
        "baseline arm refuses every run, and the gate blocks all work. "
        f"stdout was:\n{clean.stdout}"
    )
