"""Integration tests: `oxitest plugins` subcommand."""

import subprocess
import sys

from oxitest import TempDir


def _run_plugins(tmp: TempDir, *extra_args: str) -> tuple[str, str, int]:
    """Run `oxitest plugins` with cwd set to tmp."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oxitest",
            "plugins",
            "--color",
            "never",
            *extra_args,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp),
    )
    return result.stdout, result.stderr, result.returncode


def test_plugins_no_plugins_configured(tmp: TempDir):
    """`plugins` with no plugins shows 'no plugins configured' and exits 0."""
    (tmp / "pyproject.toml").write_text("[tool.oxitest]\ntestpaths = ['.']\n")
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = _run_plugins(tmp)
    assert rc == 0, f"expected exit 0, got {rc}\nstdout: {out}"
    assert "no plugins configured" in out, (
        f"expected 'no plugins configured' in: {out!r}"
    )


def test_plugins_exits_zero(tmp: TempDir):
    """`plugins` exits 0 even with no pyproject.toml."""
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = _run_plugins(tmp)
    assert rc == 0, f"expected exit 0, got {rc}\nstdout: {out}"


def test_plugins_with_configured_plugin(tmp: TempDir):
    """`plugins` with a real plugin shows its name and protocols."""
    # Create a minimal plugin module in the tmp directory.
    (tmp / "my_plugin.py").write_text(
        "from oxitest.plugin import Plugin\n\n"
        "class _Log:\n"
        "    def install(self): ...\n"
        "    def uninstall(self): ...\n"
        "    @property\n"
        "    def records(self): return []\n\n"
        "def oxitest_plugin(config=None):\n"
        "    return Plugin(log_backends=[_Log()])\n"
    )
    (tmp / "pyproject.toml").write_text(
        "[tool.oxitest]\ntestpaths = ['.']\nplugins = [\"my_plugin\"]\n"
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, err, rc = _run_plugins(tmp)
    assert rc == 0, f"expected exit 0, got {rc}\nstderr: {err}\nstdout: {out}"
    assert "my_plugin" in out, f"expected 'my_plugin' in output: {out!r}"
    assert "LogBackend" in out, f"expected 'LogBackend' in output: {out!r}"
