# Configure Test Paths

!!! abstract "How-to"
    Control which directories and files oxitest searches for tests via `pyproject.toml`.

## Add the oxitest config section

Open (or create) `pyproject.toml` in your project root and add:

```toml
[tool.oxitest]
```

All options below go inside this section.

## Set the directories to search

```toml
[tool.oxitest]
testpaths = ["tests", "integration"]
```

oxitest will only look for test files inside `tests/` and `integration/`. Without
this option it defaults to the current directory.

## Use non-standard file patterns

```toml
[tool.oxitest]
python_files = ["test_*.py", "*_test.py", "check_*.py"]
```

Any glob pattern that matches your file naming convention can be listed here. The
default is `["test_*.py", "*_test.py"]`.

## Skip specific directories

```toml
[tool.oxitest]
norecursedirs = [".git", "__pycache__", ".venv", "venv", ".tox", "dist", "build", "node_modules", "fixtures"]
```

oxitest never traverses these directories during discovery. The defaults already
include the most common ones; extend the list as needed.

## Full example

```toml
[tool.oxitest]
testpaths     = ["tests", "integration"]
python_files  = ["test_*.py", "*_test.py"]
norecursedirs = [".git", "__pycache__", ".venv", "venv", ".tox", "dist", "build", "node_modules"]
```

## See also

- [Filter tests](filter-tests.md) — run a subset by keyword or file path
- [Configuration reference](../reference/configuration.md) — all `pyproject.toml` keys
