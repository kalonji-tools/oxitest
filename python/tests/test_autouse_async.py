"""Autouse x async x scope x registration policy — issue #1539.

Q5 of the #1535 spec: async function-scope autouse is refused at decorator
time. Shared-async autouse and async-each-without-autouse remain legal.
"""

from collections.abc import AsyncGenerator, Generator

from oxitest import AutouseRegistrationError, Fixtures, TempDir, raises
from tests import helpers


def test_async_each_autouse_rejected_at_registration() -> None:
    """@fx.fixture(autouse=True) on async def with shared=False raises."""
    fx = Fixtures()
    with raises(
        AutouseRegistrationError,
        match="cannot register async fixture 'each_txn' as function-scope autouse",
    ):

        @fx.fixture(autouse=True)
        async def each_txn() -> AsyncGenerator[None, None]:
            yield


def test_illegal_combo_diagnostic_names_source_location() -> None:
    """Message includes `Defined at: <file>:<lineno>` from func.__code__."""
    fx = Fixtures()
    with raises(AutouseRegistrationError) as exc_info:

        @fx.fixture(autouse=True)
        async def leak_fx() -> AsyncGenerator[None, None]:
            yield

    message = str(exc_info.value)
    assert "Defined at:" in message, (
        f"diagnostic must name source location so the user can find the "
        f"fixture, got: {message!r}"
    )
    assert "test_autouse_async.py" in message, (
        f"Defined-at path should be the test file that registered the "
        f"illegal combo, got: {message!r}"
    )


def test_illegal_combo_diagnostic_shows_two_ways_forward() -> None:
    """Message must list the two legal exits so the user unblocks themselves."""
    fx = Fixtures()
    with raises(AutouseRegistrationError) as exc_info:

        @fx.fixture(autouse=True)
        async def blocked() -> AsyncGenerator[None, None]:
            yield

    message = str(exc_info.value)
    assert "Drop autouse=True and use @arrange('blocked')" in message, (
        f"diagnostic must name the @arrange escape hatch, got: {message!r}"
    )
    assert "Pass shared=True" in message, (
        f"diagnostic must name the shared-scope escape hatch, got: {message!r}"
    )


def test_shared_async_autouse_registers_without_error() -> None:
    """@fx.fixture(autouse=True, shared=True) on async def stays legal.

    Shared-scope autouse has legal semantics for both sync and async tests
    (session loop lives outside any single test). Only the each-scope
    combination is refused.
    """
    fx = Fixtures()

    @fx.fixture(autouse=True, shared=True)
    async def db_pool() -> AsyncGenerator[None, None]:
        yield

    assert len(fx.defs) == 1, (
        f"shared-async autouse must register successfully, got {len(fx.defs)} defs"
    )
    assert fx.defs[0].autouse is True, (
        f"registered def must carry autouse=True, got {fx.defs[0].autouse!r}"
    )
    assert fx.defs[0].is_async is True, (
        f"registered def must carry is_async=True, got {fx.defs[0].is_async!r}"
    )


def test_async_each_without_autouse_registers_without_error() -> None:
    """An async def each-scope fixture without autouse=True is legal.

    This is the recommended migration path for users who hit the
    autouse rejection: register without autouse, use @arrange on the
    async tests that need it.
    """
    fx = Fixtures()

    @fx.fixture
    async def each_txn() -> AsyncGenerator[None, None]:
        yield

    assert len(fx.defs) == 1, (
        f"async each fixture (no autouse) must register successfully, "
        f"got {len(fx.defs)} defs"
    )
    assert fx.defs[0].autouse is False, (
        f"default autouse must be False, got {fx.defs[0].autouse!r}"
    )
    assert fx.defs[0].is_async is True, (
        f"registered def must carry is_async=True, got {fx.defs[0].is_async!r}"
    )


def test_sync_autouse_all_scopes_unchanged() -> None:
    """Sync autouse behavior — both scopes — must be unchanged by the gate."""
    fx = Fixtures()

    @fx.fixture(autouse=True)
    def sync_each() -> Generator[None, None, None]:
        yield

    @fx.fixture(autouse=True, shared=True)
    def sync_shared() -> Generator[None, None, None]:
        yield

    assert len(fx.defs) == 2, (
        f"both sync autouse fixtures must register, got {len(fx.defs)} defs"
    )
    assert all(d.autouse for d in fx.defs), (
        f"both defs must carry autouse=True, got {[d.autouse for d in fx.defs]!r}"
    )
    assert all(not d.is_async for d in fx.defs), (
        f"both defs must carry is_async=False (sync factories), got "
        f"{[d.is_async for d in fx.defs]!r}"
    )


def test_diagnostic_surfaces_via_oxitest_runner(tmp: TempDir) -> None:
    """A conftest with the illegal combo fails collection with the diagnostic.

    Integration guard: verifies the AutouseRegistrationError raised at
    decorator time actually reaches the user through oxitest's collection
    error path. Complements the unit tests that inspect the exception
    directly.
    """
    (tmp / "conftest.py").write_text(
        "from oxitest import Fixtures\n"
        "\n"
        "fx = Fixtures()\n"
        "\n"
        "@fx.fixture(autouse=True)\n"
        "async def each_txn():\n"
        "    yield\n"
    )
    (tmp / "test_sample.py").write_text("def test_noop(): pass\n")

    out, err, rc = helpers.run_oxitest(tmp)

    assert rc != 0, (
        f"conftest with illegal autouse combo must fail collection, "
        f"got rc={rc}\nstdout:\n{out}\nstderr:\n{err}"
    )
    combined = out + err
    assert "cannot register async fixture 'each_txn'" in combined, (
        f"diagnostic must surface via the runner so users see it, got "
        f"stdout:\n{out}\nstderr:\n{err}"
    )
    assert "Pass shared=True" in combined, (
        f"diagnostic's escape-hatch guidance must survive to user output, "
        f"got stdout:\n{out}\nstderr:\n{err}"
    )
