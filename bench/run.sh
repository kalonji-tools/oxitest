#!/usr/bin/env bash
set -euo pipefail

# Run from repo root regardless of where script is called from
cd "$(dirname "$0")/.."

echo "=== Generating bench test files ==="
python bench/generate.py

RESULTS_DIR="bench"
WARMUP=3
RUNS=10

echo ""
echo "=== Tier: startup ==="
hyperfine \
  --warmup "$WARMUP" \
  --runs "$RUNS" \
  --export-json "$RESULTS_DIR/results_startup.json" \
  'oxitest bench/generated/startup/' \
  'pytest bench/generated/startup/'

echo ""
echo "=== Tier: below_threshold (serial only) ==="
hyperfine \
  --warmup "$WARMUP" \
  --runs "$RUNS" \
  --export-json "$RESULTS_DIR/results_below_threshold.json" \
  'oxitest --serial bench/generated/below_threshold/oxitest/' \
  'pytest bench/generated/below_threshold/pytest/'

for tier in s m l; do
  echo ""
  echo "=== Tier: $tier (serial + parallel) ==="
  hyperfine \
    --warmup "$WARMUP" \
    --runs "$RUNS" \
    --export-json "$RESULTS_DIR/results_${tier}.json" \
    "oxitest --serial bench/generated/${tier}/oxitest/" \
    "oxitest bench/generated/${tier}/oxitest/" \
    "pytest bench/generated/${tier}/pytest/"

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
