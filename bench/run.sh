#!/usr/bin/env bash
set -euo pipefail

# Run from repo root regardless of where script is called from
cd "$(dirname "$0")/.."

echo "=== Generating bench test files ==="
python bench/generate.py

RESULTS_DIR="bench"
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
STARTUP_CMDS=('oxitest bench/generated/startup/')
[[ ${#PYTEST_CMDS[@]} -gt 0 ]] && STARTUP_CMDS+=('pytest bench/generated/startup/')
hyperfine \
  --warmup "$WARMUP" \
  --runs "$RUNS" \
  --export-json "$RESULTS_DIR/results_startup.json" \
  "${STARTUP_CMDS[@]}"

echo ""
echo "=== Tier: below_threshold (serial only) ==="
BT_CMDS=('oxitest --serial bench/generated/below_threshold/oxitest/')
[[ ${#PYTEST_CMDS[@]} -gt 0 ]] && BT_CMDS+=('pytest bench/generated/below_threshold/pytest/')
hyperfine \
  --warmup "$WARMUP" \
  --runs "$RUNS" \
  --export-json "$RESULTS_DIR/results_below_threshold.json" \
  "${BT_CMDS[@]}"

for tier in s m l; do
  echo ""
  echo "=== Tier: $tier (serial + parallel) ==="
  TIER_CMDS=(
    "oxitest --serial bench/generated/${tier}/oxitest/"
    "oxitest bench/generated/${tier}/oxitest/"
  )
  [[ ${#PYTEST_CMDS[@]} -gt 0 ]] && TIER_CMDS+=("pytest bench/generated/${tier}/pytest/")
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
    "oxitest bench/generated/${tier}/oxitest/"
done

echo ""
echo "=== Merging results ==="
python -c "
import json
from pathlib import Path

merged = {'results': []}
results_dir = Path('bench')
for f in sorted(results_dir.glob('results_*.json')):
    if f.name == 'results.json':
        continue
    data = json.loads(f.read_text())
    tier = f.stem.removeprefix('results_')
    for r in data['results']:
        r['tier'] = tier
    merged['results'].extend(data['results'])

Path('bench/results.json').write_text(json.dumps(merged, indent=2) + '\n')
print(f'Merged {len(merged[\"results\"])} results into bench/results.json')
"

echo "=== Done ==="
