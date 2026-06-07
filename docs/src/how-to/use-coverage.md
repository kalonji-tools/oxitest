# Use coverage

!!! abstract "How-to"
    Collect code coverage while running tests.

## Prerequisites

`--cov` requires the `coverage` package:

```console
$ pip install coverage
```

## Basic usage

Add `--cov` to any `oxitest run` command:

```console
$ oxitest --cov
```

After the test summary, a coverage table is printed showing the percentage of
lines covered in each source file.

## Report formats

Use `--cov-report` to change the output format:

```console
$ oxitest --cov --cov-report html    # generate htmlcov/ directory
$ oxitest --cov --cov-report xml     # generate coverage.xml (for CI upload)
$ oxitest --cov --cov-report json    # generate coverage.json
$ oxitest --cov --cov-report none    # collect data only, no report
```

The default is `term` (print to terminal).

## Parallel mode

Coverage works automatically with parallel execution. Each worker subprocess
collects its own coverage data, which is combined after all tests complete.
No additional configuration is needed.

## Configuration

oxitest delegates all coverage configuration to coverage.py. Add a
`[tool.coverage]` section to `pyproject.toml`:

```toml
[tool.coverage.run]
source = ["src"]
branch = true

[tool.coverage.report]
fail_under = 80
show_missing = true
```

See the [coverage.py documentation](https://coverage.readthedocs.io/) for all
available options.

## CI integration

Example GitHub Actions step with Codecov upload:

```yaml
- name: Run tests with coverage
  run: oxitest --cov --cov-report xml

- name: Upload coverage
  uses: codecov/codecov-action@v4
  with:
    files: coverage.xml
```

## Plugin override

If you need a different coverage backend (e.g. slipcover), a plugin can
provide a `CoverageProvider` that replaces the built-in coverage.py
integration. See [Write plugins](write-plugins.md) for details.

## See also

- [CLI reference](../reference/cli.md) — `--cov` and `--cov-report` flags
- [Write plugins](write-plugins.md) — `CoverageProvider` protocol
