# Coverage Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the monolithic `coverage` job in `ci.yml` with two parallel jobs — `rust-coverage` and `python-coverage` — each posting a sticky PR comment and uploading to Codecov.

**Architecture:** Two independent CI jobs share a Rust build cache via `shared-key: coverage`. Each job generates a Cobertura XML report, summarises it with `irongut/CodeCoverageSummary`, posts a sticky PR comment via `marocchino/sticky-pull-request-comment`, and uploads to Codecov. Jobs trigger on every PR to main and on push to main.

**Tech Stack:** GitHub Actions, `cargo-tarpaulin`, `coverage.py`, `irongut/CodeCoverageSummary@v1.3.0`, `marocchino/sticky-pull-request-comment@v2`, `codecov/codecov-action@v4`, `taiki-e/install-action@v2`

---

## File Map

| File | Change |
|---|---|
| `.github/workflows/ci.yml` | Remove monolithic `coverage` job; add `rust-coverage` and `python-coverage` jobs |
| `pyproject.toml` | Already has `coverage` dependency group — no change needed |

---

### Task 1: Remove the monolithic `coverage` job

**Files:**
- Modify: `.github/workflows/ci.yml:75-122`

- [ ] **Step 1: Delete the monolithic coverage job**

Open `.github/workflows/ci.yml` and remove lines 75–122 (the entire `coverage:` job block), leaving `guard:` immediately after the `test:` job:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: read

jobs:
  lint:
    name: Lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6

      - uses: dtolnay/rust-toolchain@stable
        with:
          components: rustfmt, clippy

      - uses: Swatinem/rust-cache@v2

      - uses: astral-sh/setup-uv@v7
        with:
          enable-cache: true

      - run: uv sync --only-group lint

      - run: uv run ruff check python/
      - run: uv run ruff format --check python/
      - run: uv run codespell
      - run: cargo fmt --check
      - run: cargo clippy -- -D warnings

  test:
    name: Test (Python ${{ matrix.python-version }})
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ['3.11', '3.12', '3.13']
    steps:
      - uses: actions/checkout@v6

      - uses: dtolnay/rust-toolchain@stable

      - uses: Swatinem/rust-cache@v2
        with:
          shared-key: test
          save-if: ${{ matrix.python-version == '3.12' }}

      - uses: astral-sh/setup-uv@v7
        with:
          enable-cache: true
          python-version: ${{ matrix.python-version }}

      - run: uv sync --group test --group typecheck

      - name: Rust tests
        if: matrix.python-version == '3.12'
        run: cargo test

      - name: Build extension
        run: uv run maturin develop

      - name: ty check
        if: matrix.python-version == '3.12'
        run: uv run ty check

      - name: Python tests
        run: PYTHONPATH=python uv run python -m oxitest python/tests/

  guard:
    name: No plans/specs on main
    runs-on: ubuntu-latest
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v6

      - name: Check for plans/specs files
        run: |
          found=$(git ls-tree -r --name-only HEAD -- \
            docs/superpowers/plans docs/superpowers/specs 2>/dev/null || true)
          if [ -n "$found" ]; then
            echo "ERROR: plans/specs files found on main:"
            echo "$found"
            exit 1
          fi
```

- [ ] **Step 2: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "YAML valid"
```

Expected: `YAML valid`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(coverage): remove monolithic coverage job"
```

---

### Task 2: Add the `rust-coverage` job

**Files:**
- Modify: `.github/workflows/ci.yml` — insert `rust-coverage` job before `guard`

- [ ] **Step 1: Insert the `rust-coverage` job**

Add the following block immediately before the `guard:` job in `.github/workflows/ci.yml`:

```yaml
  rust-coverage:
    name: Rust Coverage
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v6

      - uses: dtolnay/rust-toolchain@stable

      - uses: Swatinem/rust-cache@v2
        with:
          shared-key: coverage

      - uses: taiki-e/install-action@v2
        with:
          tool: cargo-tarpaulin

      - name: Run Rust coverage
        run: cargo tarpaulin --out xml

      - name: Summarise Rust coverage
        id: rust_summary
        uses: irongut/CodeCoverageSummary@v1.3.0
        with:
          filename: cobertura.xml
          format: markdown
          output: both

      - name: Post Rust coverage comment
        if: github.event_name == 'pull_request'
        uses: marocchino/sticky-pull-request-comment@v2
        with:
          header: rust-coverage
          message: |
            ## 🦀 Rust Coverage
            ${{ steps.rust_summary.outputs.summary }}

      - name: Upload Rust coverage to Codecov
        uses: codecov/codecov-action@v4
        with:
          files: cobertura.xml
          flags: rust
          token: ${{ secrets.CODECOV_TOKEN }}
```

- [ ] **Step 2: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "YAML valid"
```

Expected: `YAML valid`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(coverage): add rust-coverage job with sticky PR comment"
```

---

### Task 3: Add the `python-coverage` job

**Files:**
- Modify: `.github/workflows/ci.yml` — insert `python-coverage` job after `rust-coverage` and before `guard`

- [ ] **Step 1: Insert the `python-coverage` job**

Add the following block immediately after the `rust-coverage` job and before `guard:`:

```yaml
  python-coverage:
    name: Python Coverage
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v6

      - uses: dtolnay/rust-toolchain@stable

      - uses: Swatinem/rust-cache@v2
        with:
          shared-key: coverage
          save-if: false

      - uses: astral-sh/setup-uv@v7
        with:
          enable-cache: true
          python-version: '3.12'

      - run: uv sync --group coverage

      - name: Build extension
        run: uv run maturin develop

      - name: Run Python coverage
        run: |
          PYTHONPATH=python uv run coverage run --source=oxitest -m oxitest python/tests/
          uv run coverage xml

      - name: Summarise Python coverage
        id: python_summary
        uses: irongut/CodeCoverageSummary@v1.3.0
        with:
          filename: coverage.xml
          format: markdown
          output: both

      - name: Post Python coverage comment
        if: github.event_name == 'pull_request'
        uses: marocchino/sticky-pull-request-comment@v2
        with:
          header: python-coverage
          message: |
            ## 🐍 Python Coverage
            ${{ steps.python_summary.outputs.summary }}

      - name: Upload Python coverage to Codecov
        uses: codecov/codecov-action@v4
        with:
          files: coverage.xml
          flags: python
          token: ${{ secrets.CODECOV_TOKEN }}
```

- [ ] **Step 2: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "YAML valid"
```

Expected: `YAML valid`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(coverage): add python-coverage job with sticky PR comment"
```

---

### Task 4: Clean up spec/plan files and push

The `guard` job blocks plans/specs from landing on `main`. Remove these files from the branch before merging.

**Files:**
- Delete: `docs/superpowers/specs/2026-05-13-coverage-design.md`
- Delete: `docs/superpowers/plans/2026-05-13-coverage-integration.md`

- [ ] **Step 1: Remove spec and plan files**

```bash
git rm docs/superpowers/specs/2026-05-13-coverage-design.md
git rm docs/superpowers/plans/2026-05-13-coverage-integration.md
git commit -m "chore: remove spec/plan files before merging to main"
```

- [ ] **Step 2: Push and verify CI**

```bash
git push origin ci/coverage-integration
```

Open the PR and confirm:
- `rust-coverage` and `python-coverage` jobs appear in the CI checks
- Both jobs complete without error
- Two sticky comments appear on the PR (🦀 Rust Coverage, 🐍 Python Coverage)

- [ ] **Step 3: Add `CODECOV_TOKEN` secret if not already set**

Go to: **GitHub → kalonji-tools/oxitest → Settings → Secrets and variables → Actions → New repository secret**

- Name: `CODECOV_TOKEN`
- Value: token from [codecov.io](https://codecov.io) after connecting the repo

If this secret is missing the Codecov upload steps will fail but the sticky comments will still work.
