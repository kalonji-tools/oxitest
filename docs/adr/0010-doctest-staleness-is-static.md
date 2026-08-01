# ADR-0010: Doctest staleness is a static property, not a run outcome

**Status:** Accepted
**Date:** 2026-08-01

The doctest coverage guard reports a scope or skip entry as *stale* when it looks like a typo, so a mistyped path (`src/mod.py` vs `src/mods.py`) cannot silently bypass coverage enforcement. Three successive predicates shipped for this guard and all three were wrong (#1796, attempts 1–3), each fixing the previous hole and opening a new one.

They failed for one reason. The guard asks a **static** question — *does this entry name something real?* — and answered it with **run-dependent** data: whether the entry matched anything during this particular invocation. Zero matches is not evidence of a typo. A `Prefix` entry legitimately yields no coverage subjects all the time (`test_*.py` exclusion, `conftest.py`, `__init__.py`, modules with only private definitions), and a narrowed run yields nothing for entries it never visited. Each attempt patched the mismatch with a proxy for "was this run narrowed", a question `Config` cannot answer correctly — `has_explicit_paths` is set by `--affected`, which narrows the item set but not the coverage walk.

**Decision.** Split the guard along the line between what is knowable statically and what only a run can know.

```
entry path missing on disk   ->  STALE, on every invocation shape (including --affected)
Prefix/File that exists      ->  never stale        (zero hits is never evidence)
Symbol/Member                ->  hit-based, gated on EXACT single-file membership
                                 in the scanned set -- `==`, never `starts_with`
```

The load-bearing invariant, which no test can express:

> **The staleness guard consults only the entry and the filesystem. It never reads `FilterConfig`, and never asks whether *this* run was narrowed.**

That is why `--affected`, `--lf`, `--ff`, `-E` and explicit CLI paths need no special case: none of them can change either input. All three prior attempts violated this invariant; anyone who reintroduces a run-level question will reopen #1796.

The "entry exists but yielded no coverage subjects" signal is dropped entirely — no diagnostic, no severity dial, no full-run special case.

## Considered Options

1. **Narrowing proxy** (attempts 1–3, shipped and reverted) — infer from `has_explicit_paths` and entry/scan-root overlap whether the run could have visited the entry. Rejected: the proxy does not exist cleanly in `Config`. Attempt 1 exempted every `oxitest <dir>`; attempt 2 exempted any entry mistyped at or above the `testpaths` prefix on a *full* run — worse than the bug; attempt 3 left `--affected` silent and treated scan-root-inside-entry as evidence, which reproduced #1796 with a *correct* entry.

2. **Pure path existence** (V4) — drop the hit-based half entirely. Scored 27/27 on the invocation-shape matrix, but loses sub-file typo detection: a `Symbol`/`Member` entry naming a function that does not exist becomes undetectable. Rejected for that regression.

3. **Static half at `Config::load`** — move path validation to the fail-closed config seam established by [ADR-0008](0008-config-fail-closed-narrow-scope.md). Rejected: load-time errors are unconditional `UsageError` (4), while staleness severity is driven by `strict` (`off` silences it, `enforce` makes it a warning). Moving it would make stale entries fatal for `strict = "enforce"` users and change the reporting channel from collection diagnostics to a hard exit.

4. **Hybrid, static plus exact-membership hit test** (chosen). Restores sub-file detection while keeping the invariant. Measured 27/27 on the matrix against V1's 15/27.

## Consequences

- **Three classes of unmatchable entry are knowingly not reported.** Each looks like a bug and is not; closing any of them without reading this ADR risks a fourth wrong predicate.

  | Blind spot | Why accepted |
  |---|---|
  | Entry exists but sits outside the declared test tree | Detecting it needs the *declared* `testpaths`, which `merge_paths` destroys — see **#1798**. Patching around it in the guard would add machinery #1798 deletes |
  | `Symbol`/`Member` entry whose file the scanner never parses — the entry abstains, whatever the reason the file was dropped. The current drop reasons, and which of them an explicit `scope` entry can override, live in `run_coverage_check` (`src/doctest/coverage.rs`); enumerating them here is how this row drifted the first time (#1799) | An unparsed file carries no evidence about the symbols inside it (#1796). Detecting these entries would couple staleness to the exclusion set, which **#1790** is actively deciding whether to change |
  | Entry exists and is reachable but yields no coverage subjects | Not evidence of anything. This is the signal all three attempts mistook for a typo |

- **#1798 is this ADR's expiry condition for the first blind spot.** Once `paths.testpaths` stops conflating the declared test tree with the effective run set, reachability becomes statically answerable and this ADR should be amended rather than worked around.

- **Two diagnostic messages, one per rule.** The static rule reports `'<entry>' names a path that does not exist`; the hit-based rule reports `'<entry>' matched no coverage subjects`. The old single message described neither accurately — a missing path never got as far as matching, and "fix the path" is wrong advice for a missing symbol. The `context` strings (`doctest.coverage.stale-scope` / `stale-skip`) are unchanged, so hard-fail classification is unaffected.

- **Runs that were green can turn red, and this ships as `fix:` anyway.** `oxitest --affected` with a mistyped entry went from exit 0 to exit 3. Unlike [ADR-0008](0008-config-fail-closed-narrow-scope.md), which introduced a new hard-fail class and shipped `feat!:`, this introduces nothing: stale entries have always been hard-fails under `strict = "abort"`. A false negative in an existing documented check is a bug fix, and the new message is self-diagnosing. `cliff.toml` sets `breaking_always_bump_major`, so marking it breaking would ship a major version for a guard fix.

- **The guard's correctness for `Prefix` depends on POSIX trailing-slash semantics.** The parser stores `Prefix` entries with their trailing `/`, so `exists()` rejects a regular file via `ENOTDIR` — which is why no explicit `is_dir()` check is needed. That behaviour is accidental, platform-dependent, and pinned by a dedicated test. If Windows support lands, that test fails and an explicit type check should be added then.

- **The unit family must kill mutants, not merely pass.** Hardwiring the predicate to `true` previously left 10 of 12 stale unit tests passing, and every integration test exercised a single invocation shape (a full run) — which is how three wrong predicates shipped green. `cfg_for_stale` is rebuilt on real files, and the invocation-shape matrix lives in the Python integration suite where shape can actually be expressed.

- **The principle generalises, but is decided only here.** *A user-named path that does not exist is a defect in the naming, not a fact about the run.* [#1797](https://github.com/kalonji-tools/oxitest/issues/1797) is the same principle on the CLI-argument surface, including the same split between a missing path (statically answerable) and a missing node ID (not). It is deliberately left to its own grilling — exit-code choice and filter carve-outs have no analogue in the config surface.
