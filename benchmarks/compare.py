"""Regression checker for multi-tier bench results."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REGRESSION_THRESHOLD = 0.10  # 10%


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


def main() -> int:
    results = load_results("benchmarks/results.json")

    # Startup
    startup = find_commands(results, tier="startup")
    ox_startup = find_command_mean(startup, "oxitest")
    py_startup = find_command_mean(startup, "pytest")

    print("STARTUP")
    if ox_startup and py_startup:
        print(f"  oxitest: {ox_startup * 1000:.0f}ms")
        print(f"  pytest:  {py_startup * 1000:.0f}ms")
        print(f"  speedup: {speedup_ratio(ox_startup, py_startup):.2f}x")
    print()

    # Per-tier summaries
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

        # Cache cold vs warm
        cold_results = find_commands(results, tier=f"{tier}_cold")
        if cold_results:
            ox_cold = find_command_mean(cold_results, "oxitest")
            if ox_cold and s["oxitest_parallel"]:
                cold_ms = f"{ox_cold * 1000:.0f}ms"
                warm_ms = f"{s['oxitest_parallel'] * 1000:.0f}ms"
                print(f"  cache cold: {cold_ms}  warm: {warm_ms}")

        print()

    # Regression check against baseline
    baseline_path = Path("benchmarks/baseline.json")
    if baseline_path.exists():
        baseline = load_results(str(baseline_path))
        print("REGRESSION CHECK")
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
        print()

    if has_regression:
        print("Regression detected.")
        return 1
    print("No regression detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
