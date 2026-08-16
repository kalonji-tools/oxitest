# ADR-0019: A test belongs to the band of what it starts

**Status:** Accepted
**Date:** 2026-08-16

oxitest holds about 1 778 Rust tests and about 2 045 Python tests. Until this record, nothing named a class of test. `CONTEXT.md` holds no entry for one. The only description is `docs/internals/src/testing.md`, which is prose, and which declares three classes of test. One of the three does not exist. It names an external `oxitest-consumer` repository, and `gh repo list kalonji-tools` returns five repositories that do not include it.

The effect of the missing vocabulary is measured, not supposed:

- The 21 `ignore:` entries in `codecov.yml` each say that a different class of test covers the excluded path. Nothing reads that claim. One entry names `python/oxitest/_bridge/import_graph.py`, which commit `5e75a5c3` deleted when a Rust module replaced it.
- `just mutate` reported 1 caught mutant of 29 viable on `src/reporter/print.rs`. The instrument and the code belong to different classes of test. No vocabulary could say so, so the number read as a weak test suite.
- The benchmark workflow finds a regression and cannot refuse one. `continue-on-error: true` discards the exit code of `benchmarks/compare.py`.

Map [#2156](https://github.com/kalonji-tools/oxitest/issues/2156) charted the effort. Seven tickets measured the suite and decided the questions. This record states the result and gives the work to the change pipeline.

## The word

Three obvious words were unavailable. Each collision is measured.

| Word | Meaning it already carries | Where |
|---|---|---|
| `tier` | a Fixture Lifetime scope, and separately a benchmark size class | `CONTEXT.md:94`, ADR-0009 (58 uses), four user documentation pages |
| `layer` | one `@parametrize` decorator in a composition stack | `CONTEXT.md:56` |
| `inprocess` | a builtin scheduling Mark — run on the main process, not on a worker | `src/filter.rs:28` |

`docs/internals/src/testing.md:3` uses `tier` for a class of test. That is a third meaning of one word in one repository, and it is the reason a reader cannot state which class of test owns a given test.

**Band** carries no other meaning here. This record uses it.

## The decision

> **A test belongs to the band of what it starts.**
>
> **A band is a class of test. The classifying axis is how much of the system is live. A test belongs to exactly one band.**

Counts below were measured by [#2162](https://github.com/kalonji-tools/oxitest/issues/2162) at `3daf8fa0`. They are stated to show the shape of the suite, not to be maintained by hand. The record described under [Two records](#two-records-and-what-each-one-keys-on) holds the current membership.

| Band | What is live | Members at `3daf8fa0` |
|---|---|---:|
| **Crate** | the Rust crate. No interpreter. | 1 773 `#[test]` |
| **Library** | the library in one process, both languages. No product process. | 1 485 tests and 53 doctests |
| **Command** | a product process. The CLI or the worker. | 528 tests |
| **Distribution** | the installed wheel, imported from outside the source tree. | **0** |

### The placement rule

Apply one question to one test: **what does this test start?**

1. The test starts no Python. → **Crate**
2. The test installs the wheel and imports it from outside the source tree. → **Distribution**
3. The test starts a product process. → **Command**
4. The test starts none of these. → **Library**

Read the rule in this order. A Distribution band test also starts the CLI after it installs the artifact, so step 2 comes before step 3.

**The unit is the test, not the file.** 26 files hold tests of two bands, so no rule can name one band for a file.

### There is no band between Crate and Library

`import oxitest` loads `oxitest._oxitest`. Every Library band test therefore has both languages live, and the sentence "both languages are live" separates no two tests. A Bridge Contract band cannot exist. The three files that looked like one are two Library band tests (`integration/test_marker_sync.py` and `test_bridge_contract.py`) and one Command band test (`test_check_bridge_sync.py`, which ADR-0018 put beside the script it covers).

### The axis is liveness, and a subject is not a liveness

Two candidate bands were refused, and the same measurement refused both.

| Candidate | Measurement | Result |
|---|---|---|
| a `docs` band | 110 of the 118 functions under `python/tests/docs/` start no product process | they are Library band tests |
| a `tooling` band | of its 9 files, 7 start a subprocess and 2 start nothing | they are 7 Command band files and 2 Library band files |

What holds each candidate together is its **subject** — published text in the first case, a repository script in the second. A subject does not change what a test starts. A subject becomes an attribute, not a band.

## What each band proves

| Band | The band proves | The standard | An exemption may say |
|---|---|---|---|
| **Crate** | the behaviour of a Rust item, with no interpreter live | mutation under `just mutate-rust`, where the mutated item returns a value that `cargo test` can read | "the only observation is a side effect" |
| **Library** | the public Python surface agrees with the published documents | the coverage report of the Library band | — |
| **Command** | the observable channels of the CLI: stdout, stderr, and the exit code | each test asserts on one channel or more, **and each `ExitCode` value has one test or more** | "the CLI cannot return this value" |
| **Distribution** | the artifact that goes out behaves as the tree that was tested | each artifact installs, and the import occurs outside the source tree | the band is one exemption. Its standard is met by the pre-upload gate in `publish.yml`, which [#2177](https://github.com/kalonji-tools/oxitest/issues/2177) built. |

### Crate

The Crate band is 1 778 `#[test]` inside `#[cfg(test)]` modules, measured at `50998e0c` by a grep for the attribute. The crate holds no `tests/` directory. Where a test sits is not band identity, so a `tests/` directory would move tests and change no band.

The band does not split further. Of the 1 773 measured at `3daf8fa0`, 1 602 touch neither Python nor the filesystem, 165 use `tempfile`, and **6** touch pyo3 or the GIL — 4 in `src/assert_rewriter.rs` and 2 in `src/prescan.rs`. Six members of 1 773 do not make a band. The 23 `Command::new` sites in `src/` start `git`, `true` and `cat`. Those are the environment, not the product.

`src/inspect/` holds 246 `#[test]` and is **unreachable from the Command band by construction**: `src/inspect/ui.rs:113` returns an error when stdout is not a terminal, and no `pty`, `openpty` or `pexpect` occurs anywhere in `python/tests`. `python/tests/integration/test_inspect.py` asserts the refusal and nothing more. This is a declared exemption with its reason, not a gap.

### Library

The Library band gets no positive obligation beyond its coverage report. "Every public symbol has a test" is the obvious candidate and is refused here: nothing in the repository derives the public symbol set, so that obligation is a gate, and this record builds no gate.

### Command — three exit codes are asserted by nothing

`src/types/exit.rs` declares five `ExitCode` values. The band's new standard bites at once.

| `ExitCode` | Value | Constructed in `src/` | Asserted by a Python test |
|---|---:|---:|---|
| `Success` | 0 | 31 | 25 sites |
| `Failure` | 1 | 20 | **none** |
| `Interrupted` | 2 | 6 | 2 sites |
| `CollectError` | 3 | 15 | **none** |
| `UsageError` | 4 | **42** | **none** |

Every site that asserts `returncode == 1` tests a script in `scripts/`, so it carries the `tooling` attribute and says nothing about the exit code of the product. The two sites that assert `== 2` say *"a failing suite exits 2"*; a failing suite exits 1, and 2 is `Interrupted`. Both statements are filed: [#2171](https://github.com/kalonji-tools/oxitest/issues/2171) for the two messages, and [#2172](https://github.com/kalonji-tools/oxitest/issues/2172) for the name `UsageError`, which is both the `ExitCode` value and a Python exception class raised at 8 sites — so a search for the name reports the exit code as tested. Both issues were open when this record was written. [#2171](https://github.com/kalonji-tools/oxitest/issues/2171) is an instrument that reports the wrong thing to a reader.

**#2172 is a product defect, and this record said otherwise.** The sentence here read *"Neither is a product defect"*, which repeated that issue's own framing of itself as a name collision. Measurement afterwards refuted it: a `UsageError` raised at startup exited 3, because five funnels in `src/pipeline/helpers.rs` substituted the exit code of the transition that caught the error for the class of the error itself. ADR-0014 fixes exit 4 by the class. The reproduction is a plugin that claims the reserved namespace, with a matched control, and it is recorded on #2172.

The correction is kept here rather than in a new record because the claim is a measurement this document made, not a decision it took. The band contract above is unaffected: `UsageError` still had no Command band test when this was written, and #2172 ships one.

### Distribution — the band whose members are jobs

**Amended by [#2177](https://github.com/kalonji-tools/oxitest/issues/2177).** The gate below is built. The band still holds **no collected test**, and that is its settled shape rather than a gap: the artifacts it examines do not exist until a tag is pushed, so no member of the suite can reach one. Its members are the 21 legs of `publish.yml`'s `gate` and `gate-sdist` jobs, plus the `wheel-manifest` job in `test.yml`. The paragraph below records what was true before that change; the two paragraphs after it record what is true now.

Before #2177, no CI job in this repository had ever imported oxitest from an artifact. `uv sync` writes `oxitest.pth`, which points at the source tree, and `RECORD` holds 13 lines and no package file. `publish.yml` built wheels, downloaded them, and uploaded them without an install. **The repository did describe two such imports and nothing ran them**: `flake.nix:23` runs `maturin build` and `flake.nix:41` installs the wheel with `python -m installer`, under `pythonImportsCheck`; `nix/package.nix` does the same and adds two `passthru.tests` that run the installed CLI. No workflow invokes either. The first version of this section said *"`maturin build` occurs nowhere, because every invocation is `maturin develop`"*, and that sentence was false when it was written.

The band proves the six properties that an editable install cannot reach:

| # | Property | Why an editable install cannot reach it |
|---|---|---|
| 1 | the file manifest of the wheel | the source tree always holds the files, and the wheel contents are never read |
| 2 | import when the source tree is absent | `oxitest.pth` points at the repository, so a repository-relative path survives |
| 3 | the `.dist-info` metadata: `requires-python`, classifiers, wheel tag | only an install of a true wheel applies them |
| 4 | the manylinux tag and the auditwheel repair | development builds are debug, unrepaired, and use the local libc |
| 5 | the `universal2` macOS artifact on both architectures | `test.yml` covers both architectures with `maturin develop`, not with that artifact |
| 6 | the sdist builds | it is built and uploaded, and never built from |

Two duties that `testing.md` gives this class of test are **already discharged** by the Command band, and they are removed from the contract. *"Testing CLI behaviour with real subprocess invocations against a real `pyproject.toml`"* is what every Command band test does, and *"regression testing across the Rust and Python boundary"* is what every Library band test does. To state covered work as the duty of an absent class of test is how the document came to describe a repository that nobody made.

**The gate refuses, and it runs before the upload.** PyPI does not permit a filename to be uploaded a second time, so a bad wheel is permanent. The gate is two jobs in `publish.yml`, between the build matrix and `publish`, and both are in the `needs:` list of `publish`. All 17 artifacts install, each on a runner that matches its tag, and every such runner already existed in `test.yml`. The `universal2` wheels install on both macOS runners, which is the only proof of property 5. The sdist gets a build, an import, and one CLI run — not the suite, which proves nothing the wheel path has not proved.

**No leg holds a list of the 17 filenames.** Each leg runs `pip install --no-index --only-binary :all: --find-links dist oxitest`, and pip refuses when no wheel matches that runner and that interpreter. The refusal *is* the coverage assertion, so the gate adds no platform literal and no interpreter literal that nothing compares. `--only-binary :all:` is what makes it an assertion: `dist/` holds the sdist beside the wheels, and without that flag a leg with no matching wheel builds from source and passes.

**`scripts/check_artifact.py` asserts the properties, and it reads the installed distribution.** `RECORD` is the file manifest, `WHEEL` holds `Tag:`, and `METADATA` holds `Requires-Python` and every `Classifier`. Reading the installed distribution rather than the wheel file removes the question of which wheel a leg owns, and it asserts what is on the disk. `importlib.metadata` drops a `RECORD` entry whose file is absent, so the manifest property asserts the file **is present**, which is the stronger of the two claims.

**A source-controlled subset also runs on a pull request.** Properties 1 and 2 are controlled by the source, so a pull request can break them; properties 3, 4 and 5 are controlled by the release matrix, so a pull request cannot. The pull-request check is therefore one debug `maturin build` on `ubuntu-latest`, installed into a clean environment, asserting properties 1 and 2. It calls the same script with a shorter `--properties` value, so it is a subset by argument and cannot drift into an assertion the release gate does not make.

**The `oxitest-consumer` repository is refused, not merely absent.** It would live outside `publish.yml` and could not see an artifact that is not yet published, so it cannot gate. The word *consumer* leaves the vocabulary.

**oxi-nixinfra is a true consumer and is not a canary of oxitest.** It exercises the plugin protocols from an installed PyPI wheel. Its `upstream-check.yml` failed all 6 runs from 2026-07-06 to 2026-08-10 on a missing `nix` binary, so `v3.0.0` and `v4.0.0` both shipped past an instrument that could not have said otherwise. A true incompatibility and that red look the same. Two conditions reopen the decision, and each is filed: [oxi-nixinfra#178](https://github.com/kalonji-tools/oxi-nixinfra/issues/178) for a green `test-python` job, and [oxi-nixinfra#179](https://github.com/kalonji-tools/oxi-nixinfra/issues/179) for a signal that reaches oxitest.

## Attributes

A band is a partition. An attribute is not. Three attributes exist, and each names a subject.

| Attribute | The test | Obligation |
|---|---|---|
| `documentation` | proves that published text is true | — |
| `regression` | names an issue in its docstring | — |
| `tooling` | covers a script in `scripts/` or `benchmarks/` | **a test with the `tooling` attribute makes its tool fail** |

The `tooling` obligation exists because a checker that never refuses is an instrument that cannot report its own silence. No coverage instrument reads `scripts/` today: `[tool.coverage.run] source = ["python/oxitest"]`, and the `rust` flag reads `src/` only. `scripts/` holds 9 files and 2 401 lines.

**There is no `smoke` attribute and no fast subset.** [#2159](https://github.com/kalonji-tools/oxitest/issues/2159) measured the case for one and refused it. The suite is two populations: 27.8 % of the cases hold 87.2 % of the serial time, and the median subprocess case costs 1 052 times the median in-process case. Against that, the four node identifiers that refuse mutant M4 all sit in the most expensive 6 % of the suite, at percentiles 94.6, 95.7, 98.9 and 99.2. Running only those four costs 4.13 s against 60.5 s for the whole suite — but that set is knowable only **after** a full run with the mutant applied. Every subset chosen by cost catches none of them. A cheap subset is nearly free and refuses nothing.

**A Mark cannot carry an attribute.** A Mark reaches no Rust test, and it needs an annotation on 2 045 Python tests. The forecast is measured rather than supposed: `pyproject.toml:182` declares the Marks `docs` and `slow`, and neither applies to a single collected test. `python -m oxitest -E "mark(slow)"` collects 0 items over the whole suite, because an `oxi_mark` in a `conftest.py` is ignored without a word — filed as [#2168](https://github.com/kalonji-tools/oxitest/issues/2168), which is open.

## Performance is measured by two instruments, and neither is a band

The bands classify tests. A benchmark run is not a test, and the axis does not reach it. The instruments are separated by what each one does with a result.

| Instrument | Event | Consequence | Baseline |
|---|---|---|---|
| **Performance Gate** | every pull request, behind the `dorny/paths-filter` that `test.yml` already uses | a required check. A regression stops the merge. | the merge-base, built and measured in the same job on the same runner |
| **Release Performance Report** | the tag push, and manual dispatch | advisory. It refuses nothing. | none |

**The Gate compares two coefficients, not four means.** Fitting each historical run's four serial means against the real item counts gives a startup floor and a per-test cost with R² of 0.9998 or better on all seven runs. The four gated sizes therefore carry two numbers between them, and four correlated means let an improvement in one hide a regression in another.

**The threshold is a multiple of the measured spread, with a percentage floor, applied to each coefficient separately.** This record fixes the rule. A **calibration run** — one revision measured twice in one job — fixes the constants, because the noise floor has never been measured: no two benchmark runs in the history of this repository share a commit, so the contribution of the runner is confounded with real change, and the 10 % threshold in use was never tested against a floor.

**The Gate costs no wall-clock time.** `Swatinem/rust-cache` keys on the lockfile and the toolchain, so a second revision recompiles the `oxitest` crate only. The budget is about 5 min 10 s against a pull-request critical path of 6 min 22 s that the macOS x86_64 and Windows legs already set.

**The Report stops measuring what the Gate measures.** It drops the synthetic sizes, keeps the dogfood arm as milliseconds per test rather than as a total, and makes the pytest comparison real. The speedup against pytest is the headline claim of this project, and no run has ever computed it: all six downloadable artifacts hold zero pytest commands.

`benchmarks/test_compare.py` holds 19 tests of the detector. They are ordinary tests, the placement rule places them, and they carry the `tooling` attribute — so the `tooling` obligation applies, and one of them must make `compare.py` refuse. Nothing runs them today. `testpaths` does not name `benchmarks/`, and a bare addition also collects 359 generated files, because `norecursedirs` does not hold `generated`.

Three live defects of the current workflow are filed as [#2166](https://github.com/kalonji-tools/oxitest/issues/2166), which is open: the corpus has not collected since 2026-08-10, the dogfood arm ran 19 min 28 s at `v4.0.0` and did not finish, and a missing baseline prints `No regression detected.`

## Two records, and what each one keys on

Membership is derived and committed. Each record has a different key, so they are two records and not one.

| Record | Keys on | The check refuses when |
|---|---|---|
| **the band record** | a test | the tree and the record disagree |
| **the obligation record** | a region of product code | a region is `unowned`, or the emitted `ignore:` differs from the committed one |

Both copy ADR-0018, which is Accepted, and its working precedent `scripts/wire_protocol.lock.json`: **the refusal is the enforcement, not the file.**

### The obligation record

`codecov.yml` today is a hand-written obligation record. It records which class of test covers a path, and nothing checks it, and it has drifted. The replacement holds five properties.

1. A region is **declared**, and the measurement refuses a disagreement. A measurement alone states a fact and cannot state a duty. A declaration alone is `codecov.yml` today.
2. A region has one of three states: **`measured`**; **`exempt`**, which names the instrument that is absent; **`unowned`**, which refuses. The third state is the gate.
3. `codecov.yml` `ignore:` is **emitted** from the record. `preflight` refuses when the committed file and the emitted file disagree. A dead entry becomes impossible, rather than corrected once.
4. The record lands seeded with the state of the tree today, and it **shrinks only**. An entry can leave. No entry can enter without a change to this record.
5. An exemption names the instrument that is absent, so a reader can tell an absent instrument from a clean result.

The seed is not the current `ignore:` list, because that list is false where it is checkable.

| Claim in `codecov.yml` | Measurement |
|---|---|
| `cargo llvm-cov` can never enter `src/pipeline/collection.rs` | **70** `#[test]` enter it. The claim is true of `collect_items()` and is applied to 1 550 product lines. |
| the excluded Rust files are unreachable from the crate | **96** `#[test]` sit inside the excluded set. The set that no in-crate test reaches is `src/pipeline/transitions/` — 1 266 lines and **0** tests, where [#2107](https://github.com/kalonji-tools/oxitest/issues/2107) put 52 of its 53 mutation misses. |
| the excluded Python modules are testable only through the integration tests | `_fixture_registry` is imported directly at **26** sites, `_fixture_session` at 20, `plugin_loader` at 16. |
| `worker.py` is untestable in process | It is **unmeasured**, not untestable: 0 % as `test.yml` runs it, and **80.5 %** with an absolute `COVERAGE_FILE`. The subprocess writes its data file into the test's temporary directory, and `combine()` never reads it. |

A smaller exclusion is not available on the Rust side. `#[coverage(off)]` is `error[E0658]` on `rustc 1.97.1`, which `rust-toolchain.toml` pins. The Python side has `# pragma: no cover`, and `python/oxitest/` already holds 3.

### Coverage reports, and the obligation record refuses

**Coverage becomes a report for each band. It refuses nothing.** The completeness of the obligation record refuses instead. Patch coverage stays as a second ratchet, because the floor below does not inflate it.

The reason is measured. A control of one file and one test, whose body is `assert True`, scores **24.1 %** against the denominator that codecov itself uses. oxitest runs its own test suite, so the harness executes product code that no test observes. A target of 80 % sits on a scale whose zero is 24.1 %.

### Mutation is judged by observability, not by a percentage

**A mutation verdict counts only when the instrument of the band can observe the mutated surface.**

`just mutate-rust` runs `cargo mutants`, which runs `cargo test`, so it measures the Crate band. `just mutate` takes the test command as an argument and falls back to `just test-python`, so it measures the Library band and the Command band together. The `mutate-rust` recipe already says as much in prose — *"MISSED below means no Rust unit test refuses the mutant. It does not mean nothing refuses it — this recipe runs cargo test only (#2113)."* This record makes that sentence the standard rather than a warning.

The `print.rs` result of 1 caught in 29 viable is therefore not a weak test suite. It is a Crate band verdict on code that only the Command band can observe.

## An instrument states its own silence

> **"Measured, and found nothing" and "did not measure" are different results. An instrument must not print the same sentence for both.**

This rule is stated once here because the map found the same shape four times, in four unrelated places:

| Instrument | What it prints when it measured nothing |
|---|---|
| `benchmarks/compare.py` with a missing baseline | `No regression detected.` |
| `oxi-nixinfra`'s `upstream-check.yml` with no `nix` binary | a red run that a true incompatibility also produces |
| `python/tests/test_worker_protocol.py:72,79,94` | `3 errors` and the sentence a genuinely broken build prints |
| `python/tests/test_lazy_collection.py:110` | a pass, with `has_dynamic_collection` hardwired to `false` |

The `exempt` state of the obligation record carries the rule into the record. An empty band carries it too: the Distribution band prints its count of 0 and names what discharges the duty today.

## The vocabulary `CONTEXT.md` gains

A missing term is a signal, and `docs/agents/domain.md` says so. `CONTEXT.md` gains a `## Test Bands` section with these entries.

- **Band** — a class of test. The classifying axis is how much of the system is live. A test belongs to exactly one band, and the unit is the test, not the file. Not to be confused with **Lifetime**/**Scope**, which `tier` names elsewhere in this glossary.
- **Crate band** — the test starts no Python.
- **Library band** — the test starts no product process. Both languages are live in one process, because `import oxitest` loads `oxitest._oxitest`.
- **Command band** — the test starts a product process, the CLI or the worker.
- **Distribution band** — the test installs the wheel and imports it from outside the source tree.
- **Attribute** — a property of a test that names its subject rather than its liveness. Cuts across the bands. Three exist: `documentation`, `regression`, `tooling`.
- **Specimen** — a test-shaped function that a band test writes into a project as input. No band collects a Specimen. 235 sit under `python/tests/data/`, which `norecursedirs` excludes, and they inflated every Python test count on map #2156 until an `ast` parse replaced the grep.
- **Performance Gate** — the instrument that refuses a change on a measured regression.
- **Release Performance Report** — the instrument that describes a release and refuses nothing.
- **Baseline** — the measurement an instrument compares against. For the Gate it is the merge-base, built in the same job. The Report has none.
- **Calibration run** — one revision measured twice in one job, which states the noise floor as a measurement instead of an assumption.

The word **tier** keeps its two existing meanings and gains no third. The word **consumer** leaves the vocabulary.

## The fate of `docs/internals/src/testing.md`

**The chapter keeps its how-to and loses its taxonomy.** It is neither replaced nor reduced to a pointer.

The chapter holds two kinds of content, and only one kind has failed. Its taxonomy is wrong: the opening sentence declares three tiers, one of which does not exist; the `## oxitest-consumer` section states a falsehood; `testing.md:3` is the site that gave `tier` its third meaning. Its how-to is correct and used: the anatomy of a Rust test, `TestItem::builder(...).arc()`, the helper import table, the `cargo insta` workflow, and the commands that run each part.

So:

1. The opening taxonomy paragraph and the whole `## oxitest-consumer` section are deleted. The chapter opens with a pointer to this record for band identity.
2. The `## Cross-language sync tests` closing paragraph is deleted. ADR-0018 replaced *"use a cross-language sync test whenever a constant must be identical on both sides"* with a rule that splits by what a check reads.
3. **Every count in the prose is deleted, and none is replaced with a fresh count.** Five counts were stale when the map found them: 56 test-module files against 89 measured, "roughly 50" top-level files against 154, "another 40" integration files against 58, 34 snapshot files against 54, and 24 snapshot assertions against 54. A count in prose has no gate and drifts by default. Counts live in the band record, which is emitted.

The chapter is then a guide to writing a test in this repository, and this record is the authority on which band owns one.

## Consequences

This record decides. It changes no test, moves no file, and builds no check. Each item below is a change, and each owes the pipeline in `CLAUDE.md` a spec, a draft pull request, a plan, a post-implementation review, mutation, and preflight.

- `CONTEXT.md` gains the section above.
- A script derives band membership and attribute membership for both languages into the committed **band record**, and a check refuses a disagreement with the tree.
- The **obligation record** lands seeded with the state of the tree, `codecov.yml` `ignore:` is emitted from it, and `preflight` refuses a disagreement.
- The codecov project target stops refusing, and becomes a report for each band. Patch coverage stays.
- `publish.yml` gains the Distribution gate, and `publish` gains it in `needs:`. A pull request gains the two source-controlled properties.
- `benchmarks.yml` splits into the Performance Gate and the Release Performance Report. The Gate becomes a required check on `main`. The calibration run fixes the threshold constants.
- `benchmarks/test_compare.py` runs in the suite, without collecting the 359 generated files.
- Each `ExitCode` value gains a Command band test, or an exemption that says the CLI cannot return it.
- `docs/internals/src/testing.md` loses its taxonomy and its counts.

A band contract already exists in this repository as prose. [#2158](https://github.com/kalonji-tools/oxitest/issues/2158) found three such comments; two hold at `50998e0c`. `python/tests/integration/test_doctest_coverage_config.py:221` says that a Rust unit test proves the promotion and that this test verifies the whole path. `src/pipeline/collection.rs:1635` says that the unit can assert only that the fold reads the declared set, and names the end-to-end test that asserts the rest. Each names its counterpart and states which half it owns. No gate reads a comment, and the third has already drifted out of the shape a citation could find. The band record is what makes that prose enforceable.

## What this record does not reach

- **The bands do not say whether a test is good.** [#2158](https://github.com/kalonji-tools/oxitest/issues/2158) measured that no cross-band duplicate pair survives red-teaming: the bands do not duplicate each other, they duplicate themselves, in 26 groups inside the Crate band and 3 inside the Python bands. Deduplication is a separate effort and is not a band question.
- **The Library band has no positive obligation** beyond its coverage report, for the reason stated above. When something derives the public symbol set, that decision can be revisited.
- **The `tooling` attribute has no coverage instrument.** The obligation is that a tool fails, not that a percentage is met. `scripts/` stays unmeasured on purpose until an instrument exists to measure it.
- **The threshold constants of the Performance Gate are not fixed here.** The rule is fixed; the calibration run fixes the numbers. To state a constant before the noise floor is measured repeats the mistake this record replaces.
- **No test moves.** Membership is derived from what a test starts, so a directory layout carries no meaning and needs no change to satisfy this record.
