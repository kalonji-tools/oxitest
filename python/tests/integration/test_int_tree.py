"""Integration tests: --tree fixture dependency visualization."""

from conftest import helpers
from oxitest import TempDir


def test_tree_basic_output(tmp: TempDir):
    """--tree shows fixture dependency tree and exits 0."""
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
    out, _, rc = helpers.common.run_oxitest(tmp, "--tree")
    assert rc == 0, f"--tree should exit 0, got {rc}. stderr: {out}"
    assert "db" in out, f"db fixture missing: {out!r}"
    assert "config" in out, f"config dep missing: {out!r}"
    assert "└── " in out or "├── " in out, f"tree chars missing: {out!r}"


def test_tree_with_keyword_filter(tmp: TempDir):
    """--tree -k filters which fixtures are roots."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture\n"
        "def config() -> str:\n"
        "    return 'cfg'\n\n"
        "@fx.fixture\n"
        "def db(config):\n"
        "    return 'db'\n"
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = helpers.common.run_oxitest(tmp, "--tree", "-k", "db")
    assert rc == 0, f"exit code: {rc}"
    # db is a root, config appears only as a dep
    lines = out.strip().split("\n")
    root_lines = [
        line
        for line in lines
        if line
        and not line[0].isspace()
        and not line.startswith("─")
        and "──" not in line[:4]
    ]
    assert any("db" in line for line in root_lines), f"db not a root: {lines}"


def test_tree_verbose_shows_tags(tmp: TempDir):
    """--tree -v shows shared/async tags."""
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n\n"
        "fx = Fixtures()\n\n"
        "@fx.fixture(shared=True)\n"
        "def db() -> str:\n"
        "    return 'db'\n"
    )
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = helpers.common.run_oxitest(tmp, "--tree", "-v")
    assert rc == 0, f"exit code: {rc}"
    assert "shared" in out, f"shared tag missing: {out!r}"


def test_tree_cycle_exits_failure(tmp: TempDir):
    """--tree detects circular deps and exits 1."""
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
    out, err, rc = helpers.common.run_oxitest(tmp, "--tree")
    assert rc != 0, f"cycle should cause failure, got rc={rc}"
    combined = out + err
    assert "ircular" in combined, f"cycle error missing: {combined!r}"


def test_tree_no_fixtures_shows_builtins(tmp: TempDir):
    """--tree with no conftest still shows built-in fixtures."""
    (tmp / "test_example.py").write_text("def test_one(): pass\n")
    out, _, rc = helpers.common.run_oxitest(tmp, "--tree")
    assert rc == 0, f"exit code: {rc}"
    assert "TempDir" in out, f"built-in TempDir missing: {out!r}"
