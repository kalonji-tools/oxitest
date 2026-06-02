"""Integration tests: `fixtures --tree` fixture dependency visualization."""

from conftest import helpers
from oxitest import TempDir


def test_tree_basic_output(tmp: TempDir):
    """`fixtures --tree` shows fixture dependency tree and exits 0."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture\n"
        "def config() -> dict:\n"
        "    return {'host': 'localhost'}\n\n"
        "@fx.fixture\n"
        "def db(config):\n"
        "    return f'connected to {config}'\n"
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = helpers.common.run_oxitest_subcmd_cwd(tmp, "fixtures", "--tree")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "db", "config")
    assert "└── " in out or "├── " in out, f"tree chars missing: {out!r}"


def test_tree_verbose_shows_tags(tmp: TempDir):
    """`fixtures --tree -v` shows shared/async tags."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture(shared=True)\n"
        "def db() -> str:\n"
        "    return 'db'\n"
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = helpers.common.run_oxitest_subcmd_cwd(tmp, "fixtures", "--tree", "-v")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "shared")


def test_tree_cycle_exits_failure(tmp: TempDir):
    """`fixtures --tree` detects circular deps and exits 1."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture\n"
        "def a(b):\n"
        "    return 'a'\n\n"
        "@fx.fixture\n"
        "def b(a):\n"
        "    return 'b'\n"
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, err, rc = helpers.common.run_oxitest_subcmd_cwd(tmp, "fixtures", "--tree")
    helpers.integ.assert_failed(out, rc)
    assert "ircular" in out + err, f"cycle error missing: {out + err!r}"


def test_tree_no_fixtures_shows_builtins(tmp: TempDir):
    """`fixtures --tree` with no conftest still shows built-in fixtures."""
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = helpers.common.run_oxitest_subcmd_cwd(tmp, "fixtures", "--tree")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "TempDir")
