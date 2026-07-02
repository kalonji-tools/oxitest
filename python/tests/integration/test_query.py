"""Integration tests for the ``oxitest query`` subcommand."""

from __future__ import annotations

import json

from oxitest import TempDir, helpers


def test_query_tests_lists_all(tmp: TempDir):
    """``query tests`` lists all collected test names."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_math.py": """\
def test_add():
    assert 1 + 1 == 2

def test_sub():
    assert 2 - 1 == 1
""",
        },
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(tmp, "query", "tests")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "test_add", "test_sub")


def test_query_tests_with_expression(tmp: TempDir):
    """``query tests -E 'name(~add)'`` filters by name predicate."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_math.py": """\
def test_add():
    assert 1 + 1 == 2

def test_sub():
    assert 2 - 1 == 1
""",
        },
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(
        tmp, "query", "tests", "-E", "name(~add)"
    )
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "test_add")
    helpers.integ.assert_excludes(out, "test_sub")


def test_query_tests_count(tmp: TempDir):
    """``query tests --count`` shows only the count."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_math.py": """\
def test_add():
    assert 1 + 1 == 2

def test_sub():
    assert 2 - 1 == 1
""",
        },
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(tmp, "query", "tests", "--count")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "2")


def test_query_tests_jsonl(tmp: TempDir):
    """``query tests --jsonl`` emits valid JSON lines."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_one.py": "def test_solo(): pass\n",
        },
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(tmp, "query", "tests", "--jsonl")
    helpers.integ.assert_passed(out, rc)
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    assert len(lines) >= 1, f"expected at least 1 JSON line, got: {out}"
    obj = json.loads(lines[0])
    assert "name" in obj, f"expected 'name' field in JSON: {obj}"


def test_query_marks(tmp: TempDir):
    """``query marks`` includes marks used in test files."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_marked.py": """\
import oxitest as oxi

@oxi.mark.slow
def test_heavy():
    pass
""",
        },
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(tmp, "query", "marks")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "slow")


def test_query_detail_single(tmp: TempDir):
    """``query tests --detail test_one`` shows a detail card."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": "def test_one(): pass\ndef test_two(): pass\n",
        },
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(
        tmp, "query", "tests", "--detail", "test_one"
    )
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "test_one")


def test_query_invalid_predicate(tmp: TempDir):
    """``query tests -E 'shared()'`` fails — shared is not valid for tests."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": "def test_one(): pass\n",
        },
    )
    _out, _err, rc = helpers.common.run_oxitest_subcmd(
        tmp, "query", "tests", "-E", "shared()"
    )
    assert rc != 0, f"expected non-zero exit for invalid predicate, got {rc}"


def test_query_helpers(tmp: TempDir):
    """``query helpers`` shows helper functions from conftest."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": "def test_one(): pass\n",
        },
        conftest="""\
            from oxitest import Helpers

            utils = Helpers()

            @utils.helper
            def my_helper():
                return 42
        """,
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(tmp, "query", "helpers")
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "my_helper")


def test_query_helpers_shows_docstring(tmp: TempDir):
    """``query helpers --detail`` includes helper docstrings."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": "def test_one(): pass\n",
        },
        conftest="""\
            from oxitest import Helpers

            utils = Helpers()

            @utils.helper
            def make_db():
                \"\"\"Create a test database.\"\"\"
                return {}
        """,
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(
        tmp, "query", "helpers", "--detail", "make_db"
    )
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "Create a test database.")


def test_query_fixtures_uses_predicate(tmp: TempDir):
    """``query fixtures -E 'uses(db)'`` filters fixtures by dependency."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": """\
from oxitest import Fixture

def test_one(conn: Fixture[object]):
    pass
""",
        },
        conftest="""\
from oxitest import Fixtures, Fixture

fx = Fixtures()

@fx.fixture
def db() -> object:
    return object()

@fx.fixture
def conn(db: Fixture[object]) -> object:
    return db
""",
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(
        tmp, "query", "fixtures", "-E", "uses(db)"
    )
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "conn")
    helpers.integ.assert_excludes(out, "db")


def test_query_tests_uses_predicate(tmp: TempDir):
    """``query tests -E 'uses(db)'`` filters tests by fixture usage."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": """\
from oxitest import Fixture

def test_with_db(db: Fixture[object]):
    pass

def test_without():
    pass
""",
        },
        conftest="""\
from oxitest import Fixtures

fx = Fixtures()

@fx.fixture
def db() -> object:
    return object()
""",
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(
        tmp, "query", "tests", "-E", "uses(db)"
    )
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "test_with_db")
    helpers.integ.assert_excludes(out, "test_without")


def test_query_fixtures_shows_docstring(tmp: TempDir):
    """``query fixtures --detail`` includes fixture docstrings."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": """\
                from oxitest import Fixture

                def test_one(db: Fixture[object]):
                    pass
            """,
        },
        conftest="""\
            from oxitest import Fixtures

            fx = Fixtures()

            @fx.fixture
            def db() -> object:
                \"\"\"Provide a database connection.\"\"\"
                return object()
        """,
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(
        tmp, "query", "fixtures", "--detail", "db"
    )
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "Provide a database connection.")


def test_query_tests_uses_substring_match(tmp: TempDir):
    """``query tests -E 'uses(~db)'`` matches fixtures by substring."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": """\
from oxitest import Fixture

def test_with_db(db_conn: Fixture[object]):
    pass

def test_without():
    pass
""",
        },
        conftest="""\
from oxitest import Fixtures

fx = Fixtures()

@fx.fixture
def db_conn() -> object:
    return object()
""",
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(
        tmp, "query", "tests", "-E", "uses(~db)"
    )
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "test_with_db")
    helpers.integ.assert_excludes(out, "test_without")


def test_query_tests_uses_exact_match(tmp: TempDir):
    """``query tests -E 'uses(=db)'`` requires exact fixture name match."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": """\
from oxitest import Fixture

def test_db(db: Fixture[object]):
    pass

def test_db_conn(db_conn: Fixture[object]):
    pass
""",
        },
        conftest="""\
from oxitest import Fixtures

fx = Fixtures()

@fx.fixture
def db() -> object:
    return object()

@fx.fixture
def db_conn() -> object:
    return object()
""",
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(
        tmp, "query", "tests", "-E", "uses(=db)"
    )
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "test_db")
    helpers.integ.assert_excludes(out, "test_db_conn")


def test_query_tests_uses_composed_with_mark(tmp: TempDir):
    """``query tests -E 'uses(db) & mark(slow)'`` composes predicates."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_a.py": """\
import oxitest as oxi
from oxitest import Fixture

@oxi.mark.slow
def test_slow_db(db: Fixture[object]):
    pass

def test_fast_db(db: Fixture[object]):
    pass

@oxi.mark.slow
def test_slow_no_db():
    pass
""",
        },
        conftest="""\
from oxitest import Fixtures

fx = Fixtures()

@fx.fixture
def db() -> object:
    return object()
""",
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(
        tmp, "query", "tests", "-E", "uses(db) & mark(slow)"
    )
    helpers.integ.assert_passed(out, rc)
    helpers.integ.assert_contains(out, "test_slow_db")
    helpers.integ.assert_excludes(out, "test_fast_db")
    helpers.integ.assert_excludes(out, "test_slow_no_db")
