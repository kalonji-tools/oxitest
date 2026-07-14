"""Tests for parametrize execution: fixture coexistence, FixtureRef resolution."""

from __future__ import annotations

from dataclasses import dataclass

from oxitest import Fixture, FixtureRef, TempDir, helpers, parametrize, raises
from oxitest._bridge._errors import UnannotatedFixtureParamError
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge.conftest_loader import create_session

# ── Group C: Fixture resolution with parametrize ──────────────────────────────


def test_plain_typed_param_not_resolved_as_fixture() -> None:
    """Plain-typed params (no Fixture[T] annotation) must not be resolved."""
    registry = FixtureRegistry()
    session = FixtureSession(registry)

    def test_fn(x: int, y: int) -> None:
        pass

    # x and y are NOT annotated with Fixture[T] — should not raise FixtureNotFoundError
    kwargs, _ = session.resolve_for_test(
        test_fn, helpers.common.make_meta("/fake/test_foo.py")
    )
    assert kwargs == {}, (
        f"plain-typed params should not be resolved as fixtures, got kwargs={kwargs!r}"
    )


def test_fixture_annotated_param_resolved_alongside_plain_param() -> None:
    """Fixture[T]-annotated params are resolved; plain-typed params are skipped."""
    registry = FixtureRegistry()

    def my_fixture() -> int:
        return 42

    registry.register(helpers.common.make_fixture_def("db", my_fixture))
    session = FixtureSession(registry)

    def test_fn(x: int, db: Fixture[int]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(
        test_fn, helpers.common.make_meta("/fake/test_foo.py")
    )
    assert kwargs == {"db": 42}, (
        f"only Fixture[T]-annotated param 'db' should be resolved, got "
        f"kwargs={kwargs!r}"
    )


def test_plain_typed_param_matching_fixture_raises_unannotated_error() -> None:
    """Wrong annotation matching a fixture raises UnannotatedFixtureParamError."""
    registry = FixtureRegistry()

    def x_fixture() -> int:
        return 99

    registry.register(helpers.common.make_fixture_def("x", x_fixture))
    session = FixtureSession(registry)

    def test_fn(x: int) -> None:
        pass

    with raises(UnannotatedFixtureParamError) as exc_info:
        session.resolve_for_test(test_fn, helpers.common.make_meta("/fake/test_foo.py"))

    msg = str(exc_info.value)
    assert "x" in msg, (
        f"UnannotatedFixtureParamError message should mention param name 'x', got "
        f"{msg!r}"
    )
    assert "Fixture[" in msg, (
        f"UnannotatedFixtureParamError message should suggest Fixture[...] annotation, "
        f"got {msg!r}"
    )


def test_executor_runs_parametrize_case(tmp: TempDir) -> None:
    """Executor injects parametrize field values; the test passes when correct."""
    result = helpers.common.exec_inline(
        tmp,
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "@dataclass(frozen=True)\n"
        "class AddCase:\n"
        "    x: int\n"
        "    y: int\n"
        "    expected: int\n"
        "@oxitest.parametrize(basic=AddCase(x=1, y=2, expected=3))\n"
        "def test_add(x, y, expected):\n"
        "    assert x + y == expected\n",
        "test_add",
        param_id="basic",
    )
    assert result.status == "passed", (
        f"parametrize case 'basic' should pass, got status={result.status!r}, "
        f"msg={result.message!r}"
    )


def test_executor_parametrize_failure(tmp: TempDir) -> None:
    """The executor returns failed status when a parametrize case assertion fails."""
    result = helpers.common.exec_inline(
        tmp,
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "@dataclass(frozen=True)\n"
        "class AddCase:\n"
        "    x: int\n"
        "    y: int\n"
        "    expected: int\n"
        "@oxitest.parametrize(wrong=AddCase(x=1, y=2, expected=99))\n"
        "def test_add(x, y, expected):\n"
        "    assert x + y == expected\n",
        "test_add",
        param_id="wrong",
    )
    assert result.status == "failed", (
        f"wrong expected value should produce status='failed', got {result.status!r}"
    )


def test_executor_parametrize_case_with_fixture(tmp: TempDir) -> None:
    """param_id + session: param values injected, fixture resolved, no collision."""
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "import oxitest\n"
        "fixtures = oxitest.Fixtures()\n"
        "@fixtures.fixture\n"
        "def multiplier():\n"
        "    return 10\n"
    )
    f = tmp / "test_mixed.py"
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture\n"
        "@dataclass(frozen=True)\n"
        "class MulCase:\n"
        "    x: int\n"
        "    expected: int\n"
        "@oxitest.parametrize(double=MulCase(x=2, expected=20))\n"
        "def test_mul(x: int, expected: int, multiplier: Fixture[int]) -> None:\n"
        "    assert x * multiplier == expected\n"
    )
    session, _, _diags = create_session([str(conftest)])
    result = helpers.common.run_test(
        str(f), "test_mul", session=session, param_id="double"
    )
    assert result.status == "passed", (
        f"parametrize with fixture should pass, got status={result.status!r}, "
        f"msg={result.message!r}"
    )


def test_fixture_ref_in_parametrize_resolves_fixture(tmp: TempDir) -> None:
    """FixtureRef[T] field is resolved via the fixture session per case."""
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "import oxitest\n"
        "fixtures = oxitest.Fixtures()\n"
        "\n"
        "@fixtures.fixture\n"
        "def pg_db():\n"
        "    return 'postgres'\n"
        "\n"
        "@fixtures.fixture\n"
        "def sqlite_db():\n"
        "    return 'sqlite'\n"
    )
    f = tmp / "test_db.py"
    # The test file defines local stubs decorated with Fixtures() so they get
    # _oxitest_fixture_name stamped. The session resolves by name from conftest.
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef\n"
        "\n"
        "_fixtures = oxitest.Fixtures()\n"
        "\n"
        "@_fixtures.fixture\n"
        "def pg_db(): return 'postgres'\n"
        "\n"
        "@_fixtures.fixture\n"
        "def sqlite_db(): return 'sqlite'\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class DbCase:\n"
        "    db: FixtureRef[str]\n"
        "    expected: str\n"
        "\n"
        "@oxitest.parametrize(\n"
        "    pg=DbCase(db=pg_db, expected='postgres'),\n"
        "    sq=DbCase(db=sqlite_db, expected='sqlite'),\n"
        ")\n"
        "def test_db(db: Fixture[str], expected: str) -> None:\n"
        "    assert db == expected\n"
    )
    session, _, _diags = create_session([str(conftest)])
    result_pg = helpers.common.run_test(
        str(f), "test_db", session=session, param_id="pg"
    )
    result_sq = helpers.common.run_test(
        str(f), "test_db", session=session, param_id="sq"
    )
    assert result_pg.status == "passed", result_pg.message
    assert result_sq.status == "passed", result_sq.message


def test_fixture_ref_compact_mode_raises(tmp: TempDir) -> None:
    """FixtureRef fields are incompatible with compact mode — must return error."""
    result = helpers.common.exec_inline(
        tmp,
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef\n"
        "@dataclass(frozen=True)\n"
        "class DbCase:\n"
        "    db: FixtureRef[str]\n"
        "def my_db(): return 'x'\n"
        "@oxitest.parametrize(pg=DbCase(db=my_db))\n"
        "def test_db(case: DbCase) -> None:\n"  # compact mode: single DbCase param
        "    pass\n",
        "test_db",
        param_id="pg",
    )
    assert result.status == "error", (
        f"FixtureRef in compact mode should produce status='error', got "
        f"{result.status!r}"
    )
    assert "compact mode" in result.message, (
        f"error message should mention 'compact mode', got {result.message!r}"
    )


def test_fixture_ref_unregistered_fixture_errors(tmp: TempDir) -> None:
    """Passing an unregistered fixture function as FixtureRef value -> error result."""
    conftest = tmp / "conftest.py"
    conftest.write_text("import oxitest\nfixtures = oxitest.Fixtures()\n")
    f = tmp / "test_bad.py"
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef\n"
        "@dataclass(frozen=True)\n"
        "class DbCase:\n"
        "    db: FixtureRef[str]\n"
        "def unknown_db(): return 'x'\n"
        "@oxitest.parametrize(pg=DbCase(db=unknown_db))\n"
        "def test_db(db: Fixture[str]) -> None:\n"
        "    pass\n"
    )
    session, _, _diags = create_session([str(conftest)])
    result = helpers.common.run_test(str(f), "test_db", session=session, param_id="pg")
    assert result.status == "error", (
        f"unregistered FixtureRef should produce status='error', got {result.status!r}"
    )
    assert "unknown_db" in result.message, (
        f"error message should mention 'unknown_db', got {result.message!r}"
    )


def test_fixture_ref_no_session_returns_error(tmp: TempDir) -> None:
    """FixtureRef field with session=None returns error result, not None injection."""
    result = helpers.common.exec_inline(
        tmp,
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef\n"
        "@dataclass(frozen=True)\n"
        "class DbCase:\n"
        "    db: FixtureRef[str]\n"
        "def my_db(): return 'x'\n"
        "@oxitest.parametrize(pg=DbCase(db=my_db))\n"
        "def test_db(db: Fixture[str]) -> None:\n"  # expanded mode
        "    pass\n",
        "test_db",
        param_id="pg",
    )
    assert result.status == "error", (
        f"FixtureRef with session=None should produce status='error', got "
        f"{result.status!r}"
    )
    assert "my_db" in result.message, (
        f"error message should mention 'my_db', got {result.message!r}"
    )


def test_parametrize_rejects_non_callable_for_fixture_ref_field() -> None:
    """FixtureRef[T] fields must hold callables — non-callable raises TypeError."""

    @dataclass(frozen=True)
    class RefCase:
        db: FixtureRef[int]

    with raises(TypeError, match="FixtureRef"):

        @parametrize(bad=RefCase(db=42))
        def test_foo(db: Fixture[int]) -> None:
            pass


# ── Group E: FixtureRef namespace ─────────────────────────────────────────────


def test_fixture_ref_uses_namespace_qualified_lookup_when_namespace_present(
    tmp: TempDir,
) -> None:
    """FixtureRef function with FixtureDef.namespace uses namespace-qualified lookup.

    Two namespaces 'db' and 'http' both define a fixture named 'conn'.  'http'
    is registered after 'db', so flat lookup would return the http version.
    The FixtureRef holds the db-namespace function, whose FixtureDef.namespace
    is "db", so the result must be the db value.
    """
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "import oxitest\n"
        "db = oxitest.Fixtures()\n"
        "http = oxitest.Fixtures()\n"
        "\n"
        "@db.fixture\n"
        "def conn():\n"
        "    return 'db-conn'\n"
        "\n"
        "@http.fixture\n"
        "def conn():  # same name, different namespace\n"
        "    return 'http-conn'\n"
    )
    # Test file imports the db-namespace conn from conftest. The registry
    # resolves the namespace via FixtureDef.namespace (matched by func identity).
    # Without namespace-aware lookup the flat registry returns the http version
    # (last registered wins).
    f = tmp / "test_ns.py"
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef\n"
        "from conftest import db as _db_ns\n"
        "\n"
        "_conn_ref = None\n"
        "for _defn in _db_ns._defs:\n"
        "    if _defn.name == 'conn':\n"
        "        _conn_ref = _defn.func\n"
        "        break\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class StoreCase:\n"
        "    store: FixtureRef[str]\n"
        "\n"
        "@oxitest.parametrize(prod=StoreCase(store=_conn_ref))\n"
        "def test_query(store: Fixture[str]) -> None:\n"
        "    assert store == 'db-conn'\n"
    )
    session, _, _diags = create_session([str(conftest)])
    result = helpers.common.run_test(
        str(f), "test_query", session=session, param_id="prod"
    )
    assert result.status == "passed", result.message


def test_fixture_ref_falls_back_to_flat_lookup_when_no_namespace(tmp: TempDir) -> None:
    """FixtureRef function without a namespace falls back to flat get_fixture.

    The fixture function is not registered in the session's registry (defined
    locally in the test file), so the registry returns no namespace and the
    executor falls back to the flat get_fixture call.
    """
    conftest = tmp / "conftest.py"
    conftest.write_text(
        "import oxitest\n"
        "fixtures = oxitest.Fixtures()\n"
        "\n"
        "@fixtures.fixture\n"
        "def pg_db():\n"
        "    return 'flat-pg'\n"
    )
    # The test file defines a local stub (NOT imported from conftest) so it will
    # NOT be in the session registry.  The flat lookup must still resolve it.
    f = tmp / "test_flat.py"
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef\n"
        "\n"
        "_fixtures = oxitest.Fixtures()\n"
        "\n"
        "@_fixtures.fixture\n"
        "def pg_db(): return 'flat-pg'\n"
        "# pg_db defined locally: not in conftest registry\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class DbCase:\n"
        "    db: FixtureRef[str]\n"
        "\n"
        "@oxitest.parametrize(pg=DbCase(db=pg_db))\n"
        "def test_db(db: Fixture[str]) -> None:\n"
        "    assert db == 'flat-pg'\n"
    )
    session, _, _diags = create_session([str(conftest)])
    result = helpers.common.run_test(str(f), "test_db", session=session, param_id="pg")
    assert result.status == "passed", result.message
