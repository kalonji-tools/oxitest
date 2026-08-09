# ADR-0014: A Target that does not exist refuses the run

**Status:** Accepted
**Date:** 2026-08-09

A **Target** is a path, a directory, or a node ID given as a command-line argument. Before [#1797](https://github.com/kalonji-tools/oxitest/issues/1797), a Target that named something absent was silently accepted. Measured on `9e756053`, Linux x86_64, in a project with a `pyproject.toml`:

```
oxitest missing.py                     → exit 0, "no tests ran"
oxitest test_one.py::test_zzz          → exit 0, "no tests ran"
oxitest test_one.py missing.py         → exit 0, "2 passed"
```

The third line is the one that matters. A CI step that renames a test file and forgets one reference stays green while running a subset nobody chose. **A typo was indistinguishable from success by exit code.**

## The decision

**Exit 4 is defined by the class of the error, not by when it is detected.**

`docs/user/reference/exit-codes.md` previously read *"`UsageError` — oxitest exits before running any tests"*. That wording described where the existing sources happened to sit; it was not a chosen property. It is replaced by a statement about the class: the request itself was invalid.

**A Target that does not exist refuses the whole run.** Exit 4, no test executes, and every bad Target in the invocation is reported rather than only the first.

Two cases stay at exit 0:

- a **valid** Target that holds no tests — an empty directory, a non-test file, a run where `-E` deselected everything;
- a **glob** node ID that matches nothing, because a glob asks to match what is present. Only a *literal* Target asserts existence.

## Why not "warn and keep exit 0"

[#1797](https://github.com/kalonji-tools/oxitest/issues/1797) offered this as the conservative option, on the grounds that it *"preserves every current script"*. Measurement refuted that. With no rootdir anchor, `oxitest missing.py test_one.py test_two.py` exited **0 after running none of the three valid tests**: `find_rootdir` is seeded with the first positional path, and `start.is_file()` is false for an absent path, so it was treated as a directory and the rootdir silently relocated.

Warning and keeping 0 would have preserved a green run that tested nothing. It is not the conservative choice.

That is also why path validation runs **before** `find_rootdir` rather than inside collection: refusing early makes the relocated-rootdir state unreachable rather than merely masked. No separate guard was added at the inference site, because a guard for an unreachable state cannot be tested.

## Divergence from pytest, deliberately

This is the first question a reader familiar with pytest will ask, so it is recorded rather than left to be rediscovered. Measured with pytest 9.1.1 via `uv run --no-project --with pytest`, Linux x86_64, on the same projects:

| Invocation | pytest | oxitest after this ADR |
|---|---|---|
| `missing.py` | **5** (`NO_TESTS_COLLECTED`), no mention of the file | 4 |
| `no/such/dir` | 4 | 4 |
| `test_one.py::test_zzz` | 4 | 4 |
| `test_one.py missing.py` | **0**, "2 passed", no mention of the file | **4** |

#1797's body claimed *"pytest exits 4 here too"*. That is false for the first row and the last. pytest silently ignores an absent **file**, and in the mixed case it exits 0 exactly as oxitest did. **oxitest deliberately diverges**: matching pytest here would mean copying the same false-green.

The choice of code 4 therefore rests on two other facts, not on pytest: an invalid **flag** already exits 4 (measured), so a Target that does not exist is the same class of command-line mistake; and code 3 is documented as *"a test file could not be imported"*, which cannot describe a file that was never found.

## Two validation points

The two kinds of Target cannot be checked at the same time, and this is a property of the information rather than a design preference.

| Target kind | Checked | Why there |
|---|---|---|
| path, directory | before collection, ahead of `find_rootdir` | A filesystem test needs nothing else, and it must precede rootdir inference. |
| literal node ID | after collection (transition 6) | A node ID names a function inside a module that has to be imported first. |

Node-ID validation sits at transition 6 and nowhere else. Transition 10 filters again, but `apply_strict_mode` has run by then, so a Target whose only matching item strict mode dropped would be refused for the wrong reason.

## No "did you mean" suggestion

`format_fixture_errors` sets a convention: a name that is not found gets a suggestion at edit distance 2. This ADR does **not** follow it, for two measured reasons.

For **paths**, the mistake that motivated #1797 was `test_fixtures.py` for `test_fixtures_dsl.py` — edit distance **4**. A threshold of 2 would miss the very example that prompted the work, and a wider threshold has no evidence behind it.

For **node IDs**, candidates could only come from the collected items, and prescan has already dropped the named module by the time the check runs. In the common case of a single mistyped Target there are no candidates at all, so a suggestion would appear only when some *other* Target happened to keep that module loaded. A hint that fires unpredictably reads as "there is no near match", which is worse than offering none.

## Non-goals

**No distinct "collected zero tests" exit code.** pytest has code 5 for this; oxitest has none, so a valid-but-empty Target — `README.md`, an empty directory, a fully deselected run — stays indistinguishable from a successful run. Adding a code would change exit 0 for behaviour the documentation currently promises, which unlike this change is plausibly a major-version matter. This is recorded so the omission is discoverable as a decision rather than read as an oversight.

**A valid Target that holds no tests is not rejected.** Rejecting it needs a decision between "holds no tests today" and "can never hold tests", and that depends on the configurable `python_files` glob.

## Semver

Shipped as `fix:`, not held for v4.0.0.

`docs/user/reference/stability.md` protects the **set** of exit codes and their **meanings**. This change adds no code, removes none, and renumbers none — `4` still means a usage error. What changed is that a situation which was misclassified as success is now classified correctly.

Supporting measurement: a search of `python/`, `src/`, `justfile` and `.github/` for a reference to a deliberately absent Target returned **4** hits, all of them fixture *names* rather than Targets. Nothing in this repository depended on the previous behaviour.

The user-visible cost is stated rather than minimised: **a suite whose CI names a Target that no longer exists changes from green to red.** That is the purpose of the change.

## Consequences

- `CONTEXT.md` gains **Target** as a domain term.
- `exit-codes.md` gains a "Targets" section, and code 4's row names the new source.
- `src/types/exit.rs`'s doc comment is a third definition site for code 4 and is kept consistent with the reference.
- A pre-existing defect was found and deliberately left alone: `find_rootdir` returns an **empty** rootdir when a relative Target names a file directly in the working directory, because the file's parent is `""` and the `pyproject.toml` probe then resolves against the working directory. It is unrelated to this decision and is worked around only where a Target is spelled back to the user.
