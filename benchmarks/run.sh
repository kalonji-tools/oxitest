#!/usr/bin/env bash
set -euo pipefail

# Run from repo root regardless of where script is called from
cd "$(dirname "$0")/.."

echo "=== Generating bench test files ==="
python benchmarks/generate.py

RESULTS_DIR="benchmarks"
WARMUP=3
RUNS="${BENCH_RUNS:-10}"

# Build pytest comparison commands only if pytest is available
PYTEST_CMDS=()
if command -v pytest &>/dev/null; then
  PYTEST_CMDS=("pytest")
  echo "pytest found — including comparison benchmarks"
else
  echo "pytest not found — running oxitest-only benchmarks"
fi

echo ""
echo "=== Tier: startup ==="
STARTUP_CMDS=('oxitest benchmarks/generated/startup/')
[[ ${#PYTEST_CMDS[@]} -gt 0 ]] && STARTUP_CMDS+=('pytest benchmarks/generated/startup/')
hyperfine \
  --warmup "$WARMUP" \
  --runs "$RUNS" \
  --export-json "$RESULTS_DIR/results_startup.json" \
  "${STARTUP_CMDS[@]}"

echo ""
echo "=== Tier: below_threshold (serial only) ==="
BT_CMDS=('oxitest --serial benchmarks/generated/below_threshold/oxitest/')
[[ ${#PYTEST_CMDS[@]} -gt 0 ]] && BT_CMDS+=('pytest benchmarks/generated/below_threshold/pytest/')
hyperfine \
  --warmup "$WARMUP" \
  --runs "$RUNS" \
  --export-json "$RESULTS_DIR/results_below_threshold.json" \
  "${BT_CMDS[@]}"

for tier in s m l; do
  echo ""
  echo "=== Tier: $tier (serial + parallel) ==="
  TIER_CMDS=(
    "oxitest --serial benchmarks/generated/${tier}/oxitest/"
    "oxitest benchmarks/generated/${tier}/oxitest/"
  )
  [[ ${#PYTEST_CMDS[@]} -gt 0 ]] && TIER_CMDS+=("pytest benchmarks/generated/${tier}/pytest/")
  hyperfine \
    --warmup "$WARMUP" \
    --runs "$RUNS" \
    --export-json "$RESULTS_DIR/results_${tier}.json" \
    "${TIER_CMDS[@]}"

  echo ""
  echo "=== Tier: $tier (cache cold) ==="
  hyperfine \
    --warmup "$WARMUP" \
    --runs "$RUNS" \
    --prepare 'rm -rf .oxitest_cache' \
    --export-json "$RESULTS_DIR/results_${tier}_cold.json" \
    "oxitest benchmarks/generated/${tier}/oxitest/"
done

# ── Lazy collection benchmarks ────────────────────────────────────────────────
# Uses the largest tier (l) to measure filtered-run speedup from lazy collection.

LAZY_DIR="benchmarks/generated/l/oxitest"
FIRST_FILE=$(ls "$LAZY_DIR"/test_gen_0.py 2>/dev/null)
if [ -n "$FIRST_FILE" ]; then
  # Pick a concrete test from the first file for node-ID targeting
  FIRST_TEST="${LAZY_DIR}/test_gen_0.py::test_trivial_0"

  echo ""
  echo "=== Lazy: single node ID ==="
  hyperfine \
    --warmup "$WARMUP" \
    --runs "$RUNS" \
    --export-json "$RESULTS_DIR/results_lazy_node_id.json" \
    "oxitest $FIRST_TEST"

  echo ""
  echo "=== Lazy: expression filter name() ==="
  hyperfine \
    --warmup "$WARMUP" \
    --runs "$RUNS" \
    --export-json "$RESULTS_DIR/results_lazy_name.json" \
    "oxitest $LAZY_DIR -E 'name(test_trivial_0)'"

  echo ""
  echo "=== Lazy: expression filter mark() ==="
  # The generated files don't have marks, so this filters to 0 matches (fast path)
  hyperfine \
    --warmup "$WARMUP" \
    --runs "$RUNS" \
    --export-json "$RESULTS_DIR/results_lazy_mark.json" \
    "oxitest $LAZY_DIR -E 'mark(nonexistent)'" || true
fi

echo ""
echo "=== Merging results ==="
python -c "
import json
from pathlib import Path

merged = {'results': []}
results_dir = Path('benchmarks')
for f in sorted(results_dir.glob('results_*.json')):
    if f.name == 'results.json':
        continue
    data = json.loads(f.read_text())
    tier = f.stem.removeprefix('results_')
    for r in data['results']:
        r['tier'] = tier
    merged['results'].extend(data['results'])

Path('benchmarks/results.json').write_text(json.dumps(merged, indent=2) + '\n')
print(f'Merged {len(merged[\"results\"])} results into benchmarks/results.json')
"

echo "=== Done ==="
