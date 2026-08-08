"""Integration tests for positional node ID targeting (#720).

Verifies the full flow: CLI parsing -> file collection -> node ID filtering
-> execution -> output. These tests catch integration bugs that unit tests
miss (path canonicalization, conftest discovery, strict mode interaction).
"""

from __future__ import annotations

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ

# ── Single node ID ───────────────────────────────────────────────────────────


def test_run_single_node_id(tmp: TempDir) -> None:
    """``oxitest run path::test_name`` runs exactly one test."""
    integ.write_project(
        tmp,
        pyproject="# oxitest",
        tests={
            "test_math.py": """\
def test_add():
    assert 1 + 1 == 2

def test_sub():
    assert 2 - 1 == 1
""",
        },
    )
    out, _, rc = helpers.run_oxitest_subcmd(
        tmp,
        "run",
        "test_math.py::test_add",
        cwd=".",
    )
    integ.assert_passed(out, rc, count=1)


def test_run_node_id_nonexistent_test_collects_zero(tmp: TempDir) -> None:
    """Targeting a test that doesn't exist collects zero items."""
    integ.write_project(
        tmp,
        pyproject="# oxitest",
        tests={
            "test_math.py": """\
def test_add():
    assert True
""",
        },
    )
    out, *_ = helpers.run_oxitest_subcmd(
        tmp,
        "run",
        "test_math.py::test_nonexistent",
        cwd=".",
    )
    integ.assert_contains(out, "no tests ran")


# ── Class-based node IDs ─────────────────────────────────────────────────────


def test_run_class_method_node_id(tmp: TempDir) -> None:
    """``oxitest run path::TestClass::test_method`` targets a class method."""
    integ.write_project(
        tmp,
        pyproject="# oxitest",
        tests={
            "test_cls.py": """\
class TestSuite:
    def test_alpha(self):
        assert True

    def test_beta(self):
        assert True

def test_standalone():
    assert True
""",
        },
    )
    out, _, rc = helpers.run_oxitest_subcmd(
        tmp,
        "run",
        "test_cls.py::TestSuite::test_alpha",
        cwd=".",
    )
    integ.assert_passed(out, rc, count=1)


def test_run_class_prefix_selects_all_methods(tmp: TempDir) -> None:
    """``oxitest run path::TestClass`` runs all methods in the class."""
    integ.write_project(
        tmp,
        pyproject="# oxitest",
        tests={
            "test_cls.py": """\
class TestSuite:
    def test_alpha(self):
        assert True

    def test_beta(self):
        assert True

def test_standalone():
    assert True
""",
        },
    )
    out, _, rc = helpers.run_oxitest_subcmd(
        tmp,
        "run",
        "test_cls.py::TestSuite",
        cwd=".",
    )
    integ.assert_passed(out, rc, count=2)


# ── Parametrized node IDs ────────────────────────────────────────────────────


def test_run_parametrized_prefix_matches_all_cases(tmp: TempDir) -> None:
    """Node ID without brackets matches all parametrized variants."""
    integ.write_project(
        tmp,
        pyproject="# oxitest",
        tests={
            "test_param.py": """\
from dataclasses import dataclass
import oxitest

@dataclass(frozen=True)
class Case:
    x: int
    y: int
    expected: int

@oxitest.parametrize(
    add=Case(1, 2, 3),
    sub=Case(5, 3, 2),
)
def test_math(x: int, y: int, expected: int):
    assert x + y == expected or x - y == expected
""",
        },
    )
    out, _, rc = helpers.run_oxitest_subcmd(
        tmp,
        "run",
        "test_param.py::test_math",
        cwd=".",
    )
    integ.assert_passed(out, rc, count=2)


def test_run_parametrized_exact_case(tmp: TempDir) -> None:
    """Node ID with brackets matches only that parametrized case."""
    integ.write_project(
        tmp,
        pyproject="# oxitest",
        tests={
            "test_param.py": """\
from dataclasses import dataclass
import oxitest

@dataclass(frozen=True)
class Case:
    x: int
    expected: int

@oxitest.parametrize(
    double=Case(2, 4),
    triple=Case(3, 9),
)
def test_mul(x: int, expected: int):
    assert x * 2 == expected or x * 3 == expected
""",
        },
    )
    out, _, rc = helpers.run_oxitest_subcmd(
        tmp,
        "run",
        "test_param.py::test_mul[double]",
        cwd=".",
    )
    integ.assert_passed(out, rc, count=1)


# ── Mixed paths and node IDs ────────────────────────────────────────────────


def test_run_mixed_path_and_node_id(tmp: TempDir) -> None:
    """Bare path runs all tests; node ID runs just one. Mixed works."""
    integ.write_project(
        tmp,
        pyproject="# oxitest",
        tests={
            "test_a.py": """\
def test_one():
    assert True

def test_two():
    assert True
""",
            "test_b.py": """\
def test_three():
    assert True
""",
        },
    )
    out, _, rc = helpers.run_oxitest_subcmd(
        tmp,
        "run",
        "test_a.py::test_one",
        "test_b.py",
        cwd=".",
    )
    integ.assert_passed(out, rc, count=2)


# ── Strict mode with subset targeting ────────────────────────────────────────


def test_strict_with_single_file_no_false_unused(tmp: TempDir) -> None:
    """Strict mode doesn't flag unused fixtures when targeting a file subset."""
    integ.write_project(
        tmp,
        tests={
            "test_a.py": """\
def test_simple():
    assert True, "trivially true"
""",
            "test_b.py": """\
from oxitest import Fixture

def test_uses_db(db: Fixture[str]):
    assert db == "connected", f"expected 'connected', got {db!r}"
""",
        },
        conftest="""\
import oxitest

fx = oxitest.Fixtures()  # oxitest: allow[registrar-in-test-module]

@fx.fixture
def db() -> str:
    return "connected"
""",
        pyproject="""\
[tool.oxitest]
strict = "abort"
""",
    )
    out, _, rc = helpers.run_oxitest_subcmd(tmp, "run", "test_a.py", cwd=".")
    integ.assert_passed(out, rc, count=1)
    integ.assert_excludes(out, "unused-fixture")


def test_strict_with_node_id_no_false_unused(tmp: TempDir) -> None:
    """Strict mode doesn't flag unused fixtures when targeting via node ID."""
    integ.write_project(
        tmp,
        tests={
            "test_a.py": """\
from oxitest import Fixture

def test_no_fixture():
    assert True, "trivially true"

def test_uses_db(db: Fixture[str]):
    assert db == "connected", f"expected 'connected', got {db!r}"
""",
        },
        conftest="""\
import oxitest

fx = oxitest.Fixtures()  # oxitest: allow[registrar-in-test-module]

@fx.fixture
def db() -> str:
    return "connected"
""",
        pyproject="""\
[tool.oxitest]
strict = "abort"
""",
    )
    out, _, rc = helpers.run_oxitest_subcmd(
        tmp,
        "run",
        "test_a.py::test_no_fixture",
        cwd=".",
    )
    integ.assert_passed(out, rc, count=1)
    integ.assert_excludes(out, "unused-fixture")


# ── Conftest ancestor discovery ──────────────────────────────────────────────


def test_node_id_in_nested_dir_loads_ancestor_conftests(tmp: TempDir) -> None:
    """Node ID targeting a file in a subdirectory loads all ancestor conftests."""
    (tmp / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp / "conftest.py").write_text(
        "import oxitest\nfx = oxitest.Fixtures()\n\n"
        "@fx.fixture\ndef root_val() -> str:\n    return 'from_root'\n",
        encoding="utf-8",
    )
    sub = tmp / "tests"
    sub.mkdir()
    (sub / "conftest.py").write_text(
        "import oxitest\nfx = oxitest.Fixtures()\n\n"
        "@fx.fixture\ndef sub_val() -> str:\n    return 'from_sub'\n",
        encoding="utf-8",
    )
    deep = sub / "deep"
    deep.mkdir()
    (deep / "test_deep.py").write_text(
        "from oxitest import Fixture\n\n"
        "def test_sees_root(root_val: Fixture[str]):\n"
        "    assert root_val == 'from_root'\n\n"
        "def test_sees_sub(sub_val: Fixture[str]):\n"
        "    assert sub_val == 'from_sub'\n",
        encoding="utf-8",
    )
    out, _, rc = helpers.run_oxitest_subcmd(
        tmp,
        "run",
        "tests/deep/test_deep.py::test_sees_root",
        cwd=".",
    )
    integ.assert_passed(out, rc, count=1)


# ── Multiple node IDs ────────────────────────────────────────────────────────


def test_run_multiple_node_ids(tmp: TempDir) -> None:
    """Multiple node IDs from different files run exactly those tests."""
    integ.write_project(
        tmp,
        pyproject="# oxitest",
        tests={
            "test_a.py": """\
def test_alpha():
    assert True

def test_beta():
    assert True
""",
            "test_b.py": """\
def test_gamma():
    assert True
""",
        },
    )
    out, _, rc = helpers.run_oxitest_subcmd(
        tmp,
        "run",
        "test_a.py::test_alpha",
        "test_b.py::test_gamma",
        cwd=".",
    )
    integ.assert_passed(out, rc, count=2)
