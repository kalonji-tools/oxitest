---
name: verify-finding
description: Use when about to report, act on, or close an audit finding, review comment, or issue claim — before the claim becomes an input to a decision.
---

# Verify Finding

A finding has two separable parts: **the defect** and **the claims it makes
about itself**. They fail independently. A stale line number does not make the
defect imaginary.

## 1. Split the claims

List every factual claim: versions, paths, counts, API behaviour, "this is the
only site". Mark each **load-bearing** (the defect disappears if it is false) or
**incidental** (evidence, wording, line references).

## 2. Prove each one

Pick the command that answers *the claim*, not a neighbouring question.

| Claim about | Command | Not |
|---|---|---|
| released version | `git tag --sort=-v:refname \| head` · `gh release list -L 5` | `--sort=-creatordate` (surfaces `backup2`) |
| a pinned dependency | `cargo tree -i <crate>` · `grep <crate> Cargo.lock` | crates.io `max_version` — that is what is *published* |
| a published version exists | crates.io: the API **with a `User-Agent` header** (below) · PyPI: `.releases` from the same JSON · inside `devenv shell`, `cargo info <crate>@<ver>` | `cargo search --limit 1`, bare `cargo info <crate>`, `jq -r .info.version` — each answers what is *newest*, which is not evidence about any other version |
| a path exists | `rg --files \| rg <name>` | — |
| a string is absent | `rg`, **plus** read the emitting site — templates and wrapped lines defeat a rendered-text grep | `rg` alone |
| a diff size | `git diff --stat $(git merge-base origin/main HEAD)..HEAD` | bare `git diff --stat` (two-dot: #1840 read 902 phantom deletions) |
| runtime behaviour | minimal repro; paste output, **the OS**, and **N of M runs** | one green run |
| an issue's state | `gh issue list --state all --limit <N>`, filtered locally | `--search` — the index is asynchronous |

The crates.io API refuses a request that carries no `User-Agent` header, with a
data-access-policy error. Send one:

```bash
curl -s -H "User-Agent: <name> (<address>)" https://crates.io/api/v1/crates/<crate> \
  | jq -r '[.versions[] | select(.num == "<version>")]
           | if length == 0 then "absent" else "present yanked=\(.[0].yanked)" end'
curl -s https://pypi.org/pypi/<pkg>/json \
  | jq -r '.releases["<version>"]
           | if . == null then "absent" else "present yanked=\(map(.yanked) | any)" end'
```

Each answer separates three states that a newest-version query collapses into
one: **absent**, **present**, and **present but yanked**. A yanked version
exists and must not be pinned, so the existence answer alone is not enough to
act on. Do not filter the yanked ones out — that hides the third state behind
the first.

Every command must be **scoped so it cannot match your own scratch**. An
unscoped `rg` finds the finding that asserts the claim, and confirms it.

For any cached tool (`cargo clippy`, `cargo doc`, `just check`): say how you
know it **ran**. Green and ran are different claims — a cached clippy returned
0 where a forced rebuild found 11.

## 3. Check for reversal, not just contradiction

`docs/adr/` is a snapshot. Before citing an ADR, find the issue that closed
**last** on that decision. Note each ADR's scope: ADR-0005 binds Python only,
so it cannot contradict a claim about Rust.

## 4. Verdict per claim, then per finding

| Verdict | Means |
|---|---|
| `VERIFIED` | command output supports it |
| `REJECTED` | command output contradicts it |
| `UNREACHABLE` | the command **cannot see** the thing — branch protection, CI, a dependency's behaviour. Not the same as absent. |

Output a table: `Claim | Load-bearing? | Command | Actual | Verdict`.

Then the finding:

- Load-bearing claim `REJECTED` → **finding REJECTED**. Name the claim.
- Only incidental claims `REJECTED` → **RE-SCOPE**. The defect stands. Correct
  the characterisation and say what changed.
- Any load-bearing claim `UNREACHABLE` → **UNVERIFIED**, not rejected. Name the
  environment that could settle it.

## Red flags — you are about to destroy a real finding

- "The line number is wrong, so they did not really look."
- "It did not reproduce, so there is no bug." (0/N can be a precondition artefact — state N.)
- "`rg` found nothing, so it does not exist." (NO_COLOR works with zero grep hits.)
- "The ADR says otherwise." (Check what closed last.)

**Never REJECT on an incidental claim.** A rejection is silent and permanent; a
re-scope is recoverable.
