"""Integration tests: `oxitest plugins` subcommand."""

from conftest import helpers
from oxitest import TempDir


def test_plugins_no_plugins_configured(tmp: TempDir):
    """`plugins` with no plugins shows 'no plugins configured' and exits 0."""
    (tmp / "pyproject.toml").write_text("[tool.oxitest]\ntestpaths = ['.']\n")
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = helpers.common.run_oxitest_subcmd_cwd(tmp, "plugins")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "no plugins configured")


def test_plugins_exits_zero(tmp: TempDir):
    """`plugins` exits 0 even with no pyproject.toml."""
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = helpers.common.run_oxitest_subcmd_cwd(tmp, "plugins")
    helpers.integ.assert_passed(out, rc)


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
    out, _, rc = helpers.common.run_oxitest_subcmd_cwd(tmp, "plugins")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "my_plugin", "LogBackend")
