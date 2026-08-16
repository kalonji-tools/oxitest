"""Regression checker for multi-tier bench results."""

from __future__ import annotations

import json
import sys
from enum import StrEnum, auto
from pathlib import Path

REGRESSION_THRESHOLD = 0.10  # 10%
LAZY_RATIO_THRESHOLD = 0.35  # lazy run must stay under 35% of full L parallel


def load_results(path: str) -> list[dict]:
    """Return list of result dicts from a hyperfine JSON export."""
    data = json.loads(Path(path).read_text())
    return data["results"]


def find_commands(results: list[dict], *, tier: str) -> list[dict]:
    """Return all result entries matching the given tier tag."""
    return [r for r in results if r.get("tier") == tier]


def find_command_mean(results: list[dict], substring: str) -> float | None:
    """Return the mean for the first command containing substring, or None."""
    for r in results:
        if substring in r["command"]:
            return r["mean"]
    return None


def check_regression(
    current: float, baseline: float, threshold: float
) -> tuple[bool, float]:
    """Return (is_regression, pct_change). Regression when pct_change > threshold."""
    pct = (current - baseline) / baseline
    return round(pct, 10) > threshold, pct


def speedup_ratio(fast: float, slow: float) -> float:
    """Return how many times faster `fast` is compared to `slow`."""
    return slow / fast


def net_overhead(full_mean: float, startup_mean: float) -> float:
    """Return net runner overhead: full run minus startup cost (minimum 0)."""
    return max(0.0, full_mean - startup_mean)


def tier_summary(tier: str, tier_results: list[dict]) -> dict:
    """Build a summary dict for a single tier's results."""
    ox_serial = find_command_mean(tier_results, "oxitest --serial")
    ox_parallel = None
    for r in tier_results:
        if (
            "oxitest" in r["command"]
            and "--serial" not in r["command"]
            and "pytest" not in r["command"]
        ):
            ox_parallel = r["mean"]
            break
    py = find_command_mean(tier_results, "pytest")

    speedup_serial = speedup_ratio(ox_serial, py) if ox_serial and py else None
    speedup_parallel = speedup_ratio(ox_parallel, py) if ox_parallel and py else None

    return {
        "tier": tier,
        "oxitest_serial": ox_serial,
        "oxitest_parallel": ox_parallel,
        "pytest": py,
        "speedup_serial": speedup_serial,
        "speedup_parallel": speedup_parallel,
    }


def lazy_summary(
    tier: str, lazy_results: list[dict], *, l_parallel_mean: float
) -> dict | None:
    """Build a summary for a lazy-collection tier, compared against L parallel."""
    if not lazy_results or l_parallel_mean <= 0:
        return None
    mean = lazy_results[0]["mean"]
    ratio = mean / l_parallel_mean
    return {
        "tier": tier,
        "mean": mean,
        "ratio": ratio,
        "is_regression": ratio > LAZY_RATIO_THRESHOLD,
    }


def realistic_summary(tier_results: list[dict]) -> dict | None:
    """Build a summary for the realistic tier with worker sweep."""
    if not tier_results:
        return None
    serial_mean = find_command_mean(tier_results, "oxitest --serial")
    if serial_mean is None:
        return None
    entries: list[dict] = []
    for r in tier_results:
        cmd = r["command"]
        mean = r["mean"]
        if "--serial" in cmd:
            label = "serial"
            speedup = None
        elif "--workers" in cmd:
            # Extract "--workers N" from command
            parts = cmd.split()
            wi = parts.index("--workers")
            label = f"--workers {parts[wi + 1]}"
            speedup = speedup_ratio(mean, serial_mean)
        else:
            label = "auto"
            speedup = speedup_ratio(mean, serial_mean)
        entries.append({"label": label, "mean": mean, "speedup": speedup})
    return {"entries": entries}


def dogfood_summary(tier_results: list[dict]) -> dict | None:
    """Build a summary for the dogfood tier (oxitest's own test suite)."""
    if not tier_results:
        return None
    serial = find_command_mean(tier_results, "oxitest --serial")
    parallel = None
    for r in tier_results:
        if "oxitest" in r["command"] and "--serial" not in r["command"]:
            parallel = r["mean"]
            break
    speedup = speedup_ratio(parallel, serial) if parallel and serial else None
    return {"serial": serial, "parallel": parallel, "speedup": speedup}


def _print_startup(results: list[dict]) -> None:
    startup = find_commands(results, tier="startup")
    ox_startup = find_command_mean(startup, "oxitest")
    py_startup = find_command_mean(startup, "pytest")
    print("STARTUP")
    if ox_startup and py_startup:
        print(f"  oxitest: {ox_startup * 1000:.0f}ms")
        print(f"  pytest:  {py_startup * 1000:.0f}ms")
        print(f"  speedup: {speedup_ratio(ox_startup, py_startup):.2f}x")
    print()


def _print_tier_cache(results: list[dict], tier: str, warm_mean: float | None) -> None:
    cold_results = find_commands(results, tier=f"{tier}_cold")
    if not cold_results:
        return
    ox_cold = find_command_mean(cold_results, "oxitest")
    if ox_cold and warm_mean:
        print(f"  cache cold: {ox_cold * 1000:.0f}ms  warm: {warm_mean * 1000:.0f}ms")


def _print_tiers(results: list[dict]) -> bool:
    tiers = ["below_threshold", "s", "m", "l"]
    has_regression = False
    for tier in tiers:
        tier_results = find_commands(results, tier=tier)
        if not tier_results:
            continue
        s = tier_summary(tier, tier_results)
        print(f"TIER: {tier}")
        if s["oxitest_serial"]:
            print(f"  oxitest serial:   {s['oxitest_serial'] * 1000:.0f}ms")
        if s["oxitest_parallel"]:
            print(f"  oxitest parallel: {s['oxitest_parallel'] * 1000:.0f}ms")
        if s["pytest"]:
            print(f"  pytest:           {s['pytest'] * 1000:.0f}ms")
        if s["speedup_serial"]:
            print(f"  speedup (serial):   {s['speedup_serial']:.2f}x")
        if s["speedup_parallel"]:
            print(f"  speedup (parallel): {s['speedup_parallel']:.2f}x")
        if s["oxitest_serial"] and s["oxitest_parallel"]:
            par_gain = speedup_ratio(s["oxitest_parallel"], s["oxitest_serial"])
            print(f"  parallel gain:      {par_gain:.2f}x over serial")
        _print_tier_cache(results, tier, s["oxitest_parallel"])
        print()
    return has_regression


def _print_lazy(results: list[dict]) -> bool:
    l_tier_results = find_commands(results, tier="l")
    l_parallel = None
    for r in l_tier_results:
        if (
            "oxitest" in r["command"]
            and "--serial" not in r["command"]
            and "pytest" not in r["command"]
        ):
            l_parallel = r["mean"]
            break
    lazy_tiers = ["lazy_node_id", "lazy_name", "lazy_mark"]
    lazy_labels = {
        "lazy_node_id": "node ID",
        "lazy_name": "name()",
        "lazy_mark": "mark()",
    }
    has_regression = False
    lazy_printed = False
    for lt in lazy_tiers:
        lr = find_commands(results, tier=lt)
        if not lr or l_parallel is None:
            continue
        s = lazy_summary(lt, lr, l_parallel_mean=l_parallel)
        if s is None:
            continue
        if not lazy_printed:
            print("LAZY COLLECTION")
            lazy_printed = True
        marker = "REGRESSION" if s["is_regression"] else "ok"
        label = lazy_labels.get(lt, lt)
        print(
            f"  {label}: {s['mean'] * 1000:.0f}ms  (ratio: {s['ratio']:.2f}) {marker}"
        )
        if s["is_regression"]:
            has_regression = True
    if lazy_printed:
        print()
    return has_regression


def _print_realistic(results: list[dict]) -> None:
    realistic_results = find_commands(results, tier="realistic")
    rs = realistic_summary(realistic_results)
    if rs is not None:
        print("REALISTIC")
        for entry in rs["entries"]:
            speedup_str = (
                f"  ({entry['speedup']:.2f}x vs serial)" if entry["speedup"] else ""
            )
            print(f"  {entry['label']:>12}: {entry['mean'] * 1000:.0f}ms{speedup_str}")
        print()


def _print_dogfood(results: list[dict]) -> None:
    dogfood_results = find_commands(results, tier="dogfood")
    ds = dogfood_summary(dogfood_results)
    if ds is not None:
        print("DOGFOOD")
        if ds["serial"]:
            print(f"  serial:   {ds['serial'] * 1000:.0f}ms")
        if ds["parallel"]:
            print(f"  parallel: {ds['parallel'] * 1000:.0f}ms")
        if ds["speedup"]:
            print(f"  speedup:  {ds['speedup']:.2f}x")
        print()


class RegressionVerdict(StrEnum):
    """The three results the baseline comparison can produce.

    NOT_MEASURED and NO_REGRESSION are different results. ADR-0019 requires an
    instrument to print a different sentence for each, because a missing
    baseline, a failed download and a job timeout all used to print
    "No regression detected." (#2166).
    """

    NOT_MEASURED = auto()
    NO_REGRESSION = auto()
    REGRESSION = auto()


def baseline_verdict(*, compared: int, has_regression: bool) -> RegressionVerdict:
    """Return the verdict for a baseline comparison that ran over `compared` tiers.

    A baseline file can exist and still compare nothing: every tier is skipped
    when either side lacks it. That path used to reach the same sentence as a
    clean comparison, which is the defect this module is being repaired for
    (#2166). Zero comparisons is not a pass.
    """
    if compared == 0:
        return RegressionVerdict.NOT_MEASURED
    if has_regression:
        return RegressionVerdict.REGRESSION
    return RegressionVerdict.NO_REGRESSION


def final_sentence(*, has_regression: bool, verdict: RegressionVerdict) -> str:
    """Return the one-line outcome the run reports.

    Pure, so the three outcomes are testable without a baseline file on disk and
    without capturing stdout.
    """
    if has_regression:
        return "Regression detected."
    if verdict is RegressionVerdict.NOT_MEASURED:
        return (
            "No regression detected in this run. "
            "The comparison against a baseline did not run."
        )
    return "No regression detected."


def _check_regressions(results: list[dict]) -> RegressionVerdict:
    baseline_path = Path("benchmarks/baseline.json")
    print("REGRESSION CHECK")
    if not baseline_path.exists():
        print("  NOT MEASURED - benchmarks/baseline.json is absent")
        print()
        return RegressionVerdict.NOT_MEASURED
    baseline = load_results(str(baseline_path))
    tiers = ["below_threshold", "s", "m", "l"]
    has_regression = False
    compared = 0
    for tier in tiers:
        tier_results = find_commands(results, tier=tier)
        base_results = find_commands(baseline, tier=tier)
        if not tier_results or not base_results:
            continue
        ox_current = find_command_mean(
            tier_results, "oxitest --serial"
        ) or find_command_mean(tier_results, "oxitest")
        ox_baseline = find_command_mean(
            base_results, "oxitest --serial"
        ) or find_command_mean(base_results, "oxitest")
        if ox_current and ox_baseline:
            compared += 1
            is_reg, pct = check_regression(
                ox_current, ox_baseline, REGRESSION_THRESHOLD
            )
            marker = "REGRESSION" if is_reg else "ok"
            print(
                f"  {tier}: {ox_current * 1000:.0f}ms"
                f" (baseline: {ox_baseline * 1000:.0f}ms) {pct:+.1%} {marker}"
            )
            if is_reg:
                has_regression = True
    if compared == 0:
        print("  NOT MEASURED - the baseline shares no tier with this run")
    print()
    return baseline_verdict(compared=compared, has_regression=has_regression)


def main() -> int:
    """Print all benchmark summaries and return 1 if any regression is found."""
    results = load_results("benchmarks/results.json")
    _print_startup(results)
    has_regression = _print_tiers(results)
    if _print_lazy(results):
        has_regression = True
    _print_realistic(results)
    _print_dogfood(results)
    verdict = _check_regressions(results)
    if verdict is RegressionVerdict.REGRESSION:
        has_regression = True
    print(final_sentence(has_regression=has_regression, verdict=verdict))
    return 1 if has_regression else 0


if __name__ == "__main__":
    sys.exit(main())
