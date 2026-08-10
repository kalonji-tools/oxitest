# ADR-0001: Remove graphify knowledge graph tooling

**Status:** Accepted
**Date:** 2026-06-19
**Executed:** 2026-08-10 ([#2028](https://github.com/kalonji-tools/oxitest/issues/2028)) — seven weeks after acceptance. The purge commit `b1ba91f9` never opened `devenv.nix`, so the install task and the Git hooks it installed survived; the Context list below now records them.

## Context

Graphify was integrated into oxitest to speed up AI-assisted codebase exploration and reduce token costs. The setup included:

- A `graphify-out/` directory with cached AST data, a knowledge graph (`graph.json`), and a report (`GRAPH_REPORT.md`)
- PreToolUse hooks in `.claude/settings.json` that forced agents to query the graph before reading source files
- CLAUDE.md instructions directing agents to use `graphify query`, `graphify path`, and `graphify explain`
- Exclusion patterns in `.gitignore`, `prek.toml`, and the codespell `skip` list in `pyproject.toml` to avoid linting graphify output
- A custom pre-push hook (later removed) to auto-commit graphify output
- A `devenv.nix` task, `oxitest:install-graphify`, that installed the tool and ran `graphify hook install` before every shell entry
- `post-commit` and `post-checkout` Git hooks, installed by that task, that rebuilt the graph after every commit and every branch switch

## Decision

Remove all graphify tooling, configuration, and output from the project.

## Reasons

1. **Unmeasurable value.** Graphify operates invisibly — agents use it in the background with no feedback. There were no metrics to verify whether it reduced token costs or improved exploration speed.
2. **Never used directly.** `graphify query`, `graphify path`, and `graphify explain` were never invoked manually during real development work, making it impossible to validate the tool was even functioning correctly.
3. **High maintenance tax.** Graphify output files needed to be committed to the repo. The tool provided no feedback when updates were skipped, leading to stale graphs. A custom pre-push hook was built as a workaround, but it was fragile and required babysitting.
4. **Three layers of workarounds.** The maintenance burden stacked: graphify itself, manual commit discipline, and an automated hook fix — all for a tool with unverified benefits.
5. **Fully reversible.** The knowledge graph is derived entirely from source code and can be regenerated at any time.

## Consequences

- Agents explore the codebase using standard tools (grep, glob, read) without a graph-first mandate.
- No `graphify-out/` directory to maintain or commit.
- Pre-commit/pre-push hooks no longer need graphify exclusion patterns.
- If a measurable codebase navigation tool emerges in the future, it can be evaluated with proper metrics before adoption.
