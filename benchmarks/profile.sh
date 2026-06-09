#!/usr/bin/env bash
set -euo pipefail

# Profile execution phases for spike #827.
# Collects PhaseTimings data across tiers, modes, and worker counts.

cd "$(dirname "$0")/.."

echo "=== Generating bench test files ==="
python benchmarks/generate.py

RESULTS_FILE="benchmarks/profile_results.txt"
> "$RESULTS_FILE"

# Tiers to profile
TIERS=("l" "realistic")

# Warm the cache first
echo ""
echo "=== Warming cache ==="
for tier in "${TIERS[@]}"; do
    oxitest --serial "benchmarks/generated/${tier}/oxitest/" > /dev/null 2>&1 || true
done

echo ""
echo "=== Profiling ==="

for tier in "${TIERS[@]}"; do
    echo ""
    echo "--- Tier: $tier ---"

    # Serial baseline
    echo "  serial..."
    echo "=== $tier serial ===" >> "$RESULTS_FILE"
    OXITEST_PROFILE=1 oxitest --serial "benchmarks/generated/${tier}/oxitest/" 2>> "$RESULTS_FILE" > /dev/null || true
    echo "" >> "$RESULTS_FILE"

    # Parallel with different worker counts
    for workers in 1 2 4 auto; do
        echo "  parallel (workers=$workers)..."
        echo "=== $tier parallel workers=$workers ===" >> "$RESULTS_FILE"
        if [ "$workers" = "auto" ]; then
            OXITEST_PROFILE=1 oxitest "benchmarks/generated/${tier}/oxitest/" 2>> "$RESULTS_FILE" > /dev/null || true
        else
            OXITEST_PROFILE=1 oxitest --workers "$workers" "benchmarks/generated/${tier}/oxitest/" 2>> "$RESULTS_FILE" > /dev/null || true
        fi
        echo "" >> "$RESULTS_FILE"
    done
done

# Dogfood: profile oxitest's own test suite
echo ""
echo "--- Dogfood: oxitest test suite ---"
echo "=== dogfood serial ===" >> "$RESULTS_FILE"
OXITEST_PROFILE=1 oxitest --serial python/tests/ 2>> "$RESULTS_FILE" > /dev/null || true
echo "" >> "$RESULTS_FILE"

echo "=== dogfood parallel ===" >> "$RESULTS_FILE"
OXITEST_PROFILE=1 oxitest python/tests/ 2>> "$RESULTS_FILE" > /dev/null || true
echo "" >> "$RESULTS_FILE"

for workers in 1 2 4; do
    echo "  dogfood parallel (workers=$workers)..."
    echo "=== dogfood parallel workers=$workers ===" >> "$RESULTS_FILE"
    OXITEST_PROFILE=1 oxitest --workers "$workers" python/tests/ 2>> "$RESULTS_FILE" > /dev/null || true
    echo "" >> "$RESULTS_FILE"
done

echo ""
echo "=== Results saved to $RESULTS_FILE ==="
cat "$RESULTS_FILE"
