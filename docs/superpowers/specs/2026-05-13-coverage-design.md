# Coverage Integration Design

**Date:** 2026-05-13
**Issue:** #5
**Status:** Approved

---

## Goal

Add test coverage reporting for both the Rust core and Python bridge, surfacing results directly on PRs via sticky comments and persisting history via Codecov.

---

## Jobs

Two parallel jobs added to `.github/workflows/ci.yml`.

### `rust-coverage`

**Trigger:** `pull_request` targeting `main` + `push` to `main`

**Steps:**
1. `actions/checkout@v6`
2. `dtolnay/rust-toolchain@stable`
3. `Swatinem/rust-cache@v2` — `shared-key: coverage`, `save-if: true`
4. `taiki-e/install-action@v2` — `tool: cargo-tarpaulin` (prebuilt binary)
5. `cargo tarpaulin --out xml` → `cobertura.xml`
6. `irongut/CodeCoverageSummary@v1.3.0` — reads `cobertura.xml`, outputs markdown table
7. `marocchino/sticky-pull-request-comment@v2` — header `rust-coverage`, posts/updates sticky comment
8. `codecov/codecov-action@v4` — `files: cobertura.xml`, `flags: rust`, `token: ${{ secrets.CODECOV_TOKEN }}`

**Permissions:** `contents: read`, `pull-requests: write`

---

### `python-coverage`

**Trigger:** `pull_request` targeting `main` + `push` to `main`

**Steps:**
1. `actions/checkout@v6`
2. `dtolnay/rust-toolchain@stable` (needed to build the PyO3 extension)
3. `Swatinem/rust-cache@v2` — `shared-key: coverage`, `save-if: false` (reads cache written by `rust-coverage`)
4. `astral-sh/setup-uv@v7` — `enable-cache: true`, `python-version: 3.12`
5. `uv sync --group coverage`
6. `uv run maturin develop`
7. `PYTHONPATH=python uv run coverage run --source=oxitest -m oxitest python/tests/`
8. `uv run coverage xml` → `coverage.xml`
9. `irongut/CodeCoverageSummary@v1.3.0` — reads `coverage.xml`, outputs markdown table
10. `marocchino/sticky-pull-request-comment@v2` — header `python-coverage`, posts/updates sticky comment
11. `codecov/codecov-action@v4` — `files: coverage.xml`, `flags: python`, `token: ${{ secrets.CODECOV_TOKEN }}`

**Permissions:** `contents: read`, `pull-requests: write`

---

## Caching Strategy

| Cache | Job | Config |
|---|---|---|
| Rust artifacts | `rust-coverage` | writes (`save-if: true`) |
| Rust artifacts | `python-coverage` | reads only (`save-if: false`) |
| uv package cache | `python-coverage` | `enable-cache: true` |
| tarpaulin binary | `rust-coverage` | prebuilt via `taiki-e/install-action` |

Both jobs share the same Rust cache via `shared-key: coverage`. Only `rust-coverage` writes it to avoid duplicate writes and race conditions.

---

## PR Experience

On each PR commit:
- A sticky "🦀 Rust Coverage" comment is posted/updated with a module-level coverage table
- A sticky "🐍 Python Coverage" comment is posted/updated with a module-level coverage table
- Codecov adds inline line-level diff annotations to the PR

On push to main:
- Both jobs run, Codecov uploads happen, sticky comment steps are skipped (no PR context)

---

## `pyproject.toml` Changes

```toml
[dependency-groups]
coverage = [
    "coverage>=7",
    { include-group = "test" },
]
```

---

## Required Setup

Add `CODECOV_TOKEN` secret to repo **Settings → Secrets → Actions** (one-time, from codecov.io).

---

## Out of Scope

- Coverage thresholds / fail-on-drop (can be added via `codecov.yml` later)
- Windows / macOS coverage (Linux only)
- PR comment on push to main
