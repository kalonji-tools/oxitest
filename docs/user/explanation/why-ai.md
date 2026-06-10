# Working with AI

!!! abstract "Explanation"
    How oxitest uses AI as a disciplined collaborator rather than an unsupervised autocomplete.

## The problem with both extremes

I've worked in environments that took opposite approaches to AI coding assistants,
and both failed.

**No guardrails.** The AI had full access to everything. Developers accepted large,
barely-reviewed diffs because "the AI wrote it." Code quality degraded. Modules grew
tangled. Tests passed but nobody understood why.

**Too many guardrails.** The AI was locked down so hard — no terminal, no tool
integrations, no memory — that it became a glorified autocomplete. Developers spent
more time working around the restrictions than they saved.

Both extremes felt wrong, so I wanted to see if there's a proper way to do this.
oxitest is that experiment: use AI at full capability, but box it in with tooling,
conventions, and review gates rather than by stripping out features.

This project uses [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview)
as the AI assistant, but the strategy is model-agnostic. The constraints described here
work with any AI coding tool that can run in a terminal.

## Why CLI, not web

Any web-based AI interface was immediately rejected. Web tools lose project context
between sessions. You start every conversation re-explaining the architecture, the
conventions, the state of the current branch. That context gap is where mistakes happen.

A CLI tool runs inside the project. It reads the codebase, the git history, the
configuration files. It has `CLAUDE.md` loaded at startup with the project's conventions.
It can run tests, lint, and commit — all within the same session, all with full context.
The context of the project is the highest priority, and a CLI tool preserves it.

## The constraints that make it work

### Language choice

Rust refuses to compile nonsense. The type system, borrow checker, and `#[deny(warnings)]`
+ `#[deny(clippy::all)]` lint configuration catch entire categories of mistakes before
code reaches review. When the AI generates Rust, the compiler is the first reviewer.

Python is less strict by design, so oxitest compensates with Ruff (linting + formatting),
ty (type checking), and codespell — all enforced by pre-commit hooks.

If you can pick a stricter language, do it. The compiler catches what review misses.

### Isolated environment

A [devenv](https://devenv.sh/) environment defines the exact toolchain: Rust compiler, Python
interpreter, maturin, uv, and CLI tools. Every contributor — human or AI — enters the
same shell with the same versions. No "works on my machine."

A [justfile](https://github.com/casey/just) exposes the common commands (`just build`,
`just test`, `just lint`, `just fmt`). The AI hits the same entry points a human would.
No hidden scripts, no magic incantations.

### Pre-commit and pre-push hooks

[prek](https://prek.j178.dev/) runs the full quality suite on every
commit and push:

- **Pre-commit:** formatting (cargo fmt, ruff format), linting (ruff check), type
  checking (ty), codespell, trailing whitespace, TOML/YAML validation, bridge sync
  verification.
- **Pre-push:** cargo clippy (catches dead code and lint errors that `cargo test`
  does not), lock file consistency, tag-version matching.
- **Branch protection:** a `no-commit-to-branch` hook prevents direct commits to main.
  Every change goes through a PR.

The AI cannot bypass these. If a hook fails, the commit is rejected. The AI must fix
the issue and try again — the same feedback loop a human developer follows.

### Git worktrees for parallel work

oxitest uses a bare repo with worktrees managed by
[worktrunk](https://worktrunk.dev/worktrunk/). When the AI works on multiple
issues simultaneously, each gets its own worktree and branch. No rebasing over
half-finished work, no accidental cross-contamination between features.

### Memory and conventions

`CLAUDE.md` at the project root tells the AI the project's conventions: how to run
tests, how commits are structured, what the architecture looks like. The AI reads this
at the start of every session. When conventions change, the file gets updated — one
source of truth, not tribal knowledge scattered across chat histories.

## Spec Driven Development

The workflow is built around
[Spec Driven Development](https://www.specdriven.dev/) (SDD) — the idea that you
write a design spec *before* touching code, get alignment on what you're building,
and then delete the spec once the feature ships. The spec is a thinking tool, not
documentation.

SDD prevents the most common AI failure mode: diving straight into implementation
before the problem is understood. Without a spec, the AI optimises for "make the
tests pass" rather than "solve the right problem." With a spec, every implementation
decision traces back to a design decision that was reviewed and approved.

I use [superpowers](https://github.com/obra/superpowers) as the
skill set that executes this strategy. The skills enforce the SDD workflow: brainstorm
before building, write a spec before planning, write a plan before coding, verify
before claiming done. I edited the default skills to match my flow — the brainstorming
skill asks sharper questions, the planning skill produces more granular issues, and
the execution skill respects the review gates I care about.

## The workflow

### 1. Brainstorm and specify

Every feature starts with a design conversation. The AI asks clarifying questions,
explores trade-offs, and drafts a design spec. The spec captures what will be built,
why, and what the acceptance criteria are.

### 2. Break into issues

The spec gets decomposed into small, ordered issues. Each issue is self-contained
and reviewable in isolation. Where possible, issues are marked for parallel execution.
Issues group into milestones for tracking.

### 3. Plan before coding

For each issue, the AI reads the spec and writes an implementation plan: which files
change, what the diff looks like conceptually, what tests cover the change. The plan
gets posted as a PR comment before any code is written.

### 4. Code with review gates

The AI opens a PR, implements the plan, and the pre-commit/pre-push hooks enforce
quality. The PR includes:

- A summary of what changed and why.
- A link back to the implementation plan.
- A test plan with checkboxes.

The AI does not merge without explicit human approval. Every PR is a review checkpoint.

### 5. Merge and clean up

After approval, the PR is rebase-merged. The worktree and branch are removed. The
milestone tracks progress across the batch.

## What's still hard

This workflow is not foolproof. It's a work in progress, and I want to be honest
about where it breaks down.

**The AI is eager.** Sometimes a question is just a question. I ask "is this function
still used?" and the AI reads the codebase, confirms it's dead code, *and then deletes
it, commits, and opens a PR* — all before I've decided whether I want that. The intent
was reconnaissance, not action. I've learned to phrase questions carefully, but the
eagerness is a recurring friction.

**Unsolicited merges.** Even with explicit instructions in `CLAUDE.md` that merging
requires approval, the AI occasionally interprets "rebase merge admin" (a command I
want to run myself) as permission to merge. This has required adding increasingly
specific rules — and even then, it's not perfect.

**Slower and more expensive.** This workflow uses more tokens than "just let the AI
code." Brainstorming, specifying, planning, reviewing — each step is a conversation.
But I'd argue slower is faster. The time spent on a spec saves multiples in rework.
The plan catches misunderstandings before they become 500-line diffs. The cost is
real, but so is the cost of reverting a bad merge.

**It requires discipline from the human too.** The tooling enforces quality, but the
human still has to review the specs, read the plans, and actually check the PRs.
If I rubber-stamp everything, the guardrails don't matter. The AI is as good as the
oversight it receives.

## What this gets you

The AI is genuinely fast. It can explore a codebase, write a spec, break it into
issues, implement each one, and open PRs — all in a fraction of the time it would
take manually.

But speed without structure is just chaos delivered faster. The value comes from the
combination:

- [**devenv**](https://devenv.sh/) ensures reproducibility.
- [**prek**](https://prek.j178.dev/) enforces quality at the commit boundary.
- **Rust's compiler** catches mistakes before review.
- [**worktrunk**](https://worktrunk.dev/worktrunk/) enables safe parallel worktrees.
- [**Superpowers**](https://github.com/obra/superpowers) skills drive the spec-driven workflow.
- **PR gates** keep a human in the loop for every merge.

The AI is boxed in by tooling and conventions, not by removing its capabilities.
You get speed without the chaos.
