# Dispatch Protocol

What a dispatched agent owes the rest of the pipeline.

**This file does not define the pipeline.** `CLAUDE.md` does. Every stage obligation below is a *reference* to it, never a restatement — a dispatch prompt that restates a stage's content is how a wrong instruction gets executed N times in parallel, and that has already happened here (§1).

Each rule states the failure that produced it. That is not decoration: half of these look like fussy bookkeeping until you see the artifact they prevent, and a rule nobody believes gets dropped under pressure.

## 1. Name the stages; do not restate them

**Rule.** The dispatch prompt names which `CLAUDE.md` Track A stages the agent owns, by number, and points at the file. It does not say what those stages require.

**Why — the omission half.** A dispatched agent inherits no stage obligations. Stage 7 offers subagents and says nothing about what a subagent owes anything else, so the prompt silently becomes the pipeline. Measured across three runs of the same shape of work:

| Stage | obligations absent from prompt (4 agents) | solo control (1 issue) | obligations named in prompt (5 agents) |
| ----- | ---------------------------------------- | ---------------------- | -------------------------------------- |
| 6 plan comment on the PR | 2/4 | ✅ | 4/4 |
| 8 `/improve branch` | 1/4 | ✅ | 8/8 |
| 10 post-merge debrief | **0/4** | ✅ | 4/4 |

The solo run scored 10/10 stages in the same period. The stages are not the weak part; the dispatch contract is. The cost of the gap was concrete: one implementation rejected the fix shape its own issue specified — textbook stage-10 material, recorded nowhere, the reasoning surviving only in a non-durable agent report.

**Why — the restatement half.** The next run acted on that lesson, wrote the obligations into five prompts, got the compliance benefit above, and *one of the restatements was wrong*: all five said to apply `diverged-from-plan` at stage 10, where `CLAUDE.md` and the label's own description both say closed PRs only. On the timeline API: two agents labelled their PRs 52 and 49 minutes **before** merge; one read the label description, refused, and asked for it at merge instead.

Two agents obeying an ephemeral, unreviewed prompt over the repo's maintained doc is the whole finding. Blast radius scales with fleet size, and the coordinator is both the single point of failure and the only reviewer.

**How.** `Stages 4, 5, 6, 8 and 10 of CLAUDE.md Track A are yours. Read them there — I am not restating them.`

## 2. One scratchpad per agent

**Rule.** Each agent gets its own scratchpad directory, named for the agent or its issue. No shared filenames, ever.

**Why.** Four agents once shared one scratchpad; three wrote `spec.md`, `plan.md` and `pr_body.md` and clobbered each other mid-run. That was a near-miss. The next occurrence was not: two agents independently wrote `pr-body.md`, and one overwrote the other **between its `Write` and its `gh pr create`**, so a PR was created carrying a `Closes` keyword for *another agent's still-open issue*. Had it survived review, merging it would have auto-closed that issue with its own PR in flight.

The one-line fix was applied on the third run: **0 collisions across 5 agents.**

**How.** `Your scratchpad is <dir>/<issue>/. Write nothing outside it.` Then, coordinator-side, read every PR body back before any merge and confirm each closes exactly its own issue. The check costs seconds; the collision should not need catching.

## 3. Cite inside your own diff, and own your hunks

**Rule.** An agent cites only files inside its own branch's diff. Where two lanes touch one file, the prompt names who owns which hunks.

**Why.** One PR amended an ADR citing claims by `path:line`; two sibling PRs in the same wave moved both files. Measured after those merged and before this one did: **4 of 13 citations broken.** One "the abort path" citation had landed inside an unrelated function; one "the severity map" citation had landed on a closing brace.

It was invisible to three per-branch reviews, and structurally so: `/improve branch` is *defined* as the branch's diff plus direct importers, so a citation into an untouched file is out of scope by construction. It became obvious immediately when the audit was scoped to three branches as one set — **audit concurrent branches as a set, not one at a time.**

The same seam appears at the planning end: conflict analysis done at *file-list* level wrongly serialised two lanes as blocked on each other; hunk-level analysis unblocked both.

**How.** Cite symbols, or `path:line@commit`, or quote the line inline. Never a bare `path:line` into a file your branch does not touch.

## 4. Some gates cannot be parallelised

**Rule.** `just preflight` is coordinator-only and runs serially. Tell agents this *with the reason*, so nobody helpfully runs it anyway.

**Why.** Two concurrent preflights corrupt each other — `68 failed · 1481 passed` concurrently against `1549 passed` alone, same commit. Stage 7 offers subagents; stage 9 demands the gate; nothing in the doc connected them until this file.

Withholding it is not free, and the doc should say so honestly rather than imply the serial re-run is either cheap or optional. In one run the coordinator's serial pass caught a regression that **every one of four agent-side gate suites had passed**. Across two runs the cumulative yield is 1 defect in roughly 13 serial runs. The cost is certain; the yield is lumpy.

**How.** `Do not run just preflight — it cannot run concurrently, and I run it serially before anything merges.`

## 5. Standing permission to refuse the coordinator

**Rule.** Where the dispatch prompt contradicts `CLAUDE.md`, `CLAUDE.md` wins, refusing is correct behaviour, and the agent should say so rather than comply quietly. Back it with a worked example of a past coordinator error.

**Why.** This is the only part of the pipeline that has ever caught a coordinator error. A coordinator-authored triage brief once asserted that a design document drew no distinction it in fact drew explicitly, and that no amendment was needed where one was. The brief was confident and cited `file:line` throughout, and it was wrong about the document it claimed to enforce. Two of three agents refused something that run; both refusals were correct.

The clause needs the worked example because correct behaviour here is nearly invisible. In the run described in §1, one agent refused a wrong instruction while two complied — and had that one agent silently complied, the wrong state would have been **3-for-3 consistent, which reads as more correct than the truth did.**

**How.** Roughly verbatim, because the wording is what did the work:

> Where anything I write below contradicts `CLAUDE.md`, `CLAUDE.md` wins. Refusing me is correct behaviour and I want it. I have been wrong before: *(one-line example)*.

## 6. Gates run in the foreground

**Rule.** Gates run in the foreground, staged, inside the agent's own turn. Never backgrounded.

**Why.** A backgrounded gate stalls the lane: the agent's turn ends, the gate keeps running, and the coordinator has to watch and nudge. Three of three lanes needed that; the one lane whose prompt specified staged foreground gates needed none.

Backgrounding also costs you the duration signal, which `CLAUDE.md`'s *Believing a verdict* rules depend on.

## Scope

Dispatch seams only. Solo runs score well without any of this, and nothing here is a substitute for a `CLAUDE.md` stage.
