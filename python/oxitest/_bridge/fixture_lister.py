"""List registered fixtures for the --fixtures CLI flag."""

from __future__ import annotations

__all__ = ["list_fixtures_from_registry", "tree_fixtures_from_registry"]

import inspect
import re
from typing import Any

from oxitest._bridge._builtins._base import BuiltinFixture
from oxitest._bridge._fixture_registry import FixtureDef, FixtureRegistry

_BUILTIN_MODULE_PREFIX = "oxitest._bridge._builtins"
_BUILTIN_CONFTEST = "<builtin>"
_BOX_WIDTH = 50
_DIM = "\033[2m"
_BOLD_CYAN = "\033[1;36m"
_DIM_YELLOW = "\033[2;33m"
_GREEN = "\033[32m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_GRAY = "\033[90m"
_RESET = "\033[0m"

_PY_KEYWORDS = (
    r"\b(?:def|return|assert|if|else|elif|for|while|with|as|import|from|class"
    r"|try|except|finally|raise|yield|async|await|pass|break|continue"
    r"|and|or|not|in|is|None|True|False)\b"
)
_PY_HIGHLIGHT = re.compile(
    rf"(?P<comment>#.*$)"
    rf"|(?P<string>\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|\"[^\"]*\"|'[^']*')"
    rf"|(?P<keyword>{_PY_KEYWORDS})"
    rf"|(?P<decorator>@\w+)",
    re.MULTILINE,
)


def _highlight_python(line: str) -> str:
    """Apply basic syntax highlighting to a line of Python code."""

    def _replace(m: re.Match[str]) -> str:
        if m.group("comment"):
            return f"{_GRAY}{m.group()}{_RESET}"
        if m.group("string"):
            return f"{_GREEN}{m.group()}{_RESET}"
        if m.group("keyword"):
            return f"{_BLUE}{m.group()}{_RESET}"
        if m.group("decorator"):
            return f"{_MAGENTA}{m.group()}{_RESET}"
        return m.group()

    return _PY_HIGHLIGHT.sub(_replace, line)


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
        defs.append(
            FixtureDef(
                name=name,
                func=_stub,
                autouse=False,
                params=None,
                conftest_path=_BUILTIN_CONFTEST,
                shared=getattr(impl_cls, "scope", "function") == "session",
                is_async=False,
            )
        )
    return defs


def _is_builtin(defn: FixtureDef[Any]) -> bool:
    if defn.conftest_path == _BUILTIN_CONFTEST:
        return True
    mod = getattr(defn.func, "__module__", "") or ""
    return mod.startswith(_BUILTIN_MODULE_PREFIX)


def _origin_key(defn: FixtureDef[Any]) -> tuple[int, str]:
    """Sort key: (0, '') for built-in, (1, plugin), (2, conftest_path)."""
    if _is_builtin(defn):
        return (0, "")
    if not defn.conftest_path:
        return (1, getattr(defn.func, "__module__", "plugin"))
    return (2, defn.conftest_path)


def _tags(
    defn: FixtureDef[Any], use_color: bool, *, include_autouse: bool = False
) -> str:
    """Build right-aligned tag string for shared/async/autouse."""
    parts = []
    if defn.shared:
        parts.append("shared")
    if defn.is_async:
        parts.append("async")
    if include_autouse and defn.autouse:
        parts.append("autouse")
    if not parts:
        return ""
    tag_str = "  ".join(parts)
    if use_color:
        return f"{_DIM_YELLOW}{tag_str}{_RESET}"
    return tag_str


def _name_with_tags(
    defn: FixtureDef[Any], use_color: bool, *, include_autouse: bool = False
) -> str:
    """Format fixture name with right-aligned tags."""
    name = defn.name
    if use_color:
        name = f"{_BOLD_CYAN}{defn.name}{_RESET}"
    tag = _tags(defn, use_color, include_autouse=include_autouse)
    if not tag:
        return f"  {name}"
    # Right-align tags to box width
    pad = _BOX_WIDTH - 2 - len(defn.name)  # 2 for leading spaces
    return f"  {name}{' ' * max(1, pad)}{tag}"


def _dim(text: str, use_color: bool) -> str:
    if use_color:
        return f"{_DIM}{text}{_RESET}"
    return text


def _format_quiet(defn: FixtureDef[Any], use_color: bool) -> str:
    tag = _tags(defn, use_color)
    name = defn.name
    if use_color:
        name = f"{_BOLD_CYAN}{defn.name}{_RESET}"
    if tag:
        pad = _BOX_WIDTH - 2 - len(defn.name)
        return f"  {name}{' ' * max(1, pad)}{tag}"
    return f"  {name}"


def _format_standard(defn: FixtureDef[Any], use_color: bool) -> str:
    lines = [_name_with_tags(defn, use_color)]
    doc = inspect.getdoc(defn.func)
    if doc:
        first_line = doc.split("\n")[0]
        pipe = _dim("│", use_color)
        lines.append(f"  {pipe} {_dim(first_line, use_color)}")
    return "\n".join(lines)


def _format_rich(defn: FixtureDef[Any], use_color: bool) -> str:
    pipe = _dim("│", use_color)
    lines = [_name_with_tags(defn, use_color, include_autouse=True)]
    doc = inspect.getdoc(defn.func)
    if doc:
        in_code = False
        for doc_line in doc.split("\n"):
            stripped = doc_line.strip()
            if stripped.startswith("```"):
                in_code = not in_code
                continue  # skip fence lines
            if in_code and use_color:
                lines.append(f"  {pipe}     {_highlight_python(doc_line)}")
            else:
                lines.append(f"  {pipe} {_dim(doc_line, use_color)}")
        lines.append(f"  {pipe}")
    # Metadata
    try:
        sig = inspect.signature(defn.func)
        deps = [p for p in sig.parameters if p != "self"]
    except (ValueError, TypeError):
        deps = []
    if deps:
        if use_color:
            colored_deps = ", ".join(f"{_BOLD_CYAN}{d}{_RESET}" for d in deps)
        else:
            colored_deps = ", ".join(deps)
        lines.append(f"  {pipe} {_dim('depends on: ', use_color)}{colored_deps}")
    if defn.params:
        lines.append(f"  {pipe} {_dim(f'parametrized: {defn.params!r}', use_color)}")
    return "\n".join(lines)


def _origin_header(defn: FixtureDef[Any]) -> str:
    if _is_builtin(defn):
        return "built-in"
    if not defn.conftest_path:
        mod = getattr(defn.func, "__module__", "plugin")
        return f"plugin ({mod})"
    return defn.conftest_path


def _box_top(title: str, use_color: bool) -> str:
    fill = "─" * max(0, _BOX_WIDTH - 4 - len(title))
    line = f"╭─ {title} {fill}╮"
    return _dim(line, use_color)


def _box_bottom(use_color: bool) -> str:
    line = f"╰{'─' * (_BOX_WIDTH - 1)}╯"
    return _dim(line, use_color)


def list_fixtures_from_registry(
    registry: FixtureRegistry,
    verbosity: int = 1,
    pattern: str | None = None,
    use_color: bool = True,
) -> str:
    """Format all fixtures in the registry as a display string.

    Args:
        registry: The fixture registry to list.
        verbosity: 0=quiet, 1=standard, 2=rich.
        pattern: Optional substring filter on fixture name.
        use_color: Whether to emit ANSI color codes.

    Returns:
        Formatted string ready for printing. Empty string if no fixtures match.
    """
    # Collect most-local definition for each fixture name
    all_defs: list[FixtureDef[Any]] = []
    seen_names: set[str] = set()
    for name in sorted(registry._defs):
        defs = registry._defs[name]
        if defs:
            all_defs.append(defs[-1])
            seen_names.add(name)

    # Add type-based built-in fixtures not already in the registry
    all_defs.extend(d for d in _builtin_defs() if d.name not in seen_names)

    total = len(all_defs)

    # Apply filter
    if pattern:
        all_defs = [d for d in all_defs if pattern in d.name]

    if not all_defs:
        if pattern:
            return _dim(f"no fixtures match '{pattern}'", use_color)
        return ""

    # Sort by origin (built-in=0, plugin=1, conftest=2), then namespace, then name
    all_defs.sort(key=lambda d: (_origin_key(d), d.namespace, d.name))

    formatter = {
        0: _format_quiet,
        1: _format_standard,
        2: _format_rich,
    }.get(verbosity, _format_standard)

    lines: list[str] = []
    current_origin = ""
    current_namespace = ""
    is_first_in_section = True

    for defn in all_defs:
        origin = _origin_header(defn)
        ns = defn.namespace

        # Origin header (box top)
        if origin != current_origin:
            if current_origin:
                # Close previous box
                lines.append(_box_bottom(use_color))
                lines.append("")
            lines.append(_box_top(origin, use_color))
            if verbosity > 0:
                lines.append("")
            current_origin = origin
            current_namespace = ""
            is_first_in_section = True

        # Namespace sub-header (within an origin)
        if ns and ns != current_namespace:
            bracket = _dim(f"  [{ns}]", use_color)
            lines.append(bracket)
            current_namespace = ns

        # Blank line between fixtures (not before first in section)
        if not is_first_in_section and verbosity > 0:
            lines.append("")

        lines.append(formatter(defn, use_color))
        is_first_in_section = False

    # Close last box
    if current_origin:
        lines.append(_box_bottom(use_color))

    # Summary line
    shown = len(all_defs)
    if pattern:
        lines.append(_dim(f"  {shown} of {total} fixtures", use_color))
    else:
        s = "" if shown == 1 else "s"
        lines.append(_dim(f"  {shown} fixture{s}", use_color))

    return "\n".join(lines)


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
    for name in sorted(registry._defs):
        defs = registry._defs[name]
        if defs:
            all_defs[name] = defs[-1]
            seen_names.add(name)

    # Add built-in fixtures
    for defn in _builtin_defs():
        if defn.name not in seen_names:
            all_defs[defn.name] = defn

    # Extract deps for each fixture
    graph: dict[str, list[str]] = {}
    for name, defn in all_defs.items():
        deps: list[str] = []
        try:
            sig = inspect.signature(defn.func)
        except (ValueError, TypeError):
            pass
        else:
            deps.extend(
                param_name
                for param_name in sig.parameters
                if param_name in all_defs and param_name != name
            )
        graph[name] = deps

    # Cycle detection via DFS (white/gray/black)
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(all_defs, WHITE)
    cycle_path: list[str] = []

    def _has_cycle(node: str, path: list[str]) -> bool:
        if color[node] == GRAY:
            cycle_path.extend(path[path.index(node) :])
            cycle_path.append(node)
            return True
        if color[node] == BLACK:
            return False
        color[node] = GRAY
        path.append(node)
        for dep in graph.get(node, []):
            if dep in color and _has_cycle(dep, path):
                return True
        path.pop()
        color[node] = BLACK
        return False

    for name in sorted(all_defs):
        if color[name] == WHITE and _has_cycle(name, []):
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
            _render_node(dep, child_prefix, i == len(deps) - 1, False)

    for i, root in enumerate(roots):
        if i > 0:
            lines.append("")
        _render_node(root, "", True, True)

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
