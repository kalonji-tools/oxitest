"""Integration tests: ``oxitest debug`` subcommand."""

from oxitest import TempDir
from tests import helpers

# -- Plugin scaffold -----------------------------------------------------------

_DEBUG_PLUGIN = """\
from types import TracebackType
from oxitest.plugin import Plugin


class _NoOpDebugger:
    \"\"\"No-op DebuggerBackend — oxitest prints banners, the backend need not.\"\"\"

    def trace(self) -> None:
        pass

    def post_mortem(self, tb: TracebackType) -> None:
        pass


def oxitest_plugin(*, config=None):
    return Plugin(debugger_backend=_NoOpDebugger())
"""

_PYPROJECT = """\
[tool.oxitest]
testpaths = ["."]
plugins = ["debug_plugin"]
"""


def _run_debug(
    tmp: TempDir, test_code: str, *, always: bool = False
) -> tuple[str, str, int]:
    """Write test + plugin, run ``oxitest debug``, return (stdout, stderr, rc)."""
    (tmp / "test_it.py").write_text(test_code, encoding="utf-8")
    (tmp / "debug_plugin.py").write_text(_DEBUG_PLUGIN, encoding="utf-8")
    (tmp / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    args = ["debug"]
    if always:
        args.append("--always")
    return helpers.run_oxitest_subcmd(tmp, *args, cwd=".")


# -- Tests ---------------------------------------------------------------------


def test_post_mortem_on_failure(tmp: TempDir) -> None:
    """``oxitest debug`` on a failing test should print a DEBUG banner."""
    _, stderr, rc = _run_debug(tmp, "def test_fail(): assert 1 == 2\n")
    assert rc != 0, f"expected non-zero exit, got {rc}"
    assert "── DEBUG" in stderr, (
        f"expected DEBUG banner in stderr when test fails:\n{stderr}"
    )


def test_post_mortem_on_pass(tmp: TempDir) -> None:
    """``oxitest debug`` on a passing test should produce no debug banners."""
    _, stderr, rc = _run_debug(tmp, "def test_ok(): assert True\n")
    assert rc == 0, f"expected exit 0, got {rc}\n{stderr}"
    assert "── DEBUG" not in stderr, (
        f"unexpected DEBUG banner in stderr when test passes:\n{stderr}"
    )
    assert "── TRACE" not in stderr, (
        f"unexpected TRACE banner in stderr when test passes:\n{stderr}"
    )


def test_always_mode_on_pass(tmp: TempDir) -> None:
    """``oxitest debug --always`` should print TRACE banner even on pass."""
    _, stderr, rc = _run_debug(tmp, "def test_ok(): assert True\n", always=True)
    assert rc == 0, f"expected exit 0, got {rc}\n{stderr}"
    assert "── TRACE" in stderr, (
        f"expected TRACE banner in stderr with --always:\n{stderr}"
    )


def test_always_mode_on_failure(tmp: TempDir) -> None:
    """``oxitest debug --always`` on failure should print both TRACE and DEBUG."""
    _, stderr, rc = _run_debug(tmp, "def test_fail(): assert 1 == 2\n", always=True)
    assert rc != 0, f"expected non-zero exit, got {rc}"
    assert "── TRACE" in stderr, (
        f"expected TRACE banner in stderr with --always:\n{stderr}"
    )
    assert "── DEBUG" in stderr, (
        f"expected DEBUG banner in stderr when test fails:\n{stderr}"
    )


def test_skip_not_debuggable(tmp: TempDir) -> None:
    """Skipped tests bypass execution entirely — no TRACE or DEBUG banners."""
    test_code = (
        "import oxitest\n\n"
        "@oxitest.mark.skip(reason='not ready')\n"
        "def test_skipped(): pass\n"
    )
    _, stderr, rc = _run_debug(tmp, test_code, always=True)
    assert rc == 0, f"expected exit 0, got {rc}\n{stderr}"
    assert "── TRACE" not in stderr, (
        f"unexpected TRACE banner for skipped test — skip bypasses run_base:\n{stderr}"
    )
    assert "── DEBUG" not in stderr, (
        f"unexpected DEBUG banner for skipped test:\n{stderr}"
    )
