# Working with AI

!!! abstract "Explanation"
    How oxitest uses AI as a disciplined collaborator rather than an unsupervised autocomplete.

## The problem with both extremes

Most teams that adopt AI coding assistants land in one of two failure modes:

**No guardrails.** The AI has full access to everything. Developers accept large,
barely-reviewed diffs because "the AI wrote it." Code quality degrades. Modules
grow tangled. Tests pass but nobody understands why.

**Too many guardrails.** The AI is locked down so hard — no terminal, no tool
integrations, no memory — that it becomes a glorified autocomplete. Developers
spend more time working around the restrictions than they save.

oxitest is a deliberate counter-experiment: use AI at full capability, but box it
in with tooling, conventions, and review gates rather than by stripping out features.

## The constraints that make it work

### Language choice

Rust refuses to compile nonsense. The type system, borrow checker, and `#[deny(warnings)]`
+ `#[deny(clippy::all)]` lint configuration catch entire categories of mistakes before
code reaches review. When the AI generates Rust, the compiler is the first reviewer.

Python is less strict by design, so oxitest compensates with Ruff (linting + formatting),
ty (type checking), and codespell — all enforced by pre-commit hooks.

### Isolated environment

A [Nix](https://nixos.org/) flake defines the exact toolchain: Rust compiler, Python
interpreter, maturin, uv, and CLI tools. Every contributor — human or AI — enters the
same shell with the same versions. No "works on my machine."

A [justfile](https://github.com/casey/just) exposes the common commands (`just build`,
`just test`, `just lint`, `just fmt`). The AI hits the same entry points a human would.
No hidden scripts, no magic incantations.

### Pre-commit and pre-push hooks

[prek](https://github.com/kalonji-tools/prek) runs the full quality suite on every
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
[worktrunk](https://github.com/anthropics/worktrunk). When the AI works on multiple
issues simultaneously, each gets its own worktree and branch. No rebasing over
half-finished work, no accidental cross-contamination between features.

### Memory and conventions

`CLAUDE.md` at the project root tells the AI the project's conventions: how to run
tests, how commits are structured, what the architecture looks like. The AI reads this
at the start of every session. When conventions change, the file gets updated — one
source of truth, not tribal knowledge scattered across chat histories.

## The workflow

### 1. Brainstorm and specify

Every feature starts with a design conversation driven by
[superpowers](https://github.com/anthropics/superpowers-marketplace) skills. The AI
asks clarifying questions, explores trade-offs, and drafts a design spec. The spec
captures what will be built, why, and what the acceptance criteria are.

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

## What this gets you

The AI is genuinely fast. It can explore a codebase, write a spec, break it into
issues, implement each one, and open PRs — all in a fraction of the time it would
take manually.

But speed without structure is just chaos delivered faster. The value comes from the
combination:

- [**Nix**](https://nixos.org/) ensures reproducibility.
- [**prek**](https://github.com/kalonji-tools/prek) enforces quality at the commit boundary.
- **Rust's compiler** catches mistakes before review.
- [**worktrunk**](https://github.com/anthropics/worktrunk) enables safe parallel worktrees.
- [**Superpowers**](https://github.com/anthropics/superpowers-marketplace) skills drive spec-driven development.
- **PR gates** keep a human in the loop for every merge.

The AI is boxed in by tooling and conventions, not by removing its capabilities.
You get speed without the chaos.
