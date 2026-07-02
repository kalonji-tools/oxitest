"""Fixture dependency tree rendering for ``query fixtures --tree``."""

from __future__ import annotations

__all__ = ["tree_fixtures_from_registry"]

import inspect
from typing import Any

from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._fixture_registry import (
    BuiltinSource,
    ConftestSource,
    FixtureDef,
    FixtureRegistry,
    FixtureScope,
)

_BUILTIN_MODULE_PREFIX = "oxitest._bridge._builtins"
_BUILTIN_CONFTEST = "<builtin>"
_DIM = "\033[2m"
_BOLD_CYAN = "\033[1;36m"
_DIM_YELLOW = "\033[2;33m"
_RESET = "\033[0m"


def _builtin_defs() -> list[FixtureDef[Any]]:
    """Create synthetic FixtureDefs for type-based built-in fixtures.

    These live in BuiltinFixture._registry (type -> impl_cls) and are not
    in the FixtureRegistry. We create FixtureDefs so the lister can display
    them alongside registry fixtures.
    """
    defs = []
    for fixture_type, impl_cls in BuiltinFixture._registry.items():
        name = fixture_type.__name__.lstrip("_")
        doc = inspect.getdoc(fixture_type) or ""

        def _stub() -> None:
            pass

        _stub.__name__ = name
        _stub.__doc__ = doc
        _stub.__module__ = "oxitest._bridge._builtins"
        is_shared = getattr(impl_cls, "scope", "function") == "session"
        defs.append(
            FixtureDef(
                name=name,
                fixture_type=fixture_type,
                scope=FixtureScope.SHARED if is_shared else FixtureScope.EACH,
                source=BuiltinSource(impl_cls=impl_cls),
                autouse=False,
                is_async=False,
            )
        )
    return defs


def _is_builtin(defn: FixtureDef[Any]) -> bool:
    if isinstance(defn.source, BuiltinSource):
        return True
    if defn.conftest_path == _BUILTIN_CONFTEST:
        return True
    if isinstance(defn.source, ConftestSource):
        mod = getattr(defn.source.func, "__module__", "") or ""
        return mod.startswith(_BUILTIN_MODULE_PREFIX)
    return False


def _origin_key(defn: FixtureDef[Any]) -> tuple[int, str]:
    """Sort key: (0, '') for built-in, (1, plugin), (2, conftest_path)."""
    if _is_builtin(defn):
        return (0, "")
    if isinstance(defn.source, ConftestSource) and not defn.source.conftest_path:
        return (1, getattr(defn.source.func, "__module__", "plugin"))
    if not isinstance(defn.source, ConftestSource):
        return (1, defn.conftest_path)
    return (2, defn.conftest_path)


def _dim(text: str, use_color: bool) -> str:
    if use_color:
        return f"{_DIM}{text}{_RESET}"
    return text


def _origin_header(defn: FixtureDef[Any]) -> str:
    if _is_builtin(defn):
        return "built-in"
    if isinstance(defn.source, ConftestSource) and not defn.source.conftest_path:
        mod = getattr(defn.source.func, "__module__", "plugin")
        return f"plugin ({mod})"
    if not isinstance(defn.source, ConftestSource):
        return defn.conftest_path
    return defn.conftest_path


# Graph coloring states for DFS cycle detection.
_WHITE, _GRAY, _BLACK = 0, 1, 2


def tree_fixtures_from_registry(
    registry: FixtureRegistry,
    verbosity: int = 0,
    pattern: str | None = None,
    use_color: bool = True,
) -> str:
    """Format all fixtures as a dependency tree.

    Args:
        registry: The fixture registry to visualize.
        verbosity: 0=names only, 1=names+tags, 2=names+tags+origin.
        pattern: Optional substring filter on root fixture names.
        use_color: Whether to emit ANSI color codes.

    Returns:
        Formatted tree string, or error message if circular deps detected.
    """
    # Build dependency graph from signatures
    all_defs: dict[str, FixtureDef[Any]] = {}
    seen_names: set[str] = set()
    for name in sorted(registry):
        defs = registry.all_defs(name)
        if defs:
            all_defs[name] = defs[-1]
            seen_names.add(name)

    # Add built-in fixtures
    for defn in _builtin_defs():
        if defn.name not in seen_names:
            all_defs[defn.name] = defn

    # Extract deps for each fixture via depends_on (populated at registration)
    graph: dict[str, list[str]] = {}
    for name, defn in all_defs.items():
        deps = [
            qualifier
            for qualifier, _binding_type in defn.depends_on
            if qualifier in all_defs and qualifier != name
        ]
        graph[name] = deps

    # Cycle detection via DFS (white/gray/black)
    color: dict[str, int] = dict.fromkeys(all_defs, _WHITE)
    cycle_path: list[str] = []

    def _has_cycle(node: str, path: list[str]) -> bool:
        if color[node] == _GRAY:
            cycle_path.extend(path[path.index(node) :])
            cycle_path.append(node)
            return True
        if color[node] == _BLACK:
            return False
        color[node] = _GRAY
        path.append(node)
        for dep in graph.get(node, []):
            if dep in color and _has_cycle(dep, path):
                return True
        path.pop()
        color[node] = _BLACK
        return False

    for name in sorted(all_defs):
        if color[name] == _WHITE and _has_cycle(name, []):
            cycle_str = " -> ".join(cycle_path)
            return f"error: Circular fixture dependency: {cycle_str}"

    # Determine roots
    total = len(all_defs)
    roots = sorted(all_defs.keys(), key=lambda n: (_origin_key(all_defs[n]), n))
    if pattern:
        roots = [r for r in roots if pattern in r]

    if not roots:
        if pattern:
            return _dim(f"no fixtures match '{pattern}'", use_color)
        return ""

    # Render tree
    lines: list[str] = []

    def _render_node(name: str, prefix: str, is_last: bool, is_root: bool) -> None:
        defn = all_defs[name]
        label = _tree_label(defn, verbosity, use_color)
        if is_root:
            lines.append(label)
        else:
            connector = "└── " if is_last else "├── "
            if use_color:
                lines.append(f"{_DIM}{prefix}{connector}{_RESET}{label}")
            else:
                lines.append(f"{prefix}{connector}{label}")

        # Recurse into deps
        deps = graph.get(name, [])
        child_prefix = prefix if is_root else (prefix + ("    " if is_last else "│   "))
        for i, dep in enumerate(deps):
            _render_node(dep, child_prefix, is_last=i == len(deps) - 1, is_root=False)

    for i, root in enumerate(roots):
        if i > 0:
            lines.append("")
        _render_node(root, "", is_last=True, is_root=True)

    # Summary
    shown = len(roots)
    if pattern:
        lines.append(f"\n── {shown} of {total} fixtures")
    else:
        s = "" if total == 1 else "s"
        lines.append(f"\n── {total} fixture{s}")

    return "\n".join(lines)


def _tree_label(defn: FixtureDef[Any], verbosity: int, use_color: bool) -> str:
    """Format a single fixture node label based on verbosity."""
    name = defn.name
    if use_color:
        name = f"{_BOLD_CYAN}{defn.name}{_RESET}"

    if verbosity == 0:
        return name

    # Verbosity 1: name + tags
    parts: list[str] = []
    if defn.shared:
        parts.append("shared")
    if defn.is_async:
        parts.append("async")
    if defn.autouse:
        parts.append("autouse")
    tag_str = ""
    if parts:
        tag_str = f" [{', '.join(parts)}]"
        if use_color:
            tag_str = f" {_DIM_YELLOW}[{', '.join(parts)}]{_RESET}"

    if verbosity == 1:
        return f"{name}{tag_str}"

    # Verbosity 2: name + tags + origin
    origin = _origin_header(defn)
    origin_str = f" ({origin})" if origin else ""
    if use_color:
        origin_str = f" {_DIM}({origin}){_RESET}" if origin else ""
    return f"{name}{tag_str}{origin_str}"
