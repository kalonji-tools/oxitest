# ADR-0015: Releases are cut when earned, and deprecation waits for users

**Status:** Accepted
**Date:** 2026-08-09

On 2026-08-09, `main` had owed a major release for eight days and nothing said so. Measured on `ca24594f`, Linux x86_64:

```console
$ git-cliff --bumped-version
v4.0.0

$ git log --oneline v3.0.0..HEAD | grep -cE '!:'
5
```

All five are [#1788](https://github.com/kalonji-tools/oxitest/issues/1788)'s helper retirement, merged 2026-08-01. `release.yml` is `workflow_dispatch` with auto-bump, so the version is **computed by git-cliff and never chosen** — v4.0.0 was not a decision waiting to be taken, it was an answer waiting to be read.

Meanwhile [#1720](https://github.com/kalonji-tools/oxitest/issues/1720) sat blocked on "a major release" that nobody had scheduled, and the dependency graph reported it as clear because a semver gate is not an edge. An issue was filed to decide v4. **The thing that needed deciding was not the release.**

## The decision

### 1. A release is cut when it is earned, and the owed state is reported

The trigger stays manual — `release.yml` via `workflow_dispatch`, no `version_override`. What changes is that the owed state becomes visible: a scheduled job on `main` compares `git-cliff --bumped-version` against the latest tag and reports when a release is owed. It blocks nothing.

The failure this addresses was not a missing policy. Anyone could have cut v4.0.0 on any of those eight days. Nobody knew it was there.

This retires a class of blocker rather than one instance. An issue whose criterion is *"released in a major version"* is satisfied by whichever major follows it, so it does not need a release scheduled in advance and does not need a decision ticket of its own.

### 2. No deprecation windows or retirement schedules while the project has no users

A surface that is being removed is removed. It does not first spend a release in a Deprecated table with a named retirement version.

**The reason, which matters more than the rule.** This project has no users. Its one known downstream, `oxi-nixinfra`, consumes oxitest through an unpinned flake input:

```nix
# oxi-nixinfra/flake.nix:6
oxitest.url = "github:kalonji-tools/oxitest";
```

It tracks the default branch. **A consumer that tracks `main` cannot be protected by a version number in either direction** — it breaks on the day a change lands, not at a bump, and a deprecation window it never observes protects nothing. The mechanisms that do work for it are pinning the input or landing both repositories together, and neither is a versioning decision.

A deprecation window for an audience of zero is documentation that must be maintained, re-read and eventually actioned, and whose only measurable effect is to train readers that the table does not mean anything. The evidence is already here: the `## Deprecated` section was created by [#1949](https://github.com/kalonji-tools/oxitest/issues/1949), and neither of its two rows was ever actioned. In the same period, #1788 removed five surfaces without appearing in it at all.

### 3. This rule expires

**Rule 2 holds only while the project has no user that consumes releases.** The first such user ends it, and this ADR is superseded rather than reinterpreted.

The expiry is stated because the rule is conditional and reads as unconditional. A future maintainer finding "we do not deprecate" without the premise attached would apply it to a project that has since acquired users, which is the opposite of what was decided here.

## What this does not change

**Semver still binds.** Versions still mean what semver says, `stability.md`'s tiers stay accurate, and a breaking change still requires a major. Rule 2 removes the *ceremony* around removal, not the *contract* around versions.

That distinction is not cosmetic. `stability.md` is what made #1720's blocker visible in the first place — the page recorded that `Fixtures` was semver-protected, and that is why the retirement could not quietly ship in a minor. The discipline paid for itself at zero users, which is the argument for keeping the accurate half and dropping the performative half.

## Consequences

- The `Retired at` column leaves `stability.md`. The section becomes *Legacy — prefer the replacement*: it still enumerates superseded spellings, which is what made a contradiction between two documents findable, but it no longer names versions nothing will act on.
- **`fx.oxi.ctx` is deleted in the same release that establishes this policy**, without a deprecation period, while it is listed `Stable`. That is the sharpest application of Rule 2 and the one most likely to read as arbitrary later. It is recorded here deliberately: the premise is the user count, the replacements already exist and are documented (`oxi.current_test()` from a test, `ctx: TestContext` from a fixture), and the one known downstream uses it **zero** times.
- Two ADRs carried retirement claims keyed to a version. Both are amended rather than edited: ADR-0009's parked consequence at `:870` is discharged, and ADR-0012's `Patcher` question is re-keyed from *"the same v4.0.0 gate"* to its real precondition, *"when `Patcher.context()` ships ([#1696](https://github.com/kalonji-tools/oxitest/issues/1696))"*. A version-shaped trigger nobody re-reads becomes a condition that can be checked.
- A scheduled workflow is the repository's first `schedule:` trigger. Its schedule cannot fire until the file is on the default branch, so its first run is a `workflow_dispatch`.
