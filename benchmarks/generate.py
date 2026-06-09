"""Generate bench test files for all tiers.

Creates test files in benchmarks/generated/ with oxitest and pytest variants.
Each file has ~12 tests: ~50% trivial, ~30% fixture, ~20% parametrize.
All tests use only stdlib operations — no external dependencies.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

BENCH_DIR = Path(__file__).parent
GENERATED = BENCH_DIR / "generated"

TIERS: dict[str, int] = {
    "below_threshold": 30,
    "s": 150,
    "m": 500,
    "l": 1000,
    "realistic": 500,
}

TESTS_PER_FILE = 12
TRIVIAL_PER_FILE = 6
FIXTURE_PER_FILE = 4
PARAMETRIZE_PER_FILE = 2
PARAMETRIZE_CASES = 4


def _oxitest_conftest() -> str:
    return textwrap.dedent("""\
        from __future__ import annotations

        import oxitest as oxi

        bench = oxi.Fixtures()


        @bench.fixture
        def data() -> dict:  # type: ignore[return]
            d: dict = {}
            yield d
            d.clear()


        @bench.fixture(shared=True)
        def seed_data() -> dict:
            return {f"key_{i}": i for i in range(100)}
    """)


def _oxitest_file(file_idx: int, test_offset: int) -> str:
    lines = [
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "",
        "import oxitest as oxi",
        "",
    ]
    idx = test_offset

    # Trivial tests (~50%)
    for _ in range(TRIVIAL_PER_FILE):
        lines.append("")
        lines.append(f"def test_trivial_{idx}() -> None:")
        lines.append(f"    d = {{}}; d['k{idx}'] = {idx}")
        lines.append(f"    assert d['k{idx}'] == {idx}, ''")
        idx += 1

    # Fixture tests (~30%) — first uses shared seed_data, rest use per-test data
    for i in range(FIXTURE_PER_FILE):
        lines.append("")
        if i == 0:
            lines.append(
                f"def test_fixture_{idx}(seed_data: oxi.Fixture[dict]) -> None:"
            )
            lines.append("    assert 'key_0' in seed_data, ''")
        else:
            lines.append(f"def test_fixture_{idx}(data: oxi.Fixture[dict]) -> None:")
            lines.append(f"    data['k{idx}'] = {idx}")
            lines.append(f"    assert data['k{idx}'] == {idx}, ''")
        idx += 1

    # Parametrize tests (~20%)
    for _ in range(PARAMETRIZE_PER_FILE):
        lines.append("")
        lines.append("@dataclass(frozen=True)")
        lines.append(f"class Case{idx}:")
        lines.append("    value: int")
        lines.append("")
        cases = ", ".join(
            f"c{c}=Case{idx}(value={c})" for c in range(PARAMETRIZE_CASES)
        )
        lines.append(f"@oxi.parametrize({cases})")
        lines.append(
            f"def test_param_{idx}(case: Case{idx}, data: oxi.Fixture[dict]) -> None:"
        )
        lines.append(f"    data['k{idx}'] = case.value")
        lines.append(f"    assert data['k{idx}'] == case.value, ''")
        idx += 1

    lines.append("")
    return "\n".join(lines)


def _oxitest_realistic_file(file_idx: int, test_offset: int) -> str:
    """Like _oxitest_file but every test body includes time.sleep(0.01)."""
    lines = [
        "from __future__ import annotations",
        "",
        "import time",
        "from dataclasses import dataclass",
        "",
        "import oxitest as oxi",
        "",
    ]
    idx = test_offset

    # Trivial tests with sleep (~50%)
    for _ in range(TRIVIAL_PER_FILE):
        lines.append("")
        lines.append(f"def test_trivial_{idx}() -> None:")
        lines.append("    time.sleep(0.01)")
        lines.append(f"    d = {{}}; d['k{idx}'] = {idx}")
        lines.append(f"    assert d['k{idx}'] == {idx}, ''")
        idx += 1

    # Fixture tests with sleep (~30%)
    for i in range(FIXTURE_PER_FILE):
        lines.append("")
        if i == 0:
            lines.append(
                f"def test_fixture_{idx}(seed_data: oxi.Fixture[dict]) -> None:"
            )
            lines.append("    time.sleep(0.01)")
            lines.append("    assert 'key_0' in seed_data, ''")
        else:
            lines.append(f"def test_fixture_{idx}(data: oxi.Fixture[dict]) -> None:")
            lines.append("    time.sleep(0.01)")
            lines.append(f"    data['k{idx}'] = {idx}")
            lines.append(f"    assert data['k{idx}'] == {idx}, ''")
        idx += 1

    # Parametrize tests with sleep (~20%)
    for _ in range(PARAMETRIZE_PER_FILE):
        lines.append("")
        lines.append("@dataclass(frozen=True)")
        lines.append(f"class Case{idx}:")
        lines.append("    value: int")
        lines.append("")
        cases = ", ".join(
            f"c{c}=Case{idx}(value={c})" for c in range(PARAMETRIZE_CASES)
        )
        lines.append(f"@oxi.parametrize({cases})")
        lines.append(
            f"def test_param_{idx}(case: Case{idx}, data: oxi.Fixture[dict]) -> None:"
        )
        lines.append("    time.sleep(0.01)")
        lines.append(f"    data['k{idx}'] = case.value")
        lines.append(f"    assert data['k{idx}'] == case.value, ''")
        idx += 1

    lines.append("")
    return "\n".join(lines)


def _pytest_file(file_idx: int, test_offset: int) -> str:
    lines = [
        "from __future__ import annotations",
        "",
        "import pytest",
        "",
        "",
        "@pytest.fixture",
        "def data():",
        "    d = {}",
        "    yield d",
        "    d.clear()",
        "",
    ]
    idx = test_offset

    # Trivial tests (~50%)
    for _ in range(TRIVIAL_PER_FILE):
        lines.append("")
        lines.append(f"def test_trivial_{idx}() -> None:")
        lines.append(f"    d = {{}}; d['k{idx}'] = {idx}")
        lines.append(f"    assert d['k{idx}'] == {idx}")
        idx += 1

    # Fixture tests (~30%)
    for _ in range(FIXTURE_PER_FILE):
        lines.append("")
        lines.append(f"def test_fixture_{idx}(data) -> None:")
        lines.append(f"    data['k{idx}'] = {idx}")
        lines.append(f"    assert data['k{idx}'] == {idx}")
        idx += 1

    # Parametrize tests (~20%)
    for _ in range(PARAMETRIZE_PER_FILE):
        lines.append("")
        cases = ", ".join(str(c) for c in range(PARAMETRIZE_CASES))
        lines.append(f'@pytest.mark.parametrize("value", [{cases}])')
        lines.append(f"def test_param_{idx}(value: int, data) -> None:")
        lines.append(f"    data['k{idx}'] = value")
        lines.append(f"    assert data['k{idx}'] == value")
        idx += 1

    lines.append("")
    return "\n".join(lines)


def _startup_file() -> str:
    return textwrap.dedent("""\
        from __future__ import annotations


        def test_noop() -> None:
            pass
    """)


def generate() -> None:
    if GENERATED.exists():
        shutil.rmtree(GENERATED)
    GENERATED.mkdir()

    # Startup tier
    startup_dir = GENERATED / "startup"
    startup_dir.mkdir()
    (startup_dir / "test_noop.py").write_text(_startup_file())

    # Tiered suites
    for tier_name, total_tests in TIERS.items():
        file_count = max(1, total_tests // TESTS_PER_FILE)

        for variant, writer, needs_conftest in [
            (
                "oxitest",
                _oxitest_realistic_file if tier_name == "realistic" else _oxitest_file,
                True,
            ),
            ("pytest", _pytest_file, False),
        ]:
            tier_dir = GENERATED / tier_name / variant
            tier_dir.mkdir(parents=True, exist_ok=True)

            if needs_conftest:
                (tier_dir / "conftest.py").write_text(_oxitest_conftest())

            for i in range(file_count):
                test_offset = i * TESTS_PER_FILE
                content = writer(i, test_offset)
                (tier_dir / f"test_gen_{i}.py").write_text(content)

    # Summary
    for tier_name, total_tests in TIERS.items():
        file_count = max(1, total_tests // TESTS_PER_FILE)
        actual = file_count * TESTS_PER_FILE
        print(f"  {tier_name}: {file_count} files, {actual} tests")
    print("  startup: 1 file, 1 test")
    print("done.")


if __name__ == "__main__":
    generate()
