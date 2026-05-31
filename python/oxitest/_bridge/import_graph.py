"""AST-based import analysis for --affected test selection.

Parses Python source files to extract import statements, then maps
changed source files to module names so we can determine which test
files are affected by source changes.
"""

__all__ = ["resolve_affected"]

import ast
from collections.abc import Sequence
from pathlib import Path


def _file_to_modules(rel_path: str, root: str) -> list[str]:
    """Convert a relative file path to all possible module name prefixes.

    Args:
        rel_path: File path relative to the project root (e.g. "myapp/utils.py").
        root: Absolute project root path (unused for relative paths, kept for API
              consistency with the bridge call).

    Returns:
        List of module names from most specific to least specific.
        E.g. "myapp/sub/foo.py" -> ["myapp.sub.foo", "myapp.sub", "myapp"].
    """
    try:
        p = Path(rel_path)
        parts = list(p.with_suffix("").parts)
    except (ValueError, TypeError):
        return []

    # __init__.py represents the package itself
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]

    if not parts:
        return []

    return [".".join(parts[:i]) for i in range(len(parts), 0, -1)]


def _extract_imported_modules(file_path: str) -> set[str]:
    """Extract all absolutely-imported module names from a Python file.

    Parses the file with ``ast`` and collects ``import X`` and
    ``from X import Y`` statements.  Relative imports (``from . import``)
    are skipped -- files in the same package are caught by direct-change
    detection.

    Returns:
        Set of dotted module names plus all their prefixes.
    """
    try:
        source = Path(file_path).read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, OSError):
        return set()

    def _module_prefixes(dotted_name: str) -> set[str]:
        parts = dotted_name.split(".")
        return {".".join(parts[:i]) for i in range(1, len(parts) + 1)}

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.update(_module_prefixes(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.update(_module_prefixes(node.module))
    return modules


def resolve_affected(
    test_files: Sequence[str],
    changed_sources: Sequence[str],
    root: str,
) -> list[str]:
    """Return test files that import any of the changed source files.

    Builds a set of module names from the changed source paths, then
    checks each test file's imports for overlap.

    Args:
        test_files: Absolute paths to test files.
        changed_sources: Paths relative to root of changed source files.
        root: Project root directory.

    Returns:
        Subset of test_files that import at least one changed module.
    """
    changed_modules: set[str] = set()
    for src in changed_sources:
        changed_modules.update(_file_to_modules(src, root))

    if not changed_modules:
        return []

    affected: list[str] = []
    for test_file in test_files:
        imports = _extract_imported_modules(test_file)
        if imports & changed_modules:
            affected.append(test_file)
    return affected
