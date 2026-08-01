"""Integration tests: explicit helper registration via Helpers()."""

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def test_explicit_helper_registration(tmp: TempDir) -> None:
    """Helpers registered via Helpers() are accessible via ``helpers``."""
    integ.write_project(
        tmp,
        tests={
            "test_it.py": """\
                from oxitest import helpers

                def test_helper_call() -> None:
                    result = helpers.utils.greet("world")
                    assert result == "hi world", f"expected greeting, got {result}"
            """,
        },
        conftest="""\
            from oxitest import Helpers

            utils = Helpers()

            @utils.helper
            def greet(name: str) -> str:
                return f"hi {name}"
        """,
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc, count=1)


def test_helpers_in_test_file_strict_violation(tmp: TempDir) -> None:
    """Helpers() in a test file triggers registrar-in-test-module under --strict."""
    integ.write_project(
        tmp,
        tests={
            "test_it.py": """\
                from oxitest import Helpers

                h = Helpers()

                @h.helper
                def nope() -> str:
                    return "nope"

                def test_pass() -> None:
                    assert True, "test body is fine"
            """,
        },
    )
    out, _, rc = helpers.run_oxitest(tmp, "--strict")
    integ.assert_failed(out, rc)
    integ.assert_contains(out, "registrar-in-test-module")


def test_multiple_helper_namespaces(tmp: TempDir) -> None:
    """Multiple Helpers() instances in one conftest create separate namespaces."""
    integ.write_project(
        tmp,
        tests={
            "test_it.py": """\
                from oxitest import helpers

                def test_math_add() -> None:
                    result = helpers.math.add(2, 3)
                    assert result == 5, f"expected 5, got {result}"

                def test_str_shout() -> None:
                    result = helpers.strings.shout("hi")
                    assert result == "HI!", f"expected 'HI!', got {result}"
            """,
        },
        conftest="""\
            from oxitest import Helpers

            math = Helpers()
            strings = Helpers()

            @math.helper
            def add(a: int, b: int) -> int:
                return a + b

            @strings.helper
            def shout(s: str) -> str:
                return s.upper() + "!"
        """,
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc, count=2)


def test_nested_conftest_helper_access(tmp: TempDir) -> None:
    """Child conftest helpers accessible alongside parent conftest helpers."""
    integ.write_project(
        tmp,
        tests={
            "test_root.py": """\
                from oxitest import helpers

                def test_root_helper() -> None:
                    result = helpers.root.ping()
                    assert result == "pong", f"expected 'pong', got {result}"
            """,
        },
        conftest="""\
            from oxitest import Helpers

            root = Helpers()

            @root.helper
            def ping() -> str:
                return "pong"
        """,
    )
    # Also write a sub-directory with its own conftest and test
    sub = tmp / "sub"
    sub.mkdir()
    (sub / "conftest.py").write_text(
        "from oxitest import Helpers\n\n"
        "child = Helpers()\n\n"
        "@child.helper\n"
        "def echo(msg: str) -> str:\n"
        "    return msg\n"
    )
    (sub / "test_sub.py").write_text(
        "from oxitest import helpers\n\n"
        "def test_child_helper() -> None:\n"
        "    result = helpers.child.echo('hello')\n"
        "    assert result == 'hello', f\"expected 'hello', got {result}\"\n\n"
        "def test_root_from_sub() -> None:\n"
        "    result = helpers.root.ping()\n"
        "    assert result == 'pong', f\"expected 'pong', got {result}\"\n"
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc, count=3)
