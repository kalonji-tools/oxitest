"""Integration tests for the deprecated ``oxitest fixtures`` subcommand."""

from __future__ import annotations

from oxitest import TempDir, helpers


def test_fixtures_subcmd_prints_deprecation_warning(tmp: TempDir) -> None:
    """``oxitest fixtures`` emits a deprecation warning to stderr."""
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
                return object()
        """,
    )
    _out, err, rc = helpers.common.run_oxitest_subcmd(tmp, "fixtures")
    assert rc == 0, f"expected exit 0, got {rc}\n{err}"
    assert "deprecated" in err, f"expected deprecation warning in stderr, got: {err!r}"


def test_fixtures_subcmd_still_lists_fixtures(tmp: TempDir) -> None:
    """``oxitest fixtures`` still outputs fixture listing despite being deprecated."""
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
                return object()
        """,
    )
    out, _err, rc = helpers.common.run_oxitest_subcmd(tmp, "fixtures")
    assert rc == 0, f"expected exit 0, got {rc}\n{out}"
    assert "db" in out, f"expected fixture 'db' in stdout, got: {out!r}"


def test_fixtures_subcmd_hidden_from_help() -> None:
    """``oxitest --help`` does not list the deprecated ``fixtures`` subcommand."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "oxitest", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # --help exits with code 0
    assert result.returncode == 0, (
        f"expected exit 0 from --help, got {result.returncode}"
    )
    # The word "fixtures" may appear in the query description but the
    # `fixtures` subcommand itself must not be listed as a top-level command.
    lines = result.stdout.splitlines()
    command_lines = [ln.strip() for ln in lines if ln.strip().startswith("fixtures")]
    assert not command_lines, (
        f"deprecated 'fixtures' subcommand should not appear as a listed command "
        f"in --help output, got:\n{result.stdout}"
    )
