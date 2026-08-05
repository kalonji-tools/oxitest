# ADR-0011: No unhandled panic routes

**Status:** Accepted
**Date:** 2026-08-05

oxitest is a test runner. When it panics, the user does not get a failing test — they get a Rust backtrace where a report should have been, and a process exit code that means nothing to their CI. Every panic route on the shipping path is a place where the runner can stop being a runner.

[#1830](https://github.com/kalonji-tools/oxitest/issues/1830) closed the loudest of those routes by denying `clippy::panic`, and found zero shipping sites — the manifest comment describing that lint as a ratchet rather than a cleanup was accurate. But `panic!` is only the spelling nobody was using. Measured on `main` at `3fac34be` with a forced rebuild, the Rust source carried **17 `unwrap()`**, **15 `expect()`** and **28 `unreachable!()`** sites outside tests. Each is the same abort with a different name.

The interesting half of that set is not the careless half. Most of the `expect()` messages state a real invariant — `"session_ready phase"`, `"non-empty trail"`, `"serializable"` — and most of them are true. The problem is where the invariant lives. A message is a claim addressed to a human reader; it is not checked, it does not survive a refactor, and it is exactly as strong as the last person who read it. That is what this ADR is about.

> **An invariant lives in a *type*, not a macro.**

`Trail::current()` is the worked example. It read:

```rust
self.screens.last().expect("trail is never empty")
```

The comment was true and the code was still wrong, because `Vec<Screen>` can be empty and nothing but discipline said otherwise. Changing the field to `{ root: Screen, rest: Vec<Screen> }` makes the accessor total: it returns `&Screen`, no `Option`, no message, no panic route, and the invariant is now something the compiler enforces on every future edit. No lint annotation can do that.

## Considered Options

1. **Status quo — `panic` denied, the other three open.** Rejected: it makes the policy cosmetic. A mechanical `unwrap()` → `unreachable!()` rewrite satisfies the existing gate and changes nothing about what the binary does to a user. The lint set has to cover every spelling of "abort here" or it covers none of them.

2. **Deny `unwrap_used` and `expect_used`, audit the `expect` messages.** Keep `expect()` legal where the message states an invariant, and review the messages for accuracy. Rejected: this is the position the codebase was already in, informally, and it produced `Trail::current`. An accurate message is still a claim no tool checks. Auditing messages optimises the wrong artefact.

3. **Deny all four, permit per-site `#[expect(clippy::unwrap_used, reason = "...")]`.** Rejected: a per-site escape hatch is the message problem wearing a `reason` attribute. It costs one line at the moment of greatest temptation — the moment the author has decided restructuring is not worth it — so it would be reached for exactly when it should not be. It also makes the exception set unenumerable: you would have to grep to know how many carve-outs exist.

4. **Deny all four; exceptions are module-scoped, numbered, and listed here (chosen).** A stubborn single site has no escape: you restructure it, or you open an ADR. An exception must name a whole module, carry a `reason`, and appear in the exception list below with its real fix named. The list is short enough to read, and growing it costs a follow-up ADR.

## Decision

Option 4. The following lints are denied in `Cargo.toml`'s `[lints.clippy]` table, each with a comment pointing back at this ADR.

| Lint | Group | Denied by | Shipping sites at adoption |
|------|-------|-----------|----------------------------|
| `panic` | restriction | [#1830](https://github.com/kalonji-tools/oxitest/issues/1830) | 0 |
| `todo` | restriction | [#1830](https://github.com/kalonji-tools/oxitest/issues/1830) | 0 |
| `unimplemented` | restriction | [#1830](https://github.com/kalonji-tools/oxitest/issues/1830) | 0 |
| `exit` | restriction | [#1829](https://github.com/kalonji-tools/oxitest/issues/1829) | 0 |
| `unwrap_used` | restriction | [#1832](https://github.com/kalonji-tools/oxitest/issues/1832) | 17 |
| `expect_used` | restriction | [#1832](https://github.com/kalonji-tools/oxitest/issues/1832) | 15 |
| `unreachable` | restriction | [#1832](https://github.com/kalonji-tools/oxitest/issues/1832) | 28 (16 excepted, see E1) |

### Scope

**This ADR governs the panic-route lint set only — not the project's lint policy generally.** Per-site `#[expect(...)]` and `#[allow(...)]` annotations for unrelated lints stay legal and need no entry here. Three live at adoption time: `#[expect(clippy::use_self, ...)]` in `src/bridge.rs`, and `#[expect(dead_code, ...)]` in `src/config/mod.rs` and `src/config/cli/inspect.rs`.

**Scope is `src/` — the Rust crate.** Python's equivalent question belongs to [ADR-0007](0007-none-by-exception.md) plus the existing strict-mode machinery, which are governed separately.

**Tests are exempt for three of the seven**, via `clippy.toml`: `allow-panic-in-tests`, `allow-unwrap-in-tests`, `allow-expect-in-tests`. Clippy's full in-tests allowance list is `dbg`, `expect`, `indexing-slicing`, `large-stack-frames`, `panic`, `print`, `unwrap`, `useless-vec` — probed against clippy's own configuration keys, not assumed. **There is no `allow-unreachable-in-tests`**, so `unreachable!()` is denied in test code too. That is a real constraint on new tests, not an oversight: a test that reaches for `unreachable!()` is asserting something, and `assert!`/`panic!` say so with a message the runner can report.

### Rule 1 — No per-site carve-out

No `#[expect(clippy::unwrap_used, ...)]`, `#[expect(clippy::expect_used, ...)]`, `#[expect(clippy::unreachable, ...)]`, or the `allow` spelling of any of them, on a function, statement, or expression. A single site that resists the lint gets restructured. If restructuring is genuinely out of reach, the unit of exception is the module, and it goes through Rule 3.

### Rule 2 — Exception list

Exceptions are module-scoped `#![allow(...)]` at the top of a module, carrying a `reason`, and numbered here. The real fix — the thing that would let the exception be deleted — must be named.

#### E1 — `src/pipeline/transitions/**` · `clippy::unreachable` · 16 sites

`Pipeline` is a non-generic struct carrying one `phase: PipelinePhase` enum. Each transition method destructures the variant it expects and `unreachable!()`s otherwise:

```rust
let PipelinePhase::Ready { session, clean_items, .. } = self.phase
else { unreachable!("execute called outside Ready phase") };
```

This is a **runtime-checked typestate**. The `unreachable!()` is not covering an unhandled error — it is standing in for a compile-time guarantee the current type does not express. It cannot be plumbed away, because there is no error to plumb: the phase is correct by construction of the call chain, and the arm exists only because `PipelinePhase` is one type rather than several.

**Real fix:** restore compile-time typestate — `Pipeline<Ready>`, `Pipeline<Collected>`, and so on, where each transition consumes the state type it needs and no other state is constructible. Generic typestate was the original design and was replaced in [PR #1043](https://github.com/kalonji-tools/oxitest/pull/1043) with the runtime enum; re-introducing it is ADR-scale work with a wide blast radius across `src/pipeline/`, explicitly out of scope for [#1832](https://github.com/kalonji-tools/oxitest/issues/1832). When it lands, this allow is deleted, not amended.

**The carve-out is typestate-only.** A site inside the module that is *not* a phase destructure does not get to hide behind it. One such site existed at adoption — `session_ready.rs` re-destructured a variant it had just matched, an artefact of `into_parts()` — and was fixed rather than absorbed, so that the module allow means exactly what it says.

### Rule 3 — Growth process

A new exception requires a **follow-up ADR** that analyses three alternatives, following the precedent of [ADR-0005](0005-immutable-by-default-interfaces.md) and [ADR-0007](0007-none-by-exception.md). It must name the module, the lint, the site count, and the real fix. Adding an entry to the list above without that ADR is not permitted — the point of a numbered list is that each number cost something.

## Considered and excluded

Named here so that they are not re-litigated on every review.

### `clippy::indexing_slicing` — 496 shipping sites

Measured on `main` at `3fac34be` with the same deduplicated method as the counts above. Excluded on two grounds:

- **Different failure mode.** `a[i]` out of bounds is usually a genuine logic bug, not a swallowed error path. The panic is the correct behaviour for an impossible index; the bug is upstream of it. `unwrap()` on an `Option` is the opposite — it is a decision to discard a case the type says exists.
- **Signal-to-noise.** Adopting it means 496 sites, most of them `&items[..n]` after a length check or indexing a slice the loop bound came from. That volume would drown the 44 sites that matter and would make the eventual green gate meaningless.

Not "never" — but adopting it is its own ADR with its own measurement, not a rider on this one.

### `clippy::arithmetic_side_effects`

Not measured. Excluded for the same reasoning: an overflow panic in release-mode arithmetic is a logic bug, and adopting the lint would mean wrapping arithmetic in `checked_*` at every site including the obviously-safe ones.

## Consequences

- **New code cannot reach for `unwrap`, `expect`, or `unreachable!` on the shipping path.** The alternatives are: make the case impossible with a type, return a `Result` and plumb it, or handle the branch. All three are more work at the keystroke and less work at the bug report.
- **`unreachable!()` is denied in tests too.** Use `assert!`/`panic!` with a message. See the scope note above.
- **`std::fmt::Write` into a `String` never fails.** The largest single cluster fixed under this ADR was `writeln!(&mut string, ...).unwrap()`. Formatting into a `String` returns `Ok` unconditionally; the correct route is `let _ = writeln!(...)` or `push_str`, **not** a new `Result` in the signature. Introducing error plumbing for an error that cannot occur is the failure mode this ADR is most likely to cause.
- **Serialising a `#[derive(Serialize)]` struct with no maps keyed by non-strings also cannot fail** — but unlike `fmt`, the type system does not say so, so those sites return `Result` and plumb one level.
- **The exception list is the enforcement surface.** A reviewer checks whether a new `#![allow]` appears in this document. Anything not listed here is a policy violation regardless of how good the `reason` string is.
- **No new tooling.** Enforcement is `cargo clippy --all-targets` in `just check`, which the preflight gate already runs. Note that a cached clippy run proves nothing — force a rebuild (`touch src/lib.rs`) before believing a green result.
