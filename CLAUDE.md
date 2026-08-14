# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

oxitest is a Python test runner rewritten in Rust. It exposes a Python API (fixture system, marks, builtins) implemented in `python/oxitest/_bridge/`, and a Rust core that orchestrates collection, scheduling, parallel execution, caching, and reporting. The two halves communicate via PyO3.

## Commands

```bash
# Enter development shell (provides cargo, python, maturin, just)
devenv shell

# Check all required tools are available
just health

# Check required agent skills are installed (warnings only)
just agent-health

# Build the Rust extension (required before running Python tests)
just build

# Run Python tests (no rebuild — build first if Rust changed)
just test-python

# Run a single Python test file
just test-python python/tests/test_fixture_registry.py

# Run Rust unit tests
just test-rust

# Run a single Rust test — NOT via `just test-rust`, whatever the argument
# shape. That recipe passes --unreferenced=reject, and any filtered run leaves
# the snapshots of the tests it skipped looking unreferenced, so it aborts
# after the tests you asked for have already passed.
cargo test --lib <test_name>

# Run all static checks (format, lint, clippy, spelling)
just check

# Format code and fix typos
just fmt

# Full pre-push gate — the `preflight` recipe in the justfile defines its
# phases; `just --list` shows only descriptions, not dependencies
just preflight

# Run one mutant end to end: apply, build, test, revert. Anchors are file
# paths, not inline text. Each terminal state has its own exit code — the
# justfile is authoritative for which.
just mutate <path> <old-anchor-file> <new-anchor-file> [test-cmd...]

# Run Rust mutants under cargo-mutants (#2072). Prefer this for Rust code that
# Rust unit tests observe: mutants come from the AST, so "the anchor never
# matched" — which reads exactly like SURVIVED — is not a possible state, and
# the scratch-tree copy means a dirty worktree is never destroyed. It runs
# `cargo test` only; the recipe's comment block says what a MISSED verdict
# covers (#2113). Always scope it; unfiltered is 3607 candidates.
just mutate-rust --file 'src/worker_result/*.rs'

# Clean build artifacts
just clean

# Show all available recipes
just
```

## Workflow

### Two rules that govern this section

**Arity — exactly one file defines each fact.** `CLAUDE.md` is that file *unless another consumer already owns it*. Where another consumer does own a fact, point at the live source instead of restating it: label values come from `gh label list`, gate definitions from the `justfile`, skill names from `docs/agents/required-skills.txt`, and the wire version from `PROTOCOL_VERSION`. A restatement is a copy, and copies drift — two of this file's recorded defects were restatements that went stale while the thing they described moved.

**A gate over these copies is refused, on measurement (#2124).** A search for a version literal cannot separate *"the version is 7"* from *"this changed at v3"*, and most matches are the second kind — including `CHANGELOG.md`, which git-cliff writes and nobody may edit by hand. Numeric literals need a second pattern that sees none of the first set, so one gate would not be enough either. Delete the copy instead: a literal that does not exist cannot go stale. The counts behind this refusal are dated on #2124; the reason is what belongs here. The defect had already recurred once (#1589, v2 against v3).

**Enforcement tiers — every obligation below declares one.** Prose an agent may silently skip is a legitimate choice, but it must be a *chosen* one, so that a step nobody performs can be told apart from a step nobody thought about.

| Tier | Mechanism | Use for |
|---|---|---|
| `gate` | `just` / CI / prek fails | machine-checkable facts |
| `artifact` | produces visible output — a PR checklist tick, a required citation | claims a reviewer must be able to audit |
| `fold-in` | the step ceases to exist as a separate action | always prefer this where it is available |
| prose | honor system, by design | everything else |

### Track A — the change pipeline (one change, linear)

```
  1       2       3       4        5        6         7         8        9       10
Grill → Issue → Triage → Spec → Draft PR → Plan → Implement → Review → Merge → Debrief
                          ▲
                          └─ a re-scoped or re-grilled issue re-enters here (Track B)
```

The numbers are the stages below. Rebase, preflight and waiting for CI are *inside* stage 9 — see its merge sequence.

**1. Grill new ideas.** Any new feature, concept, or design direction MUST be stress-tested against the existing domain model and documented decisions before anything is committed to. A defect whose mechanism is unestablished is not yet grillable. Establish the mechanism first; grilling remedies on an unverified diagnosis produces a decision tree rooted in a guess, and every option inherits the issue's framing. An issue that marks its own mechanism `unverified` is stating that this precondition fails.

The **user** invokes `grill-with-docs` — it is marked `disable-model-invocation`, so an agent cannot call it and this stage will otherwise read as skipped rather than as impossible. An agent that reaches this stage unaided runs `grilling` plus `domain-modeling` (which is what `grill-with-docs` does) and records in the issue that it did so, and that no user-driven grilling took place.

**2. Create issues.** Once an idea survives grilling and is deemed worth implementing, create GitHub issues. Every issue MUST state the "why" — why is this change needed? What problem does it solve? Organize into milestones if the work spans multiple issues.

**Check for a duplicate with `gh issue list --state all --limit <N>`, never `gh issue list --search`, and filter locally.** The list endpoint is strongly consistent; `--search` reads an asynchronously-populated index and returned **zero rows for an issue filed 33 minutes earlier** by a concurrent session, so #1970 was filed as a duplicate of #1969 and closed the same hour. The mechanism is unconfirmed — both forms return the row now, so the distinguishing experiment is gone — but the list endpoint is authoritative under either candidate. `--limit` is **not optional**: it defaults to **30** against this repo's 1205 issues, so omitting it reproduces the same silent false negative for any duplicate outside the newest 30. Raise `<N>` when the issue count approaches it.

**Not every finding becomes an issue, and the rule that decides has been re-derived 37 times (`artifact`).** Two rules point opposite ways. The workflow-evaluation series has its own trigger for filing a recurring finding; the standing instruction here is to re-scope one issue in place rather than split work into follow-up tickets. They do not conflict, because they key on different things:

| What you found | Disposition |
|---|---|
| Work discovered **inside the unit of work in hand** | Fold it in. No new issue. |
| A class an **existing issue already owns** | Re-scope that issue in place. This is what the duplicate check above is for. |
| A problem that recurs across **separate sessions** | File it. No single unit of work owns it, so nothing else can. |

The disposition leaves a mark, which is what makes this `artifact` rather than prose: a fold leaves the commit or comment that carried it, and a re-scope leaves the edited issue. Nothing distinguishes an unmarked fold from a finding somebody forgot. Rows 1 and 2 rest on six recorded maintainer decisions; **row 3 rests on none**, because the record holds no case where the maintainer was asked and chose to file (#2130).

At creation, apply exactly one **category** label, **one or more** `area:` labels, and one **triage state** label. Run `gh label list` for the current vocabulary — this file deliberately does not restate it, because the tracker cannot disagree with itself and a restatement can.

**3. Triage issues.** Every issue gets a **state label** reflecting its triage status. See `docs/agents/triage-labels.md` for the state vocabulary. Triage is also where `priority:` and `size:` are applied — they are judgements, not facts known at filing time, and a guessed `size: M` is worse than no label at all.

**4. Spec every issue.** By the time a PR is created, every issue in that PR MUST have a design spec — written when the issue is picked up or ahead of time, but never skipped. If no issue exists yet for the work being specced, create one first: every spec needs a home issue. Use the `superpowers:brainstorming` skill for spec design. Post each issue's spec section as a comment on that issue. When issues share a grouped spec, post only the section relevant to each issue — not the entire spec on every issue. The skill posts by reading the issue number out of a branch that stage 5 has not created yet; `docs/agents/skill-contracts.md`, cause D, says what to do instead.

**The spec opens with a claims audit** (`artifact` — the spec format begins with it, so there is no separate step to skip). Before writing the spec, re-verify the issue's own factual claims against the tree, and record them as a table: the claim, the verdict, and evidence of the kind the claim demands. An issue's framing is a premise, and this is the first stage that rests on it.

**The table's last row is mandatory, and it is about what the claims do not reach.** Name the set the issue's claims range over, and say what lies outside it. A bare `n/a` does not discharge it — the set has to be stated, because *"the claims range over the three tiers that set a node id; the other three are outside"* is what turns an omission into a visible gap. This is a row rather than a sentence because the sentence already exists and has been complied with through eleven recurrences: one audit returned twelve of twelve with zero findings while a maintainer's instruction to investigate the same issue against the codebase returned four, every one of them an omission. Verifying "three set sites" and asking "are three all of them" are different questions, and only the first is a claim.

Stage 4, rather than earlier, because it already produces a required comment — the audit rides an artifact that exists. In the batch that produced this clause, **three of three issues carried a false or overstated premise, each written days earlier by the same author now implementing them**, and four commands found all three before any design question was asked.

**5. Create a draft PR.** Open the draft PR *before* any implementation, so the approach can be reviewed early. GitHub requires at least one commit, so scaffold with an empty one and fold it away later:

```bash
git commit --allow-empty -m "chore: scaffold (#N)"
git push -u origin <branch>
gh pr create --draft --assignee @me --title "..." --body "..."
# the first real commit absorbs the scaffold — this rewrites an already-pushed
# commit, so the next push must be forced:
git commit --amend
git push --force-with-lease
```

**Concurrent sessions are detected, not prevented.** `.config/wt.toml`'s `pre-start` hook warns at `wt switch --create` when another branch or open PR already touches this branch's issue numbers. It is advisory and never blocks. **It cannot see triage-stage collisions** — those happen before a worktree exists, and two of the four recorded incidents were exactly that. Assume it covers implementation and merge, nothing earlier.

**Branch names are `<type>/<issue>-<slug>`** — `refactor/1777-process-lifetime-tier`, `fix/1863-1864-exitcode-ord-ctrf-name-docs`. This is not only style: `superpowers:brainstorming` reads the issue number out of the branch name to post its spec to the right issue. The ways that goes wrong are in `docs/agents/skill-contracts.md`, cause D.

`--force-with-lease` rather than `--force`: it refuses if the remote moved since your last fetch, so a force-push can never silently discard someone else's work.

Assignment is **folded into `gh pr create`** (`fold-in`) — there is no separate `gh pr edit --add-assignee` step left to forget. The previous separate step was skipped on 4/4 PRs in one session with nothing surfacing it.

**6. Plan before implementing.** Use the `superpowers:writing-plans` skill. Multiple issues can be grouped into one plan if they are tightly coupled or logically sequential. The plan MUST be posted as a comment on the PR — never on individual issues.

**The plan opens with a premise ledger** (`fold-in` — the plan format begins with it, so there is no separate step to skip). Four sections:

| Section | Contains |
|---|---|
| **Rests on** | premises the acceptance criteria depend on |
| **Narrowed by** | premises that removed an acceptance criterion, a gate, or a task |
| **Sequenced on** | steps whose predecessor must leave a buildable tree — empty is a legitimate answer, and asserts that every step stands alone |
| **Not reached by** | the case none of the premises above cover — name a **dimension the premise set never varied**, not another case inside one it already considered. Empty is a legitimate answer, and asserts you looked |

`Narrowed by` exists because a premise that *deletes* an acceptance criterion is invisible to a ledger scoped by acceptance criteria, and that is the shape of the worst defect in the series. `Not reached by` exists because the other three are each scoped to claims that **exist**, and nothing else evaluates the set for coverage — one branch's seven `Rests on` rows were each verified, each still true afterwards, and the change regressed every project in the one case none of them reached.

**"Dimension" is the operative word, and it is what makes the row work.** The rows that have changed the work named a variable the premises never moved — the lifetime tier a message's mechanism silently assumed, the execution mode every premise shared. The row that failed enumerated further entries inside a dimension already under consideration: it was filled in, honest, and measured, and the fatal case was a different one in a dimension nobody had thought to vary. So the question the row answers is not *"what other case is there?"* — which is discharged by naming **a** case — but *"what did every premise here hold constant without saying so?"*

**Every row carries evidence of the same kind as its claim.** Never a bare verdict — `Verified ✅` is free to write, `0/6` is not.

| Claim is about | Admissible | Also required | Not admissible |
|---|---|---|---|
| runtime behaviour | a command and its real output | **the environment it ran in** — OS, and any platform-dependent switch the claim turns on | a source quote, however exact — reading code is not running it |
| what a document says | the **whole quoted span** as it appears in your artifact | — | a prefix, a paraphrase, or a fragment proving only that the source discusses the topic |
| a measured quantity | the measurement, with the command that produced it | **the environment it was measured in** | a remembered or inherited figure |

The environment column exists because a ledger can be complete, every row measured, and still authorise a defect the measuring environment cannot express. Three probe results on PR #2002 were measured on Linux for a change about Windows; two defects reached CI, and neither is reachable on Linux by construction. The full suite, `just preflight`, ruff, ty, strict mkdocs and four killed mutants all passed over the first one.

The whole-span clause is not pedantry: a prefix-verified quote once passed its own ledger row and shipped fabricated, because the start of a quote is the part you remember correctly and the tail is where invention happens.

**A false premise blocks.** If an acceptance criterion changes, the issue is re-scoped and re-enters at stage 4 — the same re-entry point the diagram marks, reached from inside Track A rather than from a backlog sweep. Otherwise amend the plan and record the correction in its row. Never a silent default.

**7. Implement via subagents or inline.** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans`.

The plan is a **lead, not a specification** — its file lists, line anchors and counts have been wrong often enough that sweeping beyond them is the default, and where the plan and the code disagree the code wins. See `docs/agents/skill-contracts.md`, cause E.

When you dispatch, `docs/agents/dispatch-protocol.md` defines what a dispatched agent owes the rest of this pipeline — stage obligations, workspace isolation, citation scope, which gates it must not run, and its standing permission to refuse you. Every clause there was measured on a real wave. A dispatched agent inherits none of this pipeline's stages unless the prompt names them: in the run that measured it, stage 10 compliance was **0/4** (`artifact` — the prompt must name the stages it delegates).

**8. Post-implementation review.** After all plan tasks are implemented and pushed, run these passes before marking the PR ready:

- **Scope the diff against the merge-base, three-dot** — `git diff main...HEAD`, after a fetch. Two-dot compares *tips*, so anything merged to `main` since the branch point renders as a deletion by your branch; one review opened on a `main..HEAD` diff reporting **902 phantom deletions** across files the branch had never opened. The fetch matters for the second half of this rule: re-run any gate added to `main` since the branch point — a strict-docs gate once never ran on a branch until review caught it.
- **`ponytail:ponytail-review`** on the branch diff — hunt over-engineering, dead code, and unnecessary complexity. May be skipped on a single-commit PR touching no public surface, **provided the skip and its reason are recorded in the PR checklist** (`artifact`). No size threshold is set: the yield data is currently too thin to justify removing a gate, and the recorded skips are how that data gets collected.
- **`/improve branch`** — audit the branch changes for correctness, security, test coverage gaps, and tech debt.
- **Post every finding as a review thread** (`artifact` — the review index `just review-post` writes, which names the pass and counts its findings). No gate can supply the denominator: a pull request with zero threads satisfies `just merge-ready` and satisfies branch protection alike, which is how two pull requests in one wave posted their findings as ordinary comments (#2124). `just review-post <spec.json>` anchors each finding where it is, so a reader clicks to the code instead of hunting for a symbol named in a table. It validates the whole spec against the diff before writing anything: a finding outside every diff hunk, or in a file whose diff GitHub did not render, posts file-level; one in a file the branch never touched cannot be anchored by any API and becomes an issue citing `path:line@commit` plus a symbol, indexed from the review body. One finding is one thread is one disposition, even when it spans files — anchor at the primary site and list the rest in the body. **Everything the pass publishes — finding bodies, the review body, and every disposition reply — is written in ASD-STE100 Simplified Technical English**, in this file's and `CONTEXT.md`'s vocabulary (prose): one idea per sentence, active voice, one word one meaning, no metaphor or irony, and never a coined synonym for a term either file already defines. Evidence is exempt and stays verbatim — commands, outputs, error strings and quotes are copied, not rephrased. A finding is read by someone deciding whether to accept or refuse it, and a first pass at this shipped *"the irony is worth recording"* into a published thread. No gate can judge a sentence, so this is prose by construction rather than by omission.
- **Cross-reference the two passes before acting — for ordering, not just for overlap.** Findings that look unrelated can be sequenced: one pass once flagged four duplicated test harnesses while the other flagged a missing test, and the missing test would have been a *fifth* copy of the harness, so the deduplication had to land first. Neither pass can see that from inside itself. The half that **changes a verdict** goes in the affected thread's disposition reply — it is the reason for that disposition, and belongs where whoever clicks Resolve will read it. The half about **ordering** is a claim about the pass pair with no owning thread, and stays in the stage-8 comment.
- **Explore findings before acting.** Present findings to the user. For each finding, explore the cited code to verify it's real and determine if the fix is safe. Only fix after exploration confirms the finding is actionable. Never blindly apply review suggestions. **Every finding gets a recorded disposition** (`artifact` — the reply `just review-dispose` writes); the six verbs are unchanged and only `Fixed` is agent-resolvable, which that tool enforces rather than asks for. The count half **folds in** — the threads are the count, so there is no table to keep in step with a pass. The obligation used to be on the pass rather than on its findings, and one of three findings once vanished between the pass and the PR table while the only mention of that pass in the PR was a stale "not run" line. **Branch protection gates a thread being *resolved*, which is a different check** — a resolved thread can carry no reply at all, so it never enforces this obligation; see the merge sequence for what GitHub does bind. `scripts/check_review_threads.py` once checked it and #2072 deleted it on purpose, so a rebuild reopens a settled decision. **Re-query thread state at the merge trigger, and never carry a thread verdict forward:** two agents in one wave read `isResolved=false` with a correct query and reported merge blockers, and a later query found every thread resolved. That mechanism is unestablished and the reproduction is rare, so it is recorded here rather than built against.
- **Docs evaluation.** Check whether the changes affect user-facing documentation. Scan `docs/user/`, `docs/internals/`, `CONTEXT.md`, and error references for stale content. If docs need updating, fix them in the same PR — don't let stale docs ship.
- **Re-count the branch's emitting commits before leaving stage 8** (`artifact` — the count goes in the PR checklist). This is the last stage that creates commits, so a count made at stage 6 is stale by here. Only `feat:`/`fix:`/`perf:` reach the changelog — `cliff.toml` skips five types explicitly and `filter_commits = true` drops every type it does not name at all, `build:` among them. Count those commits against the number of user-visible changes; two entries for one change means the grouping is wrong however coherent each commit reads on its own. If they disagree, regroup **here**, not at the merge trigger. Folding a review fix as `fixup!` + `--autosquash` keeps the count where it was; a fresh `fix:` commit does not. **A branch that emits nothing cannot fail this check**, which is most of them — across a recent 40-commit window only 14 were `feat`/`fix`/`perf` — so on such a branch say the count is silent by construction rather than reading its silence as a pass.

**9. Merge rules.**

Three different operations in this stage get called "rebase" in ordinary speech. They are named separately here and used consistently throughout: **regroup** (rewriting your branch's own commits into coherent units), **rebase onto `main`** (merge-sequence step 1), and **`--rebase` merge** (the GitHub merge strategy).

- **Never push directly to main.** All changes go through pull requests.
- **Never merge without approval.** Wait for either a GitHub review approval or an explicit user command (e.g., "merge", "merge rebase delete branch"). Do not auto-merge after CI passes.
- Only `--rebase` merge is allowed. Never squash merge, never merge commits.
- Every commit message title MUST include its related issue number: `feat: add Foo (#42)`
- Multiple issues per commit are fine: `feat: add Bar and Baz (#43, #44)`
- **A pull request title carries the issue numbers that PR closes**, same form as a commit title: `feat: add Foo (#42)`, or `feat: add Bar and Baz (#43, #44)`. `just merge-ready` compares this against the closures GitHub actually parsed, so a title that omits an issue the PR closes refuses the merge. Fix the title, not the check. Dependabot PRs name no issue and close none, so they match trivially.
- **PR closing keywords**: GitHub requires the keyword before EACH issue number. Write `Closes #1, Closes #2, Closes #3` — NOT `Closes #1, #2, #3` (only the first gets closed). **The inverse fails silently and is the worse half**: GitHub's parser does not read negation, so a sentence disclaiming an issue still closes it. Write `does not address #N` — never `does not fix/close/resolve #N`. A bare `#N` with no keyword before it is an ordinary link and is safe to cite. `just merge-ready` checks this, because the wording rule cannot help a document that must quote the bad form in order to explain it — the pull request fixing this defect reproduced it that way at creation.
- Run `just preflight` before pushing.

**Commit regroup (`artifact`).** At the **first push of a multi-commit branch**, and again when stage 8 closes, regroup into coherent commits — or record in the PR why the existing grouping is already coherent. Either way it leaves a mark: a tick saying "already coherent" is a legitimate outcome, an absent tick is not.

*Not* at merge trigger, which is too late — by then the throwaway history is pushed and CI has run on it, and one branch paid a full squash → preflight → push → wait-for-CI → merge cycle to undo that. The step leaves a mark because it has been skipped silently, reported as done when it was not, and argued against with a false claim that the tooling made it impossible.

"Coherent" is a judgement, so it carries two checkable clauses:

1. **No commit that a `Sequenced on` row declared non-buildable may survive into merged history.**
2. **The branch emits one changelog entry per change** — counted when stage 8 closes, and re-counted here only if the branch has gained commits since. One branch was emitting two `fix:` entries for a single change; 4 commits → 2 made it one.

Clause 2 exists because clause 1 cannot fail in the case that keeps recurring — a history whose every commit is individually coherent and whose *count against the unit of work* is still wrong.

The tooling is available, contrary to that claim:

```bash
BASE=$(git rev-parse HEAD)                # 1. capture AFTER everything you intend to keep is committed
git branch -f backup/<slug> "$BASE"       #    a named ref, so the safety net survives the shell

git reset --soft HEAD~N && git commit     # collapse the last N into one
git rebase --onto <base> <old-parent> <branch>   # move a middle commit
GIT_SEQUENCE_EDITOR=true git rebase --autosquash -i <base>   # fold fixup! commits

git diff --quiet "$BASE" HEAD             # 2. empty ⇒ nothing lost or gained
git diff --stat "$(git merge-base origin/main HEAD)"..HEAD   # 3. read the FILE LIST against the set you meant to touch
```

`git rebase -i` works here **provided `GIT_SEQUENCE_EDITOR` is set** — it is only the interactive editor that is unavailable, not the command, so `--autosquash` is usable too. The bracketing pair — 1 and 2 — is the point: tree equality proves the regroup preserved content whatever the commits became, which means every gate result from before the regroup still applies afterwards. The baseline is **captured, not chosen** — a ref taken before the edits predates the content it protects, so a *correct* regroup reports `TREE DIFFERS`, presentationally identical to one that lost work.

**Check 2 and check 3 answer different questions, and check 2 is blind to what check 3 catches.** Check 2 compares the branch to a backup of *the same branch*, so content the branch acquired from outside its own commits is present in both refs and cancels out — it reports `TREE IDENTICAL`, correctly, while carrying the defect. Check 3 compares against `main`, where such content has nowhere to hide. This is not hypothetical: `2d7186cf`, a change to fixture dispatch phases, also reverted two merged Dependabot bumps — `taiki-e/install-action` in four workflows and `flake.lock`'s nixpkgs revision — and it merged with every gate green. `git diff --quiet backup 2d7186cf` exits **0**; check 3 lists **fourteen** files of which five belong to no fixture change (#2128).

Check 3 returns no verdict of its own. **You** compare its file list to the set the branch meant to touch, so name that set before you read the output — the check gets weaker as the branch gets longer, and an unfamiliar path in a hundred-file list reads as ordinary.

**Disposition at close (`artifact`).** A pull request that closes an issue leaves a **disposition table** on that issue before the merge: one row per acceptance criterion, naming where each went — shipped, discharged by another issue, filed as a new one, or ruled out of scope. The table carries the literal marker `<!-- disposition -->`, and `just merge-ready` refuses the merge without it.

The gate reads **presence only** and never the rows. An author who writes a dishonest table is not a problem a gate solves; an author who never writes one is, and that is what this catches. The marker counts only where it **renders as nothing** — inside a code fence or a code span it is a display of the convention rather than a use of it, which is what stops the gate from passing its own documentation.

The test an author applies: **does every acceptance criterion have an owner after this merge?**

This is an obligation at close time rather than a detector afterwards because every detector built for it failed (#2057). Unticked checkboxes fire on **70%** of completed issues and reach neither known orphan — one of them has no checkboxes at all. Deferral vocabulary reaches **0** of the two, because a spec's non-goals use the same words. "Referenced by an open issue" scored 8.7% and its recall was an artefact of a person having already noticed. The information exists unambiguously at exactly one moment — when the author closes the issue knowing what they did not ship — so the obligation sits there.

**Merge sequence** — this order, every time:

1. rebase onto latest `main`;
2. re-run `just preflight` **after** rebasing onto `main` — even if CI was green before it;
3. push; wait for CI green;
4. `just merge-ready` — refuse on an unnamed closure or a missing disposition table;
5. `gh pr merge --rebase`.

Step 4 sits here rather than first so it reads the state that will actually be merged: run before the rebase, it can be true when it runs and false at merge. It is also **not** folded into `preflight` at step 2, which happens before a closing set or a disposition table is settled — gating preflight on either would block every branch mid-pipeline.

**Step 4 is the only thing checking either invariant, and nothing forces you to run it.** A CI context carrying these two checks was built and removed on #2072 rather than shipped. The reason is worth keeping: the disposition convention was one day old at the time, and promoting a convention with no operational history into a repo-wide merge blocker commits to it before it has earned that. Revisit once the convention has a record. Until then this step is discipline, and it is honest about being discipline.

**Never pass `--admin`** (#2072). `main` required one approving review that a solo author cannot obtain, so every merge used to bypass branch protection — and admin bypass is not selective. It discarded `required_conversation_resolution` and all three required contexts along with the review. `required_approving_review_count` is now `0`, so `gh pr merge --rebase` succeeds on its own, and the four protections that were collateral damage now bind. An unresolved review thread blocks the merge because GitHub blocks it, not because a script ran.

**"CI green" means the required contexts, not every check.** The required set is defined by branch protection — query it (`gh api repos/{owner}/{repo}/branches/main/protection`) rather than trusting a remembered list, because a copy here would drift. A red **non-required** check is not a merge blocker — and is not thereby uninteresting. Say so in the debrief, and say what it was pointing at: across five recorded instances it was pointing at something real four times. Do not make a coverage check green by measuring less — widening an `ignore:` list over untested code is the recorded anti-pattern, not a fix. An issue whose acceptance criterion promotes a job into a required rollup states the observed pass rate it is promoting on, and names any known flake in that job's path — the number gets stated, not a bar set.

**An instruction that arrives mid-gate wins — and you report what the gate had reached.** Do what you are asked, and in the same reply state exactly what had and had not been verified when you stopped: which checks completed, which never ran. The failure this prevents is not disobedience, it is silently converting an instruction into a claim that the gate passed.

If the instruction is a **waiver** — *"skip preflight, there's no logical change"* — its reason is a premise. Amend the plan comment's ledger with a row for it — the evidence that made it true — and re-check that evidence before relying on it: one branch acquired a logical change *after* the waiver was granted, so the waiver was correct when given and false when used.

**A gate that was already failing before your change is the same shape**, and no instruction arrives to mark it — a plan's Task 0 said *"Expected: all green"*, and the tree was red on an untouched checkout. Record it in the ledger with the evidence that it is pre-existing: the failing test named, and a run on a tree with zero tracked modifications. State both what **voids** the row — a second, different failure — and what does **not discharge** it: the failure ceasing to occur, which at any rate below 100% is expected. That second half is the one that gets forgotten; three consecutive green preflights against a ~60–80% failure rate nearly read as evidence the problem was gone.

**A cross-cutting change must be re-verified against a freshly-rebased branch.** CI builds the *merge commit*, so a rename or a vocabulary change is broken by construction by anything that lands on `main` meanwhile — and every local gate stays green throughout, because locally the two halves never meet. This is a trigger for merge-sequence step 2, not a new step: for any rename, vocabulary change, or branch left open more than a day, rebase onto latest `main` and re-run the gate *before* requesting merge rather than discovering it in CI.

`.config/wt.toml` sets `pre-merge = "just preflight"`, so step 2 happens automatically for `wt merge` and is **bypassed by `gh pr merge`**. That asymmetry is why the sequence is written here rather than assumed.

**Run step 2 alone. Two concurrent `just preflight` runs corrupt each other** (#1827) — and the failure lies about its cause, which is the only reason this note exists. It arrives as test failures attached to whichever branch lost the race, so it reads as "your branch broke something" and the honest response is a debugging session that finds nothing. Seen twice: `68 failed · 1481 passed` against `1549 passed` alone, and later a lone 30-second timeout against a lane that passed in 17 s alone.

**The mechanism is unknown**, and three candidates are eliminated, so do not re-derive them: CPU starvation (44 busy loops on 22 cores, load 62 → still green), cache-derived timeouts (`resolve_timeout` floors at the global timeout, so every test has ≥30 s), and the shared `uv` cache lock (tests spawn `sys.executable -m oxitest`, never `uv run`). Reproduction is roughly 1 in 10 — see #1827 for the probe designs, including the barrier that overlaps *test* phases rather than builds.

If you need the gate while someone else is running it, the individual phases have never been observed to collide: `just check && just test-rust && just build && just test-python`.

**Never pass `--delete-branch`.** `main` is pinned to the primary worktree, so the merge lands but local cleanup cannot succeed — it failed 4/4 times in one session. Do the cleanup directly instead:

```bash
gh pr merge --rebase
git push origin --delete <branch>
git -C <primary-worktree> pull --ff-only
wt remove <branch> -D --foreground --yes -C <primary-worktree>
```

`--yes` is not optional here: `wt` prompts for approval, and without it the command fails outright in a non-interactive session — in the very block offered as the workaround for a known trap.

**10. Post-merge debrief.** After a PR is merged, if the implementation diverged from the plan, add a debrief comment to the closed PR explaining how, where, and why it diverged. Apply the `diverged-from-plan` label to the PR. This label is only applied to closed/merged PRs.

### Track B — backlog maintenance (whole backlog, cyclical)

Track A is per-change and linear. Backlog maintenance — triage sweeps, relevance audits, re-grilling existing issues — runs over the whole backlog at once and produces no merge. It is a **separate track, not a stage**, and it joins Track A at Spec when an issue is re-scoped.

```
Sweep → Verdict → Disposition ─→ re-scoped issue re-enters Track A at Spec
```

**Issues rot three ways, and only one of them justifies closing:**

| Rot mode | What is stale | Disposition | Evidence required |
|---|---|---|---|
| (a) the defect is fixed | the issue itself | **close** | the citation in "Evidence for analysis outputs" below, **plus the premises the verdict rests on** (`artifact`) |
| (b) the defect stands, its *characterisation* is stale | the description | **re-scope** | comment recording what changed and why this is not a close (`artifact`) |
| (c) the defect stands, its *vocabulary* names deleted concepts | the wording | **re-word** | comment mapping old term → current term (`artifact`) |

The default disposition for a stale-*looking* issue is correct it, not close it. In the audit that produced this section, ~30 issues were reviewed: **0 were closeable and 8+ needed correction.** A "close what looks stale" pass would have destroyed information in eight places.

Only `close` lists its premises. `re-scope` and `re-word` are recoverable — `close` is the one this file already calls *silent and permanent*, and the one whose verdict has been reached on evidence that measured a different thing.

Re-prioritising needs no evidence — `priority:` and `size:` are judgements (prose).

**The triage-state label is different (`artifact`).** It is the pipeline's routing decision, so a flip to `ready-for-agent` must show that each blocker its triage named has cleared. One issue was `ready-for-human` for two stated reasons; the first cleared, the label was flipped with no comment, and the second — an undecided consumer-visible shape — was silently implied to have cleared with it.

**Orphaned remainders (`artifact`).** Sweep for closed issues that an open issue's reference is the sole record of, and review each by hand. Scoped to the backlog closed **before** the disposition gate shipped (#2057): every recorded instance lives there, and after the gate the artifact is the record. Each entry names the closed issue, the obligation it left, and the owner it now has.

This half stays human, deliberately. Automating it means automating the weakest signal measured on #2057 — "referenced by at least one open issue" matched 8.7% of the closed backlog, and every match was downstream of a person having already noticed — and a report that is mostly noise trains its readers to skip it. Two orphans were found this way in one sweep, which is also the only mechanism in this repo's history that has ever found one.

### Evidence for analysis outputs (`artifact`)

The pipeline gates code. This gates *conclusions*.

**The rule fires on any claim whose acceptance subtracts work or a gate** — on consequence, not on wording. A wording trigger is evaded the moment someone writes "appears resolved" instead of "no longer reproduces".

| Claim **adds** work | Claim **subtracts** work |
|---|---|
| "this looks wrong", "missing a test", "possible bug here" | "no longer reproduces", "this issue is stale", "clippy is green", "this file is unused" |
| Wrong ⇒ someone investigates and finds nothing. Self-correcting. | Wrong ⇒ information is destroyed and nothing looks again. Silent and permanent. |
| **no citation needed** | **citation required** |

**Direction is the common case, not the rule.** The rule is *consequence*, and a claim that becomes an input to a decision is load-bearing whichever column it falls in. Three kinds slip through the table above: a claim that **specifies the verification itself** (which mutant, which command, which assert fails) — get it wrong and the test it prescribes is vacuous while reading as coverage; a claim that merely **characterises current behaviour** and then becomes spec input; and a claim **inherited** from an issue or spec written days earlier — that last one is what stage 4's claims audit exists to catch.

**A fourth kind hedges instead of asserting** — a risk, a caveat, a "may". It adds no work and subtracts none, so the table above never fires on it, and no ledger section reaches it either. Its obligation scales with where it lands: a risk in a session's prose is disposable, but one written into an issue body or a PR description is a premise for the next reader and must carry either the command that established it or an explicit `unverified` marker. A false risk once reached a spec, an issue body and a PR description; by the time it was falsified two of the three were uncorrectable, because PR descriptions freeze at merge and the issue body is the spec of record.

**A premise is any claim a later stage will rest on.** Whatever produced it — triage brief, spec, plan, review finding, debrief — its premises are bound by this rule. Deliberately not a list of stages: an earlier version of this section named stage 4 and missed stage 3, where the worst defect in the series was authored. Premises are checked where the work is: at **stage 4** in the spec's claims audit for the issue's own claims, at **stage 6** in the plan's ledger for everything the plan rests on, and at the disposition comment for a Track B `close`. Those are checkpoints, not the rule's scope — the rule binds every premise wherever it was authored, which is why it is not written as a list of stages.

**A decision that subtracts scope must name the set it enumerated over.** This is the obligation below applied to the *enumeration* rather than to the *evidence*. One branch's "fix site 1 only" subtracted two fixes on the strength of a grep over appender sites, and the set that actually needed enumerating — registration positions reachable from the public API — was never stated. An enumeration nobody wrote down cannot be checked, and reads afterwards exactly like one that was complete.

A subtracting claim MUST carry:

1. the **exact command** re-run, and its output;
2. evidence the command is **the one the claim is about** — one real verdict cited `just check` against an issue whose reproduction used a different clippy invocation, and so measured a different thing. That particular gap was later closed in #1815; the lesson is the mismatch, not the command. The command must also be **scoped so it cannot match your own scratch** — an unscoped `grep` over the worktree finds the spec asserting the claim and confirms it, and the hazard is not `grep`'s: any full-command-line matcher sees the shell that launched it, so `pgrep -f` and `pkill -f` match themselves. Both killed the shell that launched them, exit 144, twice in one session;
3. evidence the run **executed** rather than replaying a cache — a cached `cargo clippy` once returned 0 where a forced rebuild found 11. "Green" and "ran" are different claims.

This is `artifact` tier: it binds when someone reads the comment. Its value is that the omission becomes visible — a missing quote is the tell — where today there is nothing to look for.

**A citation must survive the merge it describes.** Issue comments are this repo's home for specs and research, so its most durable records carry its weakest referential integrity: a bare `path:line` rots the moment the branch it describes lands. One issue's own merge broke the citations in its comments: a cited `drain.rs:42-44` had become `));`/`}`. Cite a **symbol**, or `path:line@commit`, or quote the excerpt inline so the citation carries its own evidence. A quote rots on **content** and fails loudly (zero hits); a bare line number rots on **position** and fails silently. Never cite `CLAUDE.md` by bare line number — it is a repeating structure of tables and clauses that this repo edits continuously, so a stale line lands on plausible neighbouring prose.

### Believing a verdict (`artifact`)

**"Printed something friendly" ≠ "did the thing".** A command can report success without having executed, and ten distinct mechanisms for it have been observed in this repo — so this is stated as an invariant rather than as a list of traps to memorise, because the list has been outgrown ten times.

Before believing any verdict:

1. **Verify an operation by querying the resulting state**, not by reading its output. `gh pr view --json state,mergedAt` after a merge, `git status -sb` after a commit or push. This holds whatever the command printed, whatever wrapper it ran under, and whether or not the output was truncated. It replaces a prohibition on pipes that six eval entries recorded agents quoting and then violating, because it competed with a real need to truncate noise and lost every time (#2003).
2. **Pin an asynchronously-fetched verdict to its subject** before reading it. Resolve the head SHA first (`gh pr view "$PR" --json headRefOid`) and refuse any answer that is not about that SHA: an **empty** CI rollup reads as "nothing pending", and after a force-push a **complete green tally belonging to the previous head** reads as success.
3. **Decide "did it run?" on the terminal marker, not the clock.** A complete `just preflight` ends with `→ Preflight passed`; a run without that line did not finish, whatever it cost. Most phases announce themselves with a `→ ` line too, but not all — `mdbook` and `cargo doc` are silent — so count the marker, not the lines. Four branches once reported a failing preflight in 0–4 s because a `sed` had rewritten the recipe name; the tell was that no phase line appeared at all, not the duration. **A non-zero exit voids the heuristic entirely**: read the log. Wall-clock alone has misfired in both directions, and complete green runs have measured 108–140 s.
4. **State the run count.** N clean runs is not evidence of absence. Say how many times you ran it and capture the output — a flake and a fix are indistinguishable from a single green.

### Gate coverage (`artifact`)

**Name the gate that covers your change, and the environments it must be able to run in. If you cannot name one, verify it by hand and describe how in the PR.**

Coverage and executability are different questions, and only the first is usually asked. #1974 installed two gates into three materially different environments — a local `git commit`, `just check`, and CI's prek job — and **both were unable to execute in CI as designed**: one imports PyYAML, which `uv sync --only-group lint --only-group typecheck --only-group test` does not install, and the other calls `actionlint`, which is absent from that job's `PATH`. Both failed loudly, so the cost was a wasted push-and-wait rather than a silent hole. Nothing in the pipeline asked.

The `justfile` is authoritative for what the gates do; this file deliberately does not restate it. Gates get added — strict mkdocs, mdbook and `cargo doc` each entered preflight in separate changes — so any coverage table written here would have been wrong three times over.

Illustrative only, **not exhaustive**. Note that syntax-valid is not verified:

| Semantically gated | Syntax-only or ungated |
|---|---|
| `src/**.rs` — fmt, clippy, `test-rust`, `cargo doc` | `bacon.toml`, `prek.toml`, `cliff.toml`, `codecov.yml` — `check-toml`/`check-yaml` parse them; nothing validates them |
| `python/**.py` — ruff, ty, `test-python`, plus `scripts/check_subprocess_encoding.py` for text-mode `subprocess` calls with no `encoding=` (#1986) | `devenv.nix`, `flake.nix`, `nix/` — no gate at all |
| `docs/**.md` in the mkdocs nav — `mkdocs --strict` | `.github/actions/*/action.yml` — `check-yaml` parses them; actionlint cannot read a composite |
| `.github/workflows/*` — actionlint for referenced-but-undeclared `needs:`, and `scripts/check_platform_sets.py` for agreement between the required rollup, the wheel targets and `classifiers` (#1950). The reverse direction needed a checker until #2072 made each rollup read its results from `toJSON(needs)`; one literal cannot disagree with itself | `justfile` — `scripts/check_justfile_quoting.py` refuses a quoted interpolation (#2015); nothing else reads it |
| `docs/internals/**` — mdbook | `.envrc`, `.config/wt.toml` — no gate |
| `Cargo.lock`, `uv.lock` — lock checks | `*.md` outside the mkdocs nav — codespell only, no link check |

## Tools

### Worktrunk (`wt`)

All branch management uses Worktrunk. Never use raw `git checkout` or `git branch` for feature work.

```bash
# Create a new worktree for a feature branch
wt switch --create <branch> --yes

# Switch to an existing worktree
wt switch <branch>
```

Worktrunk runs `direnv reload` on switch (`post-switch` hook), which activates the devenv shell automatically. This means all tools (`cargo`, `ruff`, `just`, `prek`, etc.) are on PATH immediately — no manual nix store path hunting.

### devenv

The development environment is managed by devenv. All commands assume you are inside the devenv shell.

```bash
# Enter manually (if not using wt)
devenv shell

# Load into current shell without subshell
eval "$(devenv print-dev-env)"
```

Never install tools globally or via `pip install` / `cargo install`. If a tool is missing, add it to `devenv.nix`.

### prek

Pre-commit hooks are managed by prek (not pre-commit). Hooks run automatically on `git commit`. Hooks that declare `stages = ["pre-push"]` do **not** run on a local push — no pre-push shim is installed, and CI runs them instead. `prek.toml` says which and why. To run all hooks manually:

```bash
prek run --all-files
```

## Architecture

### Two-layer design

**Rust layer** (`src/`): Entry point is `src/lib.rs`, which exposes `run(args)` and `trace(level, module, message)` PyO3 functions. The Rust layer handles:
- `config.rs` — CLI parsing (clap) and `pyproject.toml` config under `[tool.oxitest]`
- `collector.rs` — file discovery based on `testpaths`/`python_files` patterns
- `cache.rs` — timing cache for parallel scheduling decisions and `--lf`/`--ff` support
- `filter.rs` — query DSL (`-E`) filtering, `--lf`/`--ff`, grouping by module
- `query/` — query DSL compiler, evaluator, and `oxitest query` subcommand
- `parallel.rs` — spawns worker subprocesses; each worker runs `python/oxitest/_bridge/worker.py`
- `scheduler.rs` — distributes test groups across workers
- `reporter/` — TTY, CI, and JSON (CTRF) reporters; `DiagnosticEntry`/`DiagnosticSeverity` in `stats.rs`; severity-sorted dedup rendering in `format/summary.rs`
- `strict.rs` — strict-mode violation checking (bare asserts, dict parametrize, missing mark reason)
- `bridge.rs` — PyO3 calls into the Python bridge: `collect_module`, `run_test`, `FixtureSession`, `drain_session_diagnostics`

**Python bridge** (`python/oxitest/_bridge/`): Pure-Python layer that does the actual test execution. Key modules by responsibility:

*Fixture system:*
- `_fixture_registry.py` — `FixtureDef`, `FixtureRegistry`; fixture definition and registry
- `_fixture_session.py` — `FixtureSession`, `_SessionProtocol`, `_Scope`; fixture lifecycle (scope caching, yield teardown, autouse)
- `_fixture_context.py` — fixture resolution context, `_warn_teardown` diagnostic helper
- `_fixture_instantiator.py` — fixture instantiation and dependency injection
- `_fixture_type.py` — `Fixture[T]`, `FixtureRef[T]`, `Yields[T]` type aliases
- `_fixture_validator.py` — fixture signature and type validation
- `proxy.py` / `proxy_ns.py` — `FrozenProxy` (shared fixtures) and `FixturesProxy` (namespace-aware `fx: Fixtures` injection)
- `_builtins/` — built-in injectable fixtures: `TempDir`, `TempDirFactory`, `StdCapture`, `FdCapture`, `Patcher`, `LogCapture`, `TestContext`

*Mark system:*
- `_mark_api.py` — mark evaluation: skip, xfail, timeout, and custom marks
- `_mark_registry.py` — mark registration and custom mark definitions

*Plugin system:*
- `plugin_loader.py` — plugin import, validation, `PluginRegistry` (frozen dataclass), `_PluginRegistryBuilder`
- `_plugin_config.py` — plugin settings resolution

*Execution:*
- `executor.py` — `run_test()`: loads module, resolves fixtures/parametrize, runs test, returns `TestResult`
- `_runners.py` — test execution runners (serial, debug)
- `result.py` — `TestResult` and outcome types, `Diagnostic` and `DiagnosticSeverity`
- `worker.py` — entry point for parallel worker subprocesses; reads JSON tasks from stdin, writes LDJSON results/diagnostics/traces to stdout via `_emit()`
- `parametrize.py` — resolves `@mark.parametrize` kwargs into per-case values

*Collection:*
- `importer.py` — `collect_module()`: imports test file, discovers `test_*` functions, returns `CollectedItem` list
- `conftest_loader.py` — loads `conftest.py` files, registers their `Fixtures()` instances
- `_loader.py` — module loading infrastructure

*Infrastructure:*
- `_coverage.py` — coverage provider integration (`CoveragePyProvider`)
- `_debugger.py` — debugger backend integration
- `_fn_metadata.py` — `FunctionMetadata` frozen dataclass
- `_violation_checkers.py` — strict-mode violation checking
- `_namespace_validation.py` — fixture namespace validation
- `_diagnostic_collector.py` — `ContextVar`-based `emit_diagnostic()` and `_diagnostic_collector_var`
- `_assert_error.py` — `_OxitestAssertionError` and enriched assertion diagnostics

### PyO3 data contract

Both the serial PyO3 path (`bridge.rs`) and the parallel JSON path (`worker_result/`) converge on `RawOutcome` (in `worker_result/convert.rs`) before producing a `TestOutcome`. `CollectedItem` fields must stay in sync with the Python `collect_module` return type. When adding fields to the Python result objects, update the corresponding `RawOutcome` variant and the PyO3 extraction logic in `bridge.rs`.

### Parallel execution

The Rust scheduler spawns `python -m oxitest._bridge.worker` subprocesses. Each worker receives a JSON task (modules + their items + fixture modules + plugins + `rootdir`) via stdin and writes LDJSON lines to stdout. `PROTOCOL_VERSION` in `src/worker_result/wire.rs` declares the wire version. Each line has a `"type"` discriminator: `"result"` (test outcome), `"diagnostic"` (user-facing message), or `"trace"` (developer log). The drain loop in `parallel/drain.rs` dispatches on this field. The worker is persistent within a run — it processes tasks until stdin is closed.

### Fixture injection protocol

Parameters annotated with `Fixture[T]` are injected; unannotated parameters are NOT (except built-in types like `TempDir`, `TestContext` which carry their own injection marker). `FixtureRef[T]` is for fixture references inside `@mark.parametrize` kwargs. `Fixtures` (bare, not `Fixture[T]`) injects a `FixturesProxy` namespace accessor.

### Configuration

`[tool.oxitest]` in `pyproject.toml` controls: `testpaths`, `python_files`, `norecursedirs`, `markers`, `timeout`, `cache_max_age`, `min_parallel_tests`, `timeout_multiplier`, `spawn_overhead_ms`, `strict`. All CLI flags override pyproject values.

### Type checking

`ty check` is the project's type checker. `just check` runs it over the **whole project**, tests included — `python/tests` is on `extra-paths` in `pyproject.toml`, so type errors in test code fail the build exactly like errors in `python/oxitest/`.

## Testing

- **Rust unit tests** (`just test-rust`): Unit tests for Rust modules.
- **Python integration tests** (`just test-python`): Run real commands. Tests use oxitest itself as the runner (`strict = "abort"`).
- **CI**: GitHub Actions. Two parallel jobs: `check` (static analysis via `just check`) and `test` (`just test-rust`, `just build`, `just test-python`). Uses `astral-sh/setup-uv` and `Swatinem/rust-cache` — no devenv in CI. The Rust toolchain is not one of CI's choices: `rust-toolchain.toml` names it and both sides install from that file, CI via `rustup toolchain install` and devenv via `languages.rust.toolchainFile` (#1792).
- **Every `assert` MUST have a message.** oxitest runs with `strict = "abort"` — bare asserts are violations. The message explains *why* the assertion matters — oxitest already shows the where, when, and what (expected vs actual). The message gives the developer the *why* so they can debug the *how*. Bad: `"expected 4 methods, got 3"` (oxitest already shows that). Good: `"FixtureProvider protocol added a method — HostProvider needs to implement it to avoid runtime TypeError"`.
- **Run a mutant with `just mutate`, never by hand** (`fold-in`). A test proves nothing until a mutation makes it fail, and every step between those two facts has produced a silent void result at least once. The recipe owns all of them: it depends on `mutation-guard`, so the clean-baseline check is no longer something to remember; it asserts the anchor matched exactly once; it refuses to report a test result when the build failed; it reverts scoped to the mutated path; and it rebuilds afterwards, so the compiled extension cannot outlive the mutant. Each terminal state has its own exit code — the justfile is authoritative for which, and for what the recipe does.

  This is a recipe rather than a sentence because the sentence did not work. It was escalated in prose to *"Run it; do not intend to."* and then violated four times, once by a run that had **quoted it in its own plan**. **A plan step that applies a mutant may not follow an uncommitted edit to the same file** — that is now enforced rather than remembered, and it has a consequence worth knowing: a change to the mutation tooling itself cannot be exercised until it is committed, because the recipe's own file is part of the tree its guard inspects.

  **Untracked files do not refuse.** `git checkout -- <file>` cannot destroy them, so refusing on them was a false positive by the guard's own rationale — and it made the verdict depend on which worktree you stood in, which a plan then encoded as universal (#1939).
- **A mutant that passes is a finding until explained.** If the test does not fail, one of two things is true: the test is weaker than it looks, or the mutation is not the inverse of the behaviour you think you changed. Both are worth knowing and neither is "write a better mutant and move on". The worst bug found in this repo's fixture work — a scope cache that was never cleared, leaking a temp directory after every worker's first task group — surfaced because a mutant *passed* and the pass was investigated.
- **A kill must be the failure you predicted.** A mutant that fails for some other reason has tested nothing, and it reads exactly like a kill. One exited 101 on a parse error, so the lint it was probing never ran at all; its mirror exited 0 because the parameter had been renamed `_app` and the site sat outside the gate the branch was installing. State which assertion you expect to fail, then check that it is the one that did.
- **Re-run the mutation set after any later change to the same code.** A mutant that *stops* failing is an unintended semantic change, and a review pass late in a branch is exactly when that happens.

### Testing guidelines

Tests in `python/tests/` must follow these rules:

1. **No class-based tests.** Use standalone `def test_*()` functions. The only exception is a class that shares `@oxi.parametrize` parameters across all its methods.
2. **Arrange, Act, Assert.** Every test should have three clear phases: set up test data (arrange), call the thing being tested (act), check the result (assert). Don't interleave setup and assertions.
3. **Dogfood oxitest features.** We are our own best user feedback. Always prefer oxitest APIs over stdlib/third-party equivalents:
   - `oxi.raises()` not `try/except` or `assertRaises`
   - `oxi.warns()` or `WarnCapture` not `warnings.catch_warnings()`
   - `TempDir` fixture not `tempfile.mkdtemp()` or `tempfile.TemporaryDirectory()`
   - `Patcher` fixture not `unittest.mock.patch` or raw `os.environ` manipulation
   - `StdCapture`/`FdCapture` not manual `sys.stdout` redirection
   - `LogCapture` not manual `logging.Handler` setup
   - `@oxi.parametrize` for multiple similar cases, not copy-pasted test functions
   - Dataclass-based test doubles not `unittest.mock.MagicMock`
   - Exception: when testing an oxitest feature itself requires bootstrapping (e.g., testing `Patcher` needs direct `os.environ` access), stdlib is acceptable in the arrange phase.
4. **Import test utilities as plain functions.** Shared utilities live in the `python/tests/helpers/` package, reached with `from tests import helpers` and called as `helpers.<function>()` — never `sys.path.insert`. Do **not** use the retired `helpers.common.<function>()` registry proxy (#1700, #1787): it resolved through `Helpers.__getattr__` to `Any`, so `ty` checked nothing at the call site. That blind spot hid 141 real type errors across ~790 calls.
5. **Test file names say what the file pins, and never a fact another consumer owns** — not the issue that prompted it, and not a wire version. The issue number belongs in the commit title, which stage 9 already requires; the wire version belongs to `PROTOCOL_VERSION`. Either one in a name is a second copy. `test_returns_none.py`, never `test_2067_returns_none.py`; `test_worker_protocol.py`, never `test_worker_protocol_v6.py` (#2124, found at v6 while the constant read 8). Four files drifted from this in eight days before it was written down (#2080), so it is prose that has already been broken once; a fifth occurrence is the evidence for making it a gate.

## Agent skills

### Issue tracker

GitHub Issues on `kalonji-tools/oxitest`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default label vocabulary (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout — `CONTEXT.md` + `docs/adr/` at root. See `docs/agents/domain.md`.

### Dispatch protocol

What a dispatched agent owes this pipeline. See `docs/agents/dispatch-protocol.md`, referenced from stage 7.

### Skill contracts

Where this repo overrides a mandated skill, and why. See `docs/agents/skill-contracts.md`. Skills say WHAT; this repo says WHERE; state the deviation in the turn you make it.
