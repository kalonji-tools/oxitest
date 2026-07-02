"""Integration test for FixtureRef[T] through the full CLI pipeline."""

from oxitest import TempDir, helpers


def test_fixture_ref_in_parametrize_resolves_fixture(tmp: TempDir):
    """FixtureRef in parametrize kwargs should resolve to the fixture value."""
    helpers.integ.write_project(
        tmp,
        tests={
            "test_ref.py": """\
                from dataclasses import dataclass
                import oxitest
                from oxitest import Fixture, FixtureRef

                fx = oxitest.Fixtures()  # oxitest: allow[registrar-in-test-module]

                @fx.fixture
                def greeting() -> str:
                    return "hello"

                @fx.fixture
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
    out, _, rc = helpers.common.run_oxitest(tmp)
    helpers.integ.assert_passed(out, rc, count=2)
