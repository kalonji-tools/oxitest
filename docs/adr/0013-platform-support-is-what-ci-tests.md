# ADR-0013: Platform support is what CI tests

**Status:** Accepted
**Date:** 2026-08-10

oxitest shipped wheels for platforms it had never run a test on, and nothing in the repository was wrong about it, because nothing in the repository said anything about it at all.

Three files each encode a platform set. Measured while charting [#1946](https://github.com/kalonji-tools/oxitest/issues/1946) on `988eb35e`:

| Encodes | File | Said |
|---|---|---|
| what we **promise** | `classifiers` in `pyproject.toml` | *nothing* — the key was absent entirely |
| what we **ship** | `publish.yml` wheel targets | linux x86_64, linux aarch64, macOS universal2 |
| what we **test** | `test.yml` platform jobs | linux x86_64 |

Three targets shipped; one was tested. The macOS `universal2` wheel had never had a test run on either of its slices, the Linux `aarch64` wheel was cross-built on an x86 runner and never executed, and PyPI was told nothing either way. Windows was worse than absent: `python/oxitest/_bridge/_timeout.py` was titled *"Cross-platform test timeout enforcement"* and carried ~41 lines of `_WindowsTimeoutContext` that no test had ever entered, so a reader of that file would reasonably conclude Windows worked.

None of that was a decision. The question *"which platforms does oxitest support?"* had never been asked, so each file had drifted to whatever its own author needed, and no gate compared them.

## Considered Options

1. **The shipped set defines support.** `publish.yml` is the closest thing to a customer-facing promise, so treat its wheel targets as the definition and derive the rest. Rejected: it is a string in a config file. A wheel target asserts that a build succeeded, which is a much weaker claim than the one users read it as, and at charting time it was asserting support for three platforms whose test count was zero. A definition that was false the day it was written is not a definition.

2. **A two-tier model: *supported* versus *published*.** Keep both sets, name the difference, and document that a published-but-unsupported platform gets wheels without a guarantee. Rejected for two reasons. The tier boundary is **unenforceable** — nothing can check that a bug report against a published-but-unsupported platform is triaged differently, so the tier exists only in prose and decays into a way of describing whatever the sets happen to be. And it answers the wrong question: it asks *"how do we describe the current state?"* when the question on the table was *"what should the state be?"*

3. **The tested set defines support (chosen).** `supported(X)` means CI runs the full suite on X. Wheel targets and `classifiers` are derived from that set.

Option 3 wins on one property the other two lack: it is grounded in an **observable event** rather than a string. A CI job either ran the suite on a real machine of that architecture or it did not, and the run is recorded, dated and re-runnable. The other two definitions are assertions a file makes about itself.

## Decision

> **`supported(X)` if and only if CI runs the full test suite on X. Wheel targets and `classifiers` are derived from that set, never the reverse.**

### Rule 1 — The tested set is the definition

A platform is supported when a job in `.github/workflows/test.yml` runs the full suite on it *and* that job is in the `Tests (required)` rollup's `needs:`. Both halves are load-bearing. A job outside the rollup can be red indefinitely without blocking anything, and a permanently-advisory job is how CI rots into silently-red — so an advisory job does not confer support.

A **configuration variant** is not a platform. `Test (symlinked TMPDIR)` runs on `ubuntu-latest` with `TMPDIR` pointed at a symlink; it reproduces a path-spelling class on Linux and is required, but it adds no platform to the set. The distinction is recorded at the job itself and in the gate's allowlist, not inferred from `runs-on`.

### Rule 2 — The other two sets are derived, in one direction

`publish.yml`'s wheel targets and `pyproject.toml`'s `classifiers` are computed from the tested set. The derivation runs **tested ⇒ shipped ⇒ promised** and never backwards.

The direction matters because both failure modes are real and only one of them is tempting. Discovering that a shipped platform is untested creates pressure to drop the wheel; discovering that a tested platform is unshipped creates pressure to do nothing, because nothing is broken. Rule 2 answers both the same way: the tested set moved, so the other two follow it. Windows entered the supported set this way — measured by [#1951](https://github.com/kalonji-tools/oxitest/issues/1951), unblocked by [#1986](https://github.com/kalonji-tools/oxitest/issues/1986), promoted to required by [#1989](https://github.com/kalonji-tools/oxitest/issues/1989), and only then given a wheel target and a classifier.

### Rule 3 — One canonical vocabulary, because the three files share none

The three sets cannot be compared as written. `test.yml` names GitHub runner labels, `publish.yml` names maturin targets, and `classifiers` names trove strings that **have no architecture axis at all** — the published list at <https://pypi.org/classifiers/> contains no classifier encoding a CPU architecture.

So platform identity is canonically `(os, arch)`, and each file declares a mapping into it:

| Canonical | `test.yml` job id | `publish.yml` target | `publish.yml` gate runner | Classifier |
|---|---|---|---|---|
| `linux-x86_64` | `python-tests`, `rust-tests` | `x86_64` | `ubuntu-latest` | `Operating System :: POSIX :: Linux` |
| `linux-aarch64` | `linux-arm` | `aarch64` | `ubuntu-24.04-arm` | `Operating System :: POSIX :: Linux` |
| `macos-arm64` | `macos-arm` | `universal2-apple-darwin` | `macos-latest` | `Operating System :: MacOS :: MacOS X` |
| `macos-x86_64` | `macos-intel` | `universal2-apple-darwin` | `macos-15-intel` | `Operating System :: MacOS :: MacOS X` |
| `windows-x86_64` | `windows` | `x86_64-pc-windows-msvc` | `windows-latest` | `Operating System :: Microsoft :: Windows` |

The gate-runner column arrived with [#2177](https://github.com/kalonji-tools/oxitest/issues/2177). It is the fourth mapping rather than a repeat of the second: `universal2-apple-darwin` is **one** wheel and **two** canonical platforms, so the two macOS rows share a target and carry different runners. Installing that one wheel on both runners is the only evidence that both of its slices load.

Two consequences of the mapping are decisions rather than details. **`universal2-apple-darwin` is one target covering two canonical platforms** — a single wheel holding both slices — so the shipped set is a many-to-one image and the comparison is over canonical identities, not over target strings. And **`classifiers` is compared as the OS projection only**, because there is nothing finer to compare it against; `Operating System :: POSIX :: Linux` is satisfied by either Linux row and carries no claim about architecture.

### Rule 4 — The invariant is a gate, not prose

`scripts/check_platform_sets.py`, wired as a `prek` hook, holds four assertions:

1. **Ship what you test** — the canonical sets from `test.yml` and `publish.yml` are equal.
2. **Promise what you test** — the OS projection of the tested set equals the classifier set.
3. **No undeclared platform job** — every job in the `Tests (required)` rollup's `needs:` is either a mapped platform job or a member of an explicit non-platform allowlist (`changes`, `rust-tests`, `tmpdir-symlink`). Without this, a new platform job is invisible: the mapping only knows the job ids written in it, so a `Test (FreeBSD)` job could be added, become required, and confer support that no file records.
4. **Vacuity guard, both directions** — each parsed set must be non-empty before any comparison, and each allowlist entry must actually appear in `needs:`. A set comparison whose parser silently returns nothing passes while guarding nothing, and an allowlist entry for a job that no longer exists is a standing exemption nobody re-reads. Over-matching and under-matching are the same hazard and a set comparison catches neither.

Prose was not available as an option here. The state this ADR replaces *was* prose — three files, each internally sensible, disagreeing for two years because agreement was nobody's job.

## Scope

This ADR governs the platform set and how the three declarations relate. It does not govern which Python versions are tested: version and platform are orthogonal axes, four versions are covered on Linux, and the platform jobs run 3.12 only.

**Release gating is deliberately not built.** A release cannot ship past a red platform job, because the platform jobs are in the `Tests (required)` rollup and `main` is protected on it — the gate is transitive and adding a second one at the publish step would guard nothing new. Recorded in [#1946](https://github.com/kalonji-tools/oxitest/issues/1946)'s `## Out of scope` and re-affirmed here.

## Consequences

- **Adding a platform is a five-file change since [#2177](https://github.com/kalonji-tools/oxitest/issues/2177), and the gate says so.** A new platform job must be mapped in `scripts/check_platform_sets.py`, given a wheel target, given a classifier, and given a runner in `publish.yml`'s Distribution band `gate` matrix. Forgetting any one of them fails the check with a message naming this ADR. That is the intended cost: the drift this ADR exists to prevent was cheap precisely because each file could move alone.

- **Removing a platform is symmetrical and is a user-visible break.** Dropping a job from the rollup drops the platform from the supported set, which drops its wheel and its classifier. That is a semver event, and the gate turns it into a deliberate four-file edit rather than a quiet job deletion.

- **Windows is supported, and `_WindowsTimeoutContext` is now executed code.** The 41 unexecuted lines that motivated [#1946](https://github.com/kalonji-tools/oxitest/issues/1946)'s framing are covered by a required job. The fate-of-the-code question that map left open is answered by the platform decision rather than separately.

- **oxitest ships a Windows wheel that no job has built.** `publish.yml` runs on `v*` tag push only, so the Windows build job cannot be exercised by a pull request. What is established: `.github/actions/platform-test` runs `uv run maturin develop` on `windows-latest` and that job is green, so maturin builds oxitest on that runner. What is not: the release build's four-interpreter `--release --out dist` shape. The Windows job is in the `publish` job's `needs:`, so a failure blocks the upload rather than shipping a partial release.

- **This ADR claimed that a suite passed on a platform, and not that a published wheel worked there. [#2177](https://github.com/kalonji-tools/oxitest/issues/2177) closed that boundary.** Every platform job still builds from source with `maturin develop`, so a `manylinux2014_aarch64` wheel could fail to load on a real aarch64 machine and every check *described here* would stay green. What changed is that the artifact is no longer unexamined: ADR-0019's Distribution band gate installs each of the 17 release artifacts on a runner matching its tag, imports it from outside the source tree, and runs the CLI once, before `publish` runs. The separate change this bullet asked for is that gate. So "supported" now carries both readings — the suite runs there, **and** the artifact was installed and imported there — and the two are held equal by check 5 of `scripts/check_platform_sets.py`.

- **The user-facing list lives in `docs/user/reference/stability.md`**, not here, and cites this ADR by absolute GitHub URL because `docs/adr/` sits outside `mkdocs.yml`'s `docs_dir` (`docs/user`) and is not published on the documentation site.

- **This ADR has no structural gate.** Its links are not validated by `mkdocs --strict`, for the reason above. Its *content*, uniquely among this repository's ADRs, is machine-checked — Rule 3's table is mirrored in `scripts/check_platform_sets.py`, so the decision and the code cannot disagree for long.

- **That script now also carries a check this ADR does not own.** Check 6, added by [#2177](https://github.com/kalonji-tools/oxitest/issues/2177), compares the five declarations of the interpreter set and holds each against `requires-python`. It is the same shape of question on a different axis, and it lives beside the platform checks because it reads the same two workflow files. It is not part of Rule 3, and no row of the table above states an interpreter. The script's own docstring is authoritative for what each check does.
