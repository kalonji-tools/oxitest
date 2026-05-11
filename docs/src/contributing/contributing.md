# Contributing

!!! abstract "Contributing"
    How to run tests, submit changes, and follow project conventions for oxitest.

## Running Rust tests

```bash
cargo test
```

Rust unit tests are inline in their modules and require no additional setup beyond the steps in
[Build Setup](build-setup.md).

## Running integration tests

Integration tests live in the `oxitest-consumer` repository. They run oxitest against a real
test suite and verify end-to-end behavior. See the `oxitest-consumer` repository for setup
instructions.

## Submitting changes

- Branch from `main`
- `cargo test` must pass before opening a PR
- Commit messages follow Conventional Commits style: `feat: add X`, `fix: correct Y`, `docs: update Z`
- Include Rust unit tests for new behavior where applicable
