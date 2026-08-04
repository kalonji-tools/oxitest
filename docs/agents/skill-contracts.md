# Skill Contracts

Where this repo overrides a mandated skill, and why.

> **Skills say WHAT. This repo says WHERE. State the deviation in the turn you make it.**

Every skill in `required-skills.txt` is maintained by someone else, and several make instructions this repo cannot follow. Neither side is wrong — an agent following a skill literally writes files this repo rejects; one following `CLAUDE.md` literally violates a skill's hard rule. This file records which way each conflict resolves, so the resolution stops being improvised once per session.

**Versions.** Quoted against `superpowers/5.0.6` and the standalone `improve` skill as installed on 2026-08-04. Quotes are **inline and verbatim**, never cited by line number: skills live outside this repo, upgrade independently, and no gate here covers them. A quote that no longer matches its skill is the signal to revisit the entry — not to trust the entry.

## Skill → cause

You hit these by skill name. They are written by cause, because one override usually governs several skills.

| Skill | Cause |
|---|---|
| `superpowers:writing-plans` | [A](#a--the-suite-assumes-a-committed-artifact-file), [E](#e--a-plan-is-a-lead-not-a-specification) |
| `superpowers:brainstorming` | [A](#a--the-suite-assumes-a-committed-artifact-file), [D](#d--brainstorming-finds-the-issue-through-the-branch-name) |
| `superpowers:subagent-driven-development` | [A](#a--the-suite-assumes-a-committed-artifact-file), [B](#b--the-execution-skills-require-a-worktree-skill-this-repo-does-not-install) |
| `superpowers:requesting-code-review` | [A](#a--the-suite-assumes-a-committed-artifact-file) |
| `superpowers:executing-plans` | [B](#b--the-execution-skills-require-a-worktree-skill-this-repo-does-not-install) |
| `improve` | [C](#c--improve-may-not-touch-source-stage-8-says-fix-it--unresolved) |

## A — The suite assumes a committed artifact file

**What the skills say.** `writing-plans`: *"**Save plans to:** `docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md`"*, then *"After saving and committing the plan file"*. `brainstorming`: write the spec to `docs/superpowers/specs/…` and *"Commit the design document to git"*. `subagent-driven-development` then reads that file back (`docs/superpowers/plans/feature-plan.md`), and `requesting-code-review` cites it as `PLAN_OR_REQUIREMENTS`.

**What this repo does.** The durable artifact is a **comment** — the spec on its issue (stage 4), the plan on its PR (stage 6). Writing the file is fine; committing it is not.

**Why.** [#1741](https://github.com/kalonji-tools/oxitest/pull/1741) proposed tracking agent notes in the repo and was closed unmerged. Tracked agent artifacts go stale against a codebase that moves faster than they do, and a stale committed plan reads as authoritative long after it stopped being true.

**Where it is enforced.** `.gitignore` — `docs/superpowers/` is ignored, so the skills' own save step succeeds and their commit step cannot. Also `plans/`, which `improve` writes to under cause C. Testable: `git add docs/superpowers/` is refused.

## B — The execution skills require a worktree skill this repo does not install

**What the skills say.** Both `executing-plans` and `subagent-driven-development` list *"**superpowers:using-git-worktrees** - REQUIRED: Set up isolated workspace before starting"*.

**What this repo does.** Worktrees come from **Worktrunk** (`wt`), documented in `CLAUDE.md` under *Tools*. `superpowers:using-git-worktrees` is absent from `required-skills.txt`, so `just agent-health` does not ask for it.

**Why.** `wt` carries this repo's hooks — `post-switch = "direnv reload"` and `pre-merge = "just preflight"`. A worktree created by any other means silently loses both, and the second one is a gate.

**Where it is enforced.** Nowhere mechanical. An agent that follows the skill literally will look for a skill that is not installed; the correct response is to use `wt` and say so.

## C — `improve` may not touch source; stage 8 says fix it — UNRESOLVED

**What the skill says.** Hard Rule 1: *"Never modify source code yourself… The ONLY files you may create or modify live under `plans/`"*. Hard Rule 2: *"Never run commands that mutate the user's working tree — no installs, no builds…, **no git commits**, no formatters."*

**What `CLAUDE.md` says.** Stage 8: *"Only fix after exploration confirms the finding is actionable"* — and the standing rule that review findings are **folded into the PR** rather than filed, which requires editing source and committing it.

**Status: unresolved, deliberately.** Confirmed by the maintainer on 2026-08-04 when this file was written — the contradiction stands and neither document is changed to remove it.

That is a decision, not an omission, and the difference is the same one this repo's enforcement tiers exist to preserve: a conflict nobody resolved must be distinguishable from a conflict nobody noticed. Resolving it would mean editing either a skill this repo does not own or a stage that earns its cost, and neither is worth doing to make a table look complete.

**What to do meanwhile.** Run `/improve` for its analysis, then apply fixes yourself, outside the skill's turn, and say in the PR that you did. That is what every run has improvised so far. It works, and its cost is that the skill's own report never reflects the fixes, so the PR comment is the only record.

## D — `brainstorming` finds the issue through the branch name

**What the skill says.** After writing the spec it will *"detect an associated issue number from the current branch name"* and post the spec there. The detection is one line:

```bash
ISSUE=$(git branch --show-current | grep -oP '(?<=#|issue-?|/)\d+' | head -1)
```

**What this repo does.** Branches are `<type>/<issue>-<slug>`, which the regex resolves correctly.

**The failure mode is `head -1`, not a missing number.** A branch with *no* number is handled well — the skill asks which issue to use. But a branch naming **two** issues silently keeps only the first, and this repo routinely uses those. Measured against the regex above:

| Branch | Resolves to | |
|---|---|---|
| `refactor/1777-process-lifetime-tier` | `1777` | correct |
| `docs/workflow-evals-remedies` | *(empty)* | safe — the skill asks |
| `fix/1863-1864-exitcode-ord-ctrf-name-docs` | `1863` | **#1864 silently gets no spec** |
| `docs/1882-1886-skill-contracts` | `1882` | **#1886 silently gets no spec** |

The last row is the branch this file was written on. On a multi-issue branch, post the second and later issues' spec sections by hand — stage 4 otherwise looks satisfied because a spec was written.

**Where it is enforced.** `CLAUDE.md` stage 5 states the convention. Nothing checks it, and nothing catches the `head -1` case.

## E — A plan is a lead, not a specification

**What the skill says.** `writing-plans`: *"Write comprehensive implementation plans assuming the engineer has zero context for our codebase and questionable taste. Document everything they need to know: which files to touch for each task, code, testing, docs they might need to check, how to test it."* That is a claim that the plan is complete.

**What this repo does.** Treats a plan's file lists, line anchors and counts as **leads**. Three clauses, each from a run where the plan was wrong:

| Clause | The failure behind it |
|---|---|
| Sweep beyond the list; where the plan and the code disagree, **the code wins**. | A single run produced seven wrong file anchors. |
| Plan scope is **per-region, not per-file**. When a review pass is expected to fire inside a file the plan touches, the plan says what may and may not change *within* that file. | A plan named out-of-scope *files* but was silent about other *sections of the same file* — which is exactly where the review finding landed, forcing an unplanned edit. |
| A plan's **verification steps are written against the plan's own output**, not against the pre-change file. | A plan asserted a `grep -c` would return `0`, while the replacement text the same plan specified contained that string as a deliberate negative reference. The check could never pass, in either direction. |

The third is not a sloppiness failure and is the reason this clause exists separately: the author wrote a correct-looking check against the file as they were reading it, and their own change made it unsatisfiable.

**A second override on the same axis: the repo dictates what a plan opens with.** `writing-plans` owns plan structure, and `CLAUDE.md` stage 6 requires every plan to begin with a **premise ledger** — the claims the work rests on, the ones that narrowed its scope, and the steps needing a buildable predecessor. That is `fold-in` tier: the plan format begins with it, so there is no separate step to skip.

**Where it is enforced.** `CLAUDE.md` stage 7 for plan authority, stage 6 for the ledger. Both point here. The authority rule binds the implementer consuming the plan; the ledger binds the author writing it.
