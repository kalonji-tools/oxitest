# Introduction

This book documents the internals of oxitest for contributors who need to
understand the Rust backend well enough to make architectural decisions.

## Who this is for

You are a contributor (or future co-maintainer) working on the Rust core
(`src/`), the Python bridge (`python/oxitest/_bridge/`), or the boundary
between them. You already know how to use oxitest — the
[user documentation](https://kalonji-tools.github.io/oxitest/) covers that.
This book covers how it works and how to change it safely.

## How to use this book

Start with [Architecture Overview](architecture.md) for the module map and
the typestate pipeline pattern. Then read the chapter relevant to what you
are changing:

- Modifying the pipeline → [Pipeline Deep Dive](pipeline.md)
- Changing data that crosses the Rust/Python boundary → [PyO3 Bridge Contract](bridge.md)
- Changing the worker wire format → [Worker Protocol](worker-protocol.md)
- Adding a CLI flag, reporter, plugin, or marker → [Extending oxitest](extending.md)
- Adding a config option → [Config System](config.md)

[Testing Strategy](testing.md) explains when to write which kind of test.
[Design Decisions](decisions.md) links to the PR/issue specs where past
trade-offs were resolved.

## Building docs locally

```bash
just docs-serve    # all docs with live reload (user docs at localhost:8000, internals at localhost:3000)
just docs-build    # build only (user docs in docs/site/, internals in docs/internals/book/)
```

For type definitions, struct fields, and method signatures, use `cargo doc --open`
rather than reading source files. The Rust API docs are auto-generated and always
current.

## Quick links

- [User docs](https://kalonji-tools.github.io/oxitest/) — tutorials, how-to guides, API reference
- [CONTRIBUTING.md](https://github.com/kalonji-tools/oxitest/blob/main/CONTRIBUTING.md) — dev setup and PR conventions
- [GitHub repository](https://github.com/kalonji-tools/oxitest)
