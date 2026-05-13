# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0](https://github.com/kalonji-tools/oxitest/releases/tag/v0.1.0) - 2026-05-13

### Added

- *(release)* add release job to publish workflow — creates GitHub Release after PyPI publish
- *(release)* add cliff.toml for git-cliff changelog configuration
- *(hooks)* add pre-push hook to validate v* tag matches Cargo.toml version
- implement --capture-environment environment snapshot
- create build.rs and add --capture-environment flag to Cli
- *(tooling)* add AI agent checklist items to PR template
- *(tooling)* add pull request template
- *(tooling)* add feature request issue template
- *(tooling)* add bug report issue template
- AST assert rewriter — operand capture for enriched failure output
- fixture namespaces — Fixtures(name=), FixturesProxy, fx:Fixtures injection
- parallel execution — Scheduler, persistent worker pool, --workers N, --serial
- test cache — TestCache, --lf, --ff, --durations, per-test timeout scaling
- strict mode — bare-assert checker, dict-parametrize violations, --strict flag
- test helpers — raises, warns, importorskip, timeout
- built-in fixtures Tier 2 — LogCapture, WarnCapture, FixtureTeardownWarning
- built-in fixtures Tier 1 — TempDir, TempDirFactory, StdCapture, FdCapture, Patcher
- type-safe fixture annotations — Fixture[T], FixtureRef[T], Yields[T]
- parametrize — mark.parametrize → oxitest.parametrize, dict/dataclass modes
- fixture engine — Fixtures class, FixtureDef, FixtureSession, conftest loader
- marks — skip, skipif, xfail, usefixtures, custom markers, -m expression filter
- TTY/CI reporter — diagnostic blocks, ANSI color, exit codes, --json CTRF
- initial pipeline v0.1 — config, collector, PyO3 bridge, executor, reporter

### Fixed

- *(release)* configure release-plz to skip crates.io registry
- *(release)* remove unnecessary fetch-depth, pin action-gh-release to SHA
- *(release)* protect breaking commits, tighten parser regexes, clean up cliff.toml
- *(ci)* switch tarpaulin to cargo-llvm-cov (compatible with PyO3 extension-module)
- *(ci)* add setup-uv to rust-coverage so PyO3 can find libpython for linking
- *(ci)* fix sticky comment path, pin tarpaulin output, add codecov fail_ci_if_error
- *(bridge)* migrate PyO3 API from 0.22 to 0.28
- *(docs)* annotate Fixtures.fixture impl signature to silence griffe warnings
- *(prek)* correct remote-ref field in check-no-plans-on-main; add no-commit-to-branch builtin
- move capture-environment early-exit before filesystem setup; update exit-codes doc
- resolve real git dir in build.rs for worktree compatibility
- strict = "abort" in pyproject.toml; remove test_skipped_via_pytest
- set VIRTUAL_ENV before maturin develop to prevent uv resolving into Nix store
- remove UV_PYTHON — causes maturin to target immutable Nix store

### Other

- *(ci)* remove dead step ids from coverage jobs
- remove spec/plan files before merging to main
- *(coverage)* add python-coverage job with sticky PR comment
- *(coverage)* add rust-coverage job with sticky PR comment
- *(coverage)* remove monolithic coverage job
- *(plan)* coverage integration implementation plan
- *(spec)* coverage integration design for issue #5
- add coverage job with tarpaulin (Rust) and coverage.py (Python) ([#5](https://github.com/kalonji-tools/oxitest/pull/5))
- enable uv and Rust caching to reduce wall time ([#9](https://github.com/kalonji-tools/oxitest/pull/9))
- *(security)* add SECURITY.md with disclosure policy ([#11](https://github.com/kalonji-tools/oxitest/pull/11))
- expand test job to Python 3.11 / 3.12 / 3.13 matrix ([#10](https://github.com/kalonji-tools/oxitest/pull/10))
- *(deps)* bump actions/upload-pages-artifact from 3 to 5
- *(deps)* bump actions/checkout from 4 to 6
- *(deps)* bump actions/download-artifact from 4 to 8
- *(deps)* bump dorny/paths-filter from 3 to 4
- *(deps)* bump the cargo-deps group with 3 updates
- *(deps)* bump actions/setup-python from 5 to 6
- *(deps)* bump astral-sh/setup-uv from 5 to 7
- add status badges to README ([#7](https://github.com/kalonji-tools/oxitest/pull/7))
- *(tooling)* add dependabot config with grouped cargo updates ([#8](https://github.com/kalonji-tools/oxitest/pull/8))
- *(tooling)* add MIT license ([#4](https://github.com/kalonji-tools/oxitest/pull/4))
- *(docs)* add workflow_dispatch trigger for manual deploys ([#22](https://github.com/kalonji-tools/oxitest/pull/22))
- *(docs)* use dorny/paths-filter so required job always runs on PRs
- *(docs)* skip build on non-docs PRs; add required-check companion job
- add CONTRIBUTING.md with label taxonomy
- embed OXITEST_GIT_SHA via build.rs
- document --capture-environment flag in CLI reference
- add failing tests for --capture-environment CLI flag
- use justfile_directory() instead of pwd for VIRTUAL_ENV path
- prevent uv from downloading Python — delegate to Nix
- skip nix develop when already inside the dev shell
- uv dependency groups — build, test, lint, typecheck, docs; use uv in CI
- docs workflow — build on PRs, deploy to GitHub Pages on main
- test workflow — lint, type-check, Rust/Python tests, plans/specs guard
- add project background — educational goals and motivation
- pre-push hook — reject plans/specs on main
- release automation — release-plz, maturin PyPI publish, workflow permissions
- mkdocs-material site — guides, references, explanation, contributing pages
- comprehensive test suite — fixtures, marks, parametrize, cache, parallel
- add dev tooling — prek, ruff, codespell, justfile, pyproject
- scaffold project — Nix devshell, maturin crate, .gitignore
