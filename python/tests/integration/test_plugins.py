"""Integration tests: `oxitest query plugins` subcommand."""

from oxitest import TempDir, helpers


def test_plugins_no_plugins_configured(tmp: TempDir) -> None:
    """`plugins` with no plugins shows 'no plugins configured' and exits 0."""
    (tmp / "pyproject.toml").write_text("[tool.oxitest]\ntestpaths = ['.']\n")
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = helpers.common.run_oxitest_subcmd(tmp, "query", "plugins", cwd=".")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "no results")


def test_plugins_exits_zero(tmp: TempDir) -> None:
    """`plugins` exits 0 even with no pyproject.toml."""
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = helpers.common.run_oxitest_subcmd(tmp, "query", "plugins", cwd=".")
    helpers.integ.assert_passed(out, rc)


def test_plugins_with_configured_plugin(tmp: TempDir) -> None:
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
        "    return Plugin(log_backends=(_Log(),))\n"
    )
    (tmp / "pyproject.toml").write_text(
        "[tool.oxitest]\ntestpaths = ['.']\nplugins = [\"my_plugin\"]\n"
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = helpers.common.run_oxitest_subcmd(tmp, "query", "plugins", cwd=".")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "my_plugin")
    # Protocol details are in the detail card
    out2, _, rc2 = helpers.common.run_oxitest_subcmd(
        tmp,
        "query",
        "plugins",
        "--detail",
        "my_plugin",
        cwd=".",
    )
    helpers.integ.assert_passed(out2, rc2)
    helpers.integ.assert_contains(out2, "LogBackend")
