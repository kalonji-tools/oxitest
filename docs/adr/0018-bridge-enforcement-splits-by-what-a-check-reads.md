# ADR-0018: Bridge enforcement splits by what a check reads

**Status:** Accepted
**Date:** 2026-08-12

The Rust and Python halves of the bridge agree by convention, not by a compiler. `CollectedItem` crosses through PyO3 `FromPyObject`, which resolves by attribute name at runtime. `WireResult` crosses as LDJSON, which serde deserializes by field name. Neither direction has a compile-time check, so both were guarded by comparing source text — in **two** places, written twice, at unequal strength.

`scripts/check_bridge_sync.py` ran six checks from a prek hook. `python/tests/test_bridge_contract.py` re-implemented three of them as tests. Nothing in either file mentioned the other.

## The measurement

Which of the two was stronger was never established. It was assumed, and the assumption was backwards.

Mutants applied to copies of the tree, Linux 6.18.41 x86_64, CPython 3.12.13, at `884d4640`:

| Mutant | Script | Test |
|---|---|---|
| Python-only field added to `CollectedItem` | `MISMATCH: Python-only fields: ['shard_id']` | **survived** |
| `kind` deleted from `CollectedItem` | `errors=1` | `errors=1` |
| Rust-only field added to `WireResult` | `errors=1` | no test exists |
| Rust-only field removed from `WireResult` | `errors=1` | no test exists |
| Rust-only reporter field renamed | `errors=1` | no test exists |

`test_collected_item_fields_match_rust` asserted `rust_fields <= python_attributes`. A subset holds when Python grows a field Rust does not have, so the test could not see that drift. The script compared both directions.

Both sides also hardcoded the *same* adapter for the same field. The test carried `python_fields | {"param_id"}`; the script carried an explicit `CollectedItem`/`param_id`/`kind` special case citing #1564. The duplication was not two independent checks. It was one check, copied, and degraded in the copy.

## The decision

> **Enforcement splits by what a check reads, not by which file it lives in.**

| Concern | Reads | Home |
|---|---|---|
| **Behaviour** — round trips, payload shape, unknown-field tolerance | a running program | `python/tests/test_bridge_contract.py` |
| **Source symmetry** — field-name lockstep | two files as text | `scripts/check_bridge_sync.py` |

### Source symmetry is a lint, not a safety guard

This is the part that decides where it belongs, so it is stated rather than assumed.

An unknown wire field is dropped **by design**. `extra_unknown_fields_are_ignored` in `src/worker_result/tests.rs` asserts it, and its message says why: *"a worker on a newer protocol would otherwise report every duration as 0"*. No wire type carries `#[serde(deny_unknown_fields)]`. Version skew is handled by `PROTOCOL_VERSION`, not by field-set equality.

So the check does not prevent breakage. It catches an author who changed one side and forgot the other, at the moment they can still fix it cheaply. That is a lint, and a lint belongs where it is cheap enough to run on every commit.

### Cheap enough means no build

The script is pure stdlib and runs in **33 ms**. The contract tests cannot run at all without a compiled extension: importing `oxitest._bridge.result` loads `oxitest/__init__.py`, which reaches `from oxitest._oxitest import trace` through `_fixture_decorator` → `_fixture_registry` → `_boundary`. A pre-commit hook that costs a `maturin develop` is a pre-commit hook people disable.

That asymmetry is the whole reason the two homes exist. Behaviour cannot move into the script, and source symmetry should not move into the tests.

## Consequences

- The four source-symmetry tests in `test_bridge_contract.py` and the third `PROTOCOL_VERSION` assertion in `test_check_protocol_version.py` are deleted. 26 tests to 22, and 8 to 7.
- `test_check_protocol_version.py` becomes `test_check_bridge_sync.py`. It already loaded the whole script and tested one of six checks; it now covers the extractors.
- Adding a bridge check means adding it to the script and a test for its **extractor**, not a second assertion of the same contract from the test suite.
- The script is now a single point of failure for source symmetry, which is the point. Its extractors are parsers over source text and can return a partial set that reads as agreement, so each one is pinned by a test against a fixture with a known field set.

### The version is part of the shape

Every check above compares **two files at one commit**, and that shape has a blind spot: a field added to *both* sides agrees with itself. Measured before the fix — `errors=0`, no output — while the protocol had silently changed.

`wire.rs` instructs: *"Bump when adding, removing, or changing fields in `WorkerTask` or `WireResult`."* Nothing enforced it.

The obvious fix is to diff against the previous commit, and it does not work here. The CI Quality job runs `prek run --all-files`, which has no base ref, so a diff-based check would run at `git commit` and not in CI — the shape of defect #1974, a gate installed into an environment it cannot execute in.

So the previous state becomes an ordinary file. `scripts/wire_protocol.lock.json` records the field sets the current `PROTOCOL_VERSION` describes, and `lock_report` holds one invariant: **the record equals the tree**. Two failures, deliberately distinct, because they are different mistakes with different fixes:

| State | Verdict |
|---|---|
| field sets moved, version unchanged | `unbumped` — the protocol changed silently |
| anything else differs | `stale` — the record was not regenerated after a real bump |

**The refusal is the enforcement, not the file.** A record that can always be regenerated is advisory: change the fields, regenerate, never bump. `--update-lock` refuses to write when the shape moved and the version did not, which leaves bumping as the only way forward.

## What this does not reach

`wire.rs` names `WorkerTask` **and** `WireResult`. The lock records `WireResult` and `WorkerTaskItem` — the two the script already extracts.

`WorkerTask` carries nine fields. `protocol_version` is covered by the version check. The other eight — `modules`, `fixture_modules`, `plugins`, `timeout_secs`, `keep_tmp`, `rootdir`, `show_locals`, `show_internals` — and every field of `WorkerTaskModule` have **no extractor**, so a change to those still passes every check including the lock.

That is a narrower gap in the same dimension, left open deliberately: closing it means three new extractors, and the measurement that justified the lock was taken on `WireResult`.
