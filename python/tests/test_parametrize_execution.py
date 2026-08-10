"""Tests for parametrize execution: fixture coexistence, FixtureRef resolution."""

from __future__ import annotations

from dataclasses import dataclass

from oxitest import Fixture, FixtureRef, TempDir, parametrize, raises
from oxitest._bridge._errors import UnannotatedFixtureParamError
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._syspath import ensure_rootdir_importable
from oxitest._bridge.result import ErrorResult, PassedResult
from tests import helpers

# ── Group C: Fixture resolution with parametrize ──────────────────────────────


def test_plain_typed_param_not_resolved_as_fixture() -> None:
    """Plain-typed params (no Fixture[T] annotation) must not be resolved."""
    registry = FixtureRegistry()
    session = FixtureSession(registry)

    def test_fn(x: int, y: int) -> None:
        pass

    # x and y are NOT annotated with Fixture[T] — should not raise FixtureNotFoundError
    kwargs, _ = session.resolve_for_test(
        test_fn, helpers.make_meta("/fake/test_foo.py")
    )
    assert kwargs == {}, (
        f"plain-typed params should not be resolved as fixtures, got kwargs={kwargs!r}"
    )


def test_fixture_annotated_param_resolved_alongside_plain_param() -> None:
    """Fixture[T]-annotated params are resolved; plain-typed params are skipped."""
    registry = FixtureRegistry()

    def my_fixture() -> int:
        return 42

    registry.register(helpers.make_fixture_def("db", my_fixture))
    session = FixtureSession(registry)

    def test_fn(x: int, db: Fixture[int]) -> None:
        pass

    kwargs, _ = session.resolve_for_test(
        test_fn, helpers.make_meta("/fake/test_foo.py")
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

    registry.register(helpers.make_fixture_def("x", x_fixture))
    session = FixtureSession(registry)

    def test_fn(x: int) -> None:
        pass

    with raises(UnannotatedFixtureParamError) as exc_info:
        session.resolve_for_test(test_fn, helpers.make_meta("/fake/test_foo.py"))

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
    result = helpers.exec_inline(
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
    helpers.assert_result(
        result,
        PassedResult,
        why="parametrize case 'basic' should pass",
    )


def test_executor_parametrize_failure(tmp: TempDir) -> None:
    """The executor returns failed status when a parametrize case assertion fails."""
    result = helpers.exec_inline(
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
    declarations = tmp / "__fixtures__.py"
    declarations.write_text(
        "from oxitest import fixture\n"
        "@fixture(lifetime='function')\n"
        "def multiplier():\n"
        "    return 10\n",
        encoding="utf-8",
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
        "    assert x * multiplier == expected\n",
        encoding="utf-8",
    )
    session = helpers.session_from_declarations(
        declarations, anchor_package_path=str(tmp)
    )
    result = helpers.run_test(str(f), "test_mul", session=session, param_id="double")
    helpers.assert_result(
        result,
        PassedResult,
        why="parametrize with fixture should pass",
    )


def test_fixture_ref_in_parametrize_resolves_fixture(tmp: TempDir) -> None:
    """FixtureRef[T] field is resolved via the fixture session per case."""
    declarations = tmp / "__fixtures__.py"
    declarations.write_text(
        "from oxitest import fixture\n"
        "\n"
        "@fixture(lifetime='function')\n"
        "def pg_db():\n"
        "    return 'postgres'\n"
        "\n"
        "@fixture(lifetime='function')\n"
        "def sqlite_db():\n"
        "    return 'sqlite'\n",
        encoding="utf-8",
    )
    f = tmp / "test_db.py"
    # The test file declares local stubs with @oxi.fixture so they carry the
    # fixture marker. The session resolves by name from the declaration file.
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef, fixture\n"
        "\n"
        "\n"
        "@fixture(lifetime='function')\n"
        "def pg_db(): return 'postgres'\n"
        "\n"
        "@fixture(lifetime='function')\n"
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
        "    assert db == expected\n",
        encoding="utf-8",
    )
    session = helpers.session_from_declarations(
        declarations, anchor_package_path=str(tmp)
    )
    result_pg = helpers.run_test(str(f), "test_db", session=session, param_id="pg")
    result_sq = helpers.run_test(str(f), "test_db", session=session, param_id="sq")
    helpers.assert_result(
        result_pg,
        PassedResult,
        why="case 'pg' must resolve its FixtureRef to the pg_db fixture value",
    )
    helpers.assert_result(
        result_sq,
        PassedResult,
        why="case 'sq' must resolve its FixtureRef to the sqlite_db fixture value --"
        " each case resolves independently, so 'sq' must not reuse 'pg'",
    )


def test_fixture_ref_compact_mode_raises(tmp: TempDir) -> None:
    """FixtureRef fields are incompatible with compact mode — must return error."""
    result = helpers.exec_inline(
        tmp,
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef, fixture\n"
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
    result = helpers.assert_result(
        result,
        ErrorResult,
        why="FixtureRef in compact mode should produce status='error'",
    )
    assert "compact mode" in result.message, (
        f"error message should mention 'compact mode', got {result.message!r}"
    )


def test_fixture_ref_unregistered_fixture_errors(tmp: TempDir) -> None:
    """Passing an unregistered fixture function as FixtureRef value -> error result."""
    declarations = tmp / "__fixtures__.py"
    # Empty on purpose: the registry must hold nothing for the ref to find.
    declarations.write_text("", encoding="utf-8")
    f = tmp / "test_bad.py"
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef, fixture\n"
        "@dataclass(frozen=True)\n"
        "class DbCase:\n"
        "    db: FixtureRef[str]\n"
        "def unknown_db(): return 'x'\n"
        "@oxitest.parametrize(pg=DbCase(db=unknown_db))\n"
        "def test_db(db: Fixture[str]) -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )
    session = helpers.session_from_declarations(
        declarations, anchor_package_path=str(tmp)
    )
    result = helpers.run_test(str(f), "test_db", session=session, param_id="pg")
    result = helpers.assert_result(
        result,
        ErrorResult,
        why="unregistered FixtureRef should produce status='error'",
    )
    assert "unknown_db" in result.message, (
        f"error message should mention 'unknown_db', got {result.message!r}"
    )


def test_fixture_ref_no_session_returns_error(tmp: TempDir) -> None:
    """FixtureRef field with session=None returns error result, not None injection."""
    result = helpers.exec_inline(
        tmp,
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef, fixture\n"
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
    result = helpers.assert_result(
        result,
        ErrorResult,
        why="FixtureRef with session=None should produce status='error'",
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
    """A FixtureRef resolves through the namespace its function was declared in.

    Two namespaces both define ``conn``. The ref holds the outer one's function,
    so the result must be the outer value even though the inner is registered
    later and would win a flat, name-only lookup.

    Three things about the arrangement are forced rather than chosen:

    - The anchors **nest**. A namespace is an anchor directory's basename, and
      B1 makes a fixture visible only inside its own anchor, so two siblings
      could never both be visible to the one test.
    - The implementations live in ordinary modules. Registration matches by
      **function identity**, and the framework imports the declaration file
      itself — so a test importing ``__fixtures__`` by name would get a second
      module object, a different function, and a lookup that fails for a reason
      the test never meant to exercise.
    - ``@oxi.fixture`` attaches a marker and returns the function unchanged, so
      a declaration file re-exporting one registers it just the same.
    """
    # run_test loads a module by path and does not do the rootdir sys.path
    # setup a real run does (#1780), so the implementation modules would not
    # be importable from either the declaration file or the test module.
    ensure_rootdir_importable(str(tmp))
    outer = tmp / "db"
    outer.mkdir()
    inner = outer / "http"
    inner.mkdir()
    (tmp / "impl_db.py").write_text(
        "from oxitest import fixture\n\n"
        "@fixture(lifetime='function')\n"
        "def conn() -> str:\n"
        "    return 'db-conn'\n",
        encoding="utf-8",
    )
    (tmp / "impl_http.py").write_text(
        "from oxitest import fixture\n\n"
        "@fixture(lifetime='function')\n"
        "def conn() -> str:\n"
        "    return 'http-conn'\n",
        encoding="utf-8",
    )
    outer_decls = outer / "__fixtures__.py"
    outer_decls.write_text("from impl_db import conn\n", encoding="utf-8")
    inner_decls = inner / "__fixtures__.py"
    inner_decls.write_text("from impl_http import conn\n", encoding="utf-8")

    f = inner / "test_ns.py"
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef\n"
        "from impl_db import conn as _conn_ref\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class StoreCase:\n"
        "    store: FixtureRef[str]\n"
        "\n"
        "@oxitest.parametrize(prod=StoreCase(store=_conn_ref))\n"
        "def test_query(store: Fixture[str]) -> None:\n"
        "    assert store == 'db-conn', 'the outer namespace must win'\n",
        encoding="utf-8",
    )

    reg = FixtureRegistry()
    for decls, anchor in ((outer_decls, outer), (inner_decls, inner)):
        for defn in helpers.session_from_declarations(
            decls, anchor_package_path=str(anchor)
        ).registry.all():
            reg.register(defn)
    session = FixtureSession(reg)

    result = helpers.run_test(str(f), "test_query", session=session, param_id="prod")
    helpers.assert_result(
        result,
        PassedResult,
        why="the FixtureRef must resolve through namespace-qualified lookup -- flat"
        " lookup would return the later-registered inner 'conn' instead",
    )


def test_fixture_ref_falls_back_to_flat_lookup_when_no_namespace(tmp: TempDir) -> None:
    """FixtureRef function without a namespace falls back to flat get_fixture_by_name.

    The fixture function is not registered in the session's registry (defined
    locally in the test file), so the registry returns no namespace and the
    executor falls back to the flat get_fixture_by_name call.
    """
    declarations = tmp / "__fixtures__.py"
    declarations.write_text(
        "from oxitest import fixture\n"
        "\n"
        "@fixture(lifetime='function')\n"
        "def pg_db():\n"
        "    return 'flat-pg'\n",
        encoding="utf-8",
    )
    # The test file defines a local stub (NOT imported from conftest) so it will
    # NOT be in the session registry.  The flat lookup must still resolve it.
    f = tmp / "test_flat.py"
    f.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "import oxitest\n"
        "from oxitest import Fixture, FixtureRef, fixture\n"
        "\n"
        "\n"
        "@fixture(lifetime='function')\n"
        "def pg_db(): return 'flat-pg'\n"
        "# pg_db defined locally: not in conftest registry\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class DbCase:\n"
        "    db: FixtureRef[str]\n"
        "\n"
        "@oxitest.parametrize(pg=DbCase(db=pg_db))\n"
        "def test_db(db: Fixture[str]) -> None:\n"
        "    assert db == 'flat-pg'\n",
        encoding="utf-8",
    )
    session = helpers.session_from_declarations(
        declarations, anchor_package_path=str(tmp)
    )
    result = helpers.run_test(str(f), "test_db", session=session, param_id="pg")
    helpers.assert_result(
        result,
        PassedResult,
        why="a FixtureRef whose function carries no registered namespace must still"
        " resolve via the flat get_fixture_by_name fallback",
    )
