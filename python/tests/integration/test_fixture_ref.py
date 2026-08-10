"""Integration test for FixtureRef[T] through the full CLI pipeline."""

from oxitest import TempDir
from tests import helpers
from tests.integration import helpers as integ


def test_fixture_ref_in_parametrize_resolves_fixture(tmp: TempDir) -> None:
    """FixtureRef in parametrize kwargs should resolve to the fixture value."""
    integ.write_project(
        tmp,
        tests={
            "test_ref.py": """\
                from dataclasses import dataclass
                import oxitest
                from oxitest import Fixture, FixtureRef, fixture

                @fixture(lifetime='function')
                def greeting() -> str:
                    return "hello"

                @fixture(lifetime='function')
                def farewell() -> str:
                    return "goodbye"

                @dataclass(frozen=True)
                class Case:
                    word: FixtureRef[str]
                    expected: str

                @oxitest.parametrize(
                    greet=Case(word=greeting, expected="hello"),
                    bye=Case(word=farewell, expected="goodbye"),
                )
                def test_ref(word: Fixture[str], expected: str) -> None:
                    assert word == expected, f"expected {expected!r}, got {word!r}"
            """,
        },
    )
    out, _, rc = helpers.run_oxitest(tmp)
    integ.assert_passed(out, rc, count=2)
