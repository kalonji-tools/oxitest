"""Integration tests: fixture scope interactions."""

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def test_shared_fixture_with_parametrize(tmp: TempDir) -> None:
    """shared=True fixture is instantiated once across all parametrize cases."""
    (tmp / "__fixtures__.py").write_text(
        "from oxitest import Fixture, fixture\n"
        "\n"
        "call_count = 0\n"
        "\n"
        "@fixture(lifetime='package')\n"
        "def db() -> str:\n"
        "    global call_count\n"
        "    call_count += 1\n"
        "    return f'conn-{call_count}'\n",
        encoding="utf-8",
    )
    (tmp / "test_shared_param.py").write_text(
        "from dataclasses import dataclass\n"
        "from oxitest import Fixture\n"
        "import oxitest as oxi\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class Case:\n"
        "    val: int\n"
        "\n"
        "@oxi.parametrize(a=Case(1), b=Case(2), c=Case(3))\n"
        "def test_uses_same_db(db: Fixture[str], case: Case):\n"
        "    # shared fixture: always conn-1 regardless of parametrize case\n"
        "    assert db == 'conn-1', f'expected conn-1, got {db}'\n",
        encoding="utf-8",
    )

    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc, count=3)


def test_shared_and_autouse_both_apply(tmp: TempDir) -> None:
    """shared=True fixture combined with autouse fixture — both are injected."""
    (tmp / "__fixtures__.py").write_text(
        "from oxitest import Fixture, fixture\n"
        "\n"
        "log = []\n"
        "\n"
        "@fixture(lifetime='package')\n"
        "def db() -> str:\n"
        "    return 'shared-conn'\n"
        "\n"
        "@fixture(lifetime='function', autouse=True)\n"
        "def setup():\n"
        "    log.append('setup')\n",
        encoding="utf-8",
    )
    (tmp / "test_both.py").write_text(
        "from oxitest import Fixture\n"
        "\n"
        "def test_a(db: Fixture[str]):\n"
        "    assert db == 'shared-conn'\n"
        "\n"
        "def test_b(db: Fixture[str]):\n"
        "    assert db == 'shared-conn'\n",
        encoding="utf-8",
    )

    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc, count=2)


def test_yield_fixture_teardown_lifo(tmp: TempDir) -> None:
    """Yield fixtures tear down in LIFO order (reverse of setup)."""
    (tmp / "__fixtures__.py").write_text(
        "from oxitest import Fixture, Yields, fixture\n"
        "\n"
        "from state import order\n"
        "\n"
        "@fixture(lifetime='function')\n"
        "def first() -> Yields[str]:\n"
        "    order.append('setup-first')\n"
        "    yield 'first'\n"
        "    order.append('teardown-first')\n"
        "\n"
        "@fixture(lifetime='function')\n"
        "def second(first: Fixture[str]) -> Yields[str]:\n"
        "    order.append('setup-second')\n"
        "    yield f'{first}+second'\n"
        "    order.append('teardown-second')\n",
        encoding="utf-8",
    )
    (tmp / "state.py").write_text("order = []\n", encoding="utf-8")
    (tmp / "test_ordering.py").write_text(
        "from oxitest import Fixture\n"
        "import state\n"
        "\n"
        "def test_uses_both(second: Fixture[str]):\n"
        "    assert second == 'first+second'\n"
        "\n"
        "def test_teardown_was_lifo():\n"
        "    # After test_uses_both tears down, order should show LIFO\n"
        "    assert state.order == [\n"
        "        'setup-first', 'setup-second',\n"
        "        'teardown-second', 'teardown-first',\n"
        "    ], f'unexpected order: {state.order}'\n",
        encoding="utf-8",
    )

    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc, count=2)


def test_skip_takes_precedence_over_xfail(tmp: TempDir) -> None:
    """When both @mark.skip and @mark.xfail are applied, skip takes precedence."""
    (tmp / "test_marks.py").write_text(
        "import oxitest as oxi\n"
        "\n"
        "@oxi.mark.skip(reason='not ready')\n"
        "@oxi.mark.xfail(reason='known bug')\n"
        "def test_both_marks():\n"
        "    assert False\n"
        "\n"
        "def test_plain_pass():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc)
    integ.assert_contains(out, "1 skipped")
    integ.assert_contains(out, "1 passed")
