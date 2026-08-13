# ADR-0010: Doctest staleness is a static property, not a run outcome

**Status:** Accepted (amended 2026-08-06, 2026-08-12 — see [Amendments](#amendments))
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

- **Three classes of unmatchable entry are knowingly not reported**, and one that was has since been closed. Each of the three looks like a bug and is not; closing any of them without reading this ADR risks a fourth wrong predicate. The closed row is kept rather than deleted, because *why* it was accepted is what dates the ones that remain.

  | Blind spot | Why accepted |
  |---|---|
  | Entry exists but sits outside the declared test tree | **CLOSED by [Amendment 1](#amendment-1--the-first-blind-spot-closes-2026-08-06) (2026-08-06).** Originally accepted because "Detecting it needs the *declared* `testpaths`, which `merge_paths` destroys — see **#1798**. Patching around it in the guard would add machinery #1798 deletes". #1798 shipped that field, so the machinery is no longer a workaround |
  | `Symbol`/`Member` entry whose file the scan never attempts to read — the entry abstains, whatever the reason the file was withheld. The current withholding reasons, and which of them an explicit `scope` entry can override, live in `run_coverage_check` (`src/doctest/coverage.rs`); enumerating them here is how this row drifted the first time (#1799). A file the scan attempts but cannot parse left this blind spot in #1800: the entry reports the parse failure (`doctest.coverage.parse-error`) instead of abstaining | A file the scan never read carries no evidence about the symbols inside it (#1796). Detecting these entries would couple staleness to the exclusion set, which **#1790** is actively deciding whether to change |
  | Entry exists and is reachable but yields no coverage subjects *for a reason the run discovered* | Not evidence of anything. This is the signal all three attempts mistook for a typo |
  | Entry names a file the built-in filters exclude — a `python_files` match, `conftest.py`, or a path under `norecursedirs` | Knowingly not reported, and **not** the row above: this one is provably futile from config alone. Split out by [Amendment 1](#amendment-1--the-first-blind-spot-closes-2026-08-06), which also records why only half of it is answerable — `File`/`Symbol`/`Member` entries name a filename the filters can be tested against, `Prefix` entries do not, and "every file under this prefix is excluded" is a hit-based question this ADR forbids |

- **#1798 was this ADR's expiry condition for the first blind spot, and it fired on 2026-08-06.** `paths.testpaths` stopped conflating the declared test tree with the effective run set, reachability became statically answerable, and the ADR was amended rather than worked around — see [Amendment 1](#amendment-1--the-first-blind-spot-closes-2026-08-06). The condition is spent; there is no second expiry hiding behind it.

- **One diagnostic message per rule** — two as of this ADR, three since [Amendment 1](#amendment-1--the-first-blind-spot-closes-2026-08-06). The static rule reports `'<entry>' names a path that does not exist`; the hit-based rule reports `'<entry>' matched no coverage subjects`; the reachability rule reports `'<entry>' is outside the declared test tree, so it can never match`. The old single message described none of them accurately — a missing path never got as far as matching, and "fix the path" is wrong advice for a missing symbol. The `context` strings (`doctest.coverage.stale-scope` / `stale-skip`) are unchanged throughout, so hard-fail classification is unaffected.

- **Runs that were green can turn red, and this ships as `fix:` anyway.** `oxitest --affected` with a mistyped entry went from exit 0 to exit 3. Unlike [ADR-0008](0008-config-fail-closed-narrow-scope.md), which introduced a new hard-fail class and shipped `feat!:`, this introduces nothing: stale entries have always been hard-fails under `strict = "abort"`. A false negative in an existing documented check is a bug fix, and the new message is self-diagnosing. `cliff.toml` sets `breaking_always_bump_major`, so marking it breaking would ship a major version for a guard fix.

- **The guard's correctness for `Prefix` depends on POSIX trailing-slash semantics.** The parser stores `Prefix` entries with their trailing `/`, so `exists()` rejects a regular file via `ENOTDIR` — which is why no explicit `is_dir()` check is needed. That behaviour is accidental, platform-dependent, and pinned by a dedicated test. If Windows support lands, that test fails and an explicit type check should be added then.

- **The unit family must kill mutants, not merely pass.** Hardwiring the predicate to `true` previously left 10 of 12 stale unit tests passing, and every integration test exercised a single invocation shape (a full run) — which is how three wrong predicates shipped green. `cfg_for_stale` is rebuilt on real files, and the invocation-shape matrix lives in the Python integration suite where shape can actually be expressed.

- **The principle generalises, but is decided only here.** *A user-named path that does not exist is a defect in the naming, not a fact about the run.* [#1797](https://github.com/kalonji-tools/oxitest/issues/1797) is the same principle on the CLI-argument surface, including the same split between a missing path (statically answerable) and a missing node ID (not). It is deliberately left to its own grilling — exit-code choice and filter carve-outs have no analogue in the config surface.

## Amendments

### Amendment 1 — the first blind spot closes (2026-08-06)

[#1798](https://github.com/kalonji-tools/oxitest/issues/1798) split the declared
test tree out of `paths.testpaths`, then removed the last route by which the
coverage walk could see positional CLI paths. Both halves were needed. The field
alone left a project that declares no `testpaths` falling back to the
argv-overwritten one, so `oxitest tests/` audited only `tests/` — the original
defect surviving one layer down, in the branch no test reached, while
`docs/user/how-to/use-doctests.md` promised the opposite.

**The new verdict.** A `scope` or `skip` entry that exists on disk but is
disjoint from every declared root is now reported as stale:

```
unreachable ⟺ ∀d ∈ D: !E.starts_with(d) && !d.starts_with(E)
```

Symmetric, not containment. An entry that *contains* a declared root — `src/`
against `testpaths = ["src/pkg"]` — matches every subject under it, so
containment alone would report a working config stale. That is the same
"correct entry reported stale" shape that reopened #1796 on attempt 3, and it is
pinned by `stale_prefix_entry_containing_the_declared_tree_is_never_stale`.

The check is ordered **after** the existence check, so an entry that is both
mistyped and disjoint reports as a typo — existence is the more fundamental and
more certain fact, and "add it to testpaths" is wrong advice for a filename that
will still be wrong afterwards. It applies to `scope` and `skip` alike: the
private-module abstention is not a precedent, because that one exists where the
scan never *read* the file, whereas reachability holds either way.

**Why the invariant survives.** The guard now consults a third input,
`declared_testpaths`, via `collector::coverage_roots`. That is legal only
because no coverage path can reach `paths.testpaths` any more — the invariant
became a property of the call graph rather than a rule review must uphold. It
rests on one fact: **`Config::merge_toml`, in `src/config/merge.rs`, is the sole
writer of `declared_testpaths`** outside its `Default` and the test modules.
Anything that starts writing it from argv reopens #1796, and no test can express
that — which is why it is written here.

Named by symbol rather than by line (#2112). The line this sentence used to cite
had already drifted onto an unrelated field, and a stale line number rots
silently: it lands on plausible neighbouring code and still reads as a citation.
The same sentence, with the same defect, sat on `StalenessInputs::coverage_roots`
in `src/pipeline/collection.rs`.

The same reasoning is why the diagnostic reuses `doctest.coverage.stale-scope` /
`stale-skip` rather than minting a context of its own. This ADR already reserves
those two for "entries that can never match", which is exactly what an
unreachable entry is; and `split_coverage_diagnostics` hard-fails on a
hardcoded list of five context strings, so a sixth that nobody adds there
degrades silently to a pending warning under `abort`.

**What was not built.** The built-in-filter class, now split out as its own row
above. A `File`/`Symbol`/`Member` entry naming a `test_*.py`, a `conftest.py`,
or a path under `norecursedirs` is provably unmatchable from config alone — but
the `Prefix` equivalent is not, and shipping only the answerable half would
report `scope = ["tests/test_one.py"]` while staying silent on
`scope = ["tests/"]`. An asymmetry users would read as a bug, with no
principled defence. If it is ever built it needs its own grilling, not a fourth
predicate written from this paragraph.

### Amendment 2 — the declared root set becomes declarable (2026-08-12)

[#1790](https://github.com/kalonji-tools/oxitest/issues/1790) adds
`[tool.oxitest.doctest] roots`. Amendment 1 defined reachability against the
declared test tree, `D`. This amendment changes what `D` can be, and nothing
else.

**The invariant holds.** `roots` is read from configuration and resolved against
the filesystem. It is not `FilterConfig`, and it cannot say whether *this* run
was narrowed, so the load-bearing invariant above is untouched — `--affected`,
`--lf`, `--ff`, `-E` and explicit CLI paths still need no special case.

**Why `D` needed to become declarable.** `testpaths` answers *"where are the
tests?"*. The coverage audit was reading it as *"where does public API live?"*.
Those are different questions, and a project that declares both a test root and
a library root under `testpaths` — as oxitest itself does — cannot distinguish
them. Measured on `2247a508`, oxitest's own tree with the audit at
`scope = "public"` and no `skip`: **49** collection errors, every one a test
helper.

The audit could not be narrowed any other way. A list-form `scope` reaches the
right files and switches the module-path privacy filter off with them
(`src/doctest/subjects.rs`), so the same intent expressed as
`scope = ["python/oxitest/"]` produces **221** collection errors instead of
none. Scoping and privacy were mutually exclusive, and the `skip` entries this
project carried were not a workaround for one filter — they were the only way it
could state its intent at all.

**Why not derive `D` from packaging metadata.** A generator reading
`[tool.maturin] python-source`, setuptools `packages` or `[project]` would be
zero-config and correct by construction where it works. It was rejected because
it does not work here: this project's own `python-source = "python"` is the
**parent** of both `python/oxitest` and `python/tests`, so deriving from it
reproduces exactly the ambiguity being removed. A mechanism that silently does
nothing for the project that motivated it is worse than one line of config.

**Why `roots` is opt-in.** `docs/user/reference/stability.md` places every
`[tool.oxitest]` key documented in the reference under semver protection, and
`v4.0.0` shipped on 2026-08-09. Changing what `scope = "public"` covers is a
major-version change. So an unconfigured project keeps today's behaviour, and
the `conftest.py` exclusion in the subject enumeration **stays** as a migration
cushion for it — a project arriving from pytest still has a `conftest.py` full of
undocumented fixture functions, even though oxitest stopped loading the file in
[#1720](https://github.com/kalonji-tools/oxitest/issues/1720). That exclusion's
expiry condition is this key's default flipping, which waits for a major version.

**One call, still.** `roots` is resolved inside `collector::coverage_roots`,
which both the coverage walk and the staleness guard call. Resolving it at either
caller instead would let the two compute `D` separately — the "correct entry
reported stale" shape that reopened #1796 three times.
