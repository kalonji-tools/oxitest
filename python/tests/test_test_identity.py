"""TestIdentity carries the running test's identity into a fixture (#1879)."""

from __future__ import annotations

from pathlib import Path

import oxitest as oxi
from oxitest import TempDir
from oxitest._bridge._errors import TestIdentityUnavailableError
from oxitest._bridge._test_identity import TestIdentity
from oxitest._bridge._test_meta import TestMeta
from tests import helpers

_ROUTES = Path(__file__).parent / "data" / "test_identity_routes"
_ASYNC = Path(__file__).parent / "data" / "test_identity_async"
_REFUSED = Path(__file__).parent / "data" / "test_identity_refused"


def test_test_identity_exposes_the_four_identity_fields() -> None:
    """All four identity accessors answer from a real TestMeta."""
    # Arrange
    meta = TestMeta(
        module_path="/t.py",
        fn_name="test_x",
        node_id="/t.py::test_x",
        markers=frozenset({"slow"}),
    )

    # Act
    identity = TestIdentity(meta)

    # Assert
    assert identity.name == "test_x", (
        "name is the accessor the whole type exists for; a fixture derives a "
        "per-test resource name from it"
    )
    assert identity.node_id == "/t.py::test_x", (
        "node_id is the field the proxy route used to lose, so asserting it "
        "here keeps the route comparison a comparison of two real answers"
    )
    assert identity.marks == frozenset({"slow"}), (
        "marks lets a fixture vary setup by mark without the test restating it"
    )
    assert identity.param_id is None, (
        "an unparametrized test has no case id; None is the answer, not a raise"
    )


def test_test_identity_is_exported_from_the_package() -> None:
    """TestIdentity is reachable as public API, not only from _bridge."""
    # Assert
    assert oxi.TestIdentity is TestIdentity, (
        "TestIdentity is public API — a fixture author imports it from oxitest, "
        "never from _bridge"
    )


def test_every_function_lifetime_route_carries_identity() -> None:
    """All five function-lifetime routes resolve the running test's identity."""
    # Act
    stdout, _stderr, code = helpers.run_oxitest(_ROUTES, "--warnings")

    # Assert
    assert code == 0, (
        f"every route must resolve identity; a non-zero exit means one route "
        f"reaches _resolve_deps by a path the tier gate does not cover\n{stdout}"
    )


def test_the_async_route_carries_identity() -> None:
    """The async caller of _resolve_deps resolves identity per test."""
    # Act
    stdout, _stderr, code = helpers.run_oxitest(_ASYNC, "--warnings")

    # Assert
    assert code == 0, (
        f"_resolve_async_deps is a separate caller of _resolve_deps from "
        f"_instantiate, so the sync route passing does not cover it\n{stdout}"
    )


def test_a_wider_lifetime_fixture_may_not_declare_test_identity(
    tmp: TempDir,
) -> None:
    """A module-lifetime fixture declaring TestIdentity is refused at load."""
    # Arrange
    pkg = tmp.path / "wid"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__fixtures__.py").write_text(
        "import oxitest as oxi\n"
        "from oxitest import TestIdentity\n\n\n"
        '@oxi.fixture(lifetime="module")\n'
        "def wide(test: TestIdentity) -> str:\n"
        "    return test.name\n",
        encoding="utf-8",
    )
    # No test uses `wide`. This is what discriminates a REGISTRATION-time
    # refusal from a resolution-time one: resolution never happens here, so a
    # runtime guard alone leaves this project green.
    (pkg / "test_w.py").write_text(
        "def test_unrelated() -> None:\n"
        '    assert True, "the offending fixture is never resolved"\n',
        encoding="utf-8",
    )

    # Act
    stdout, stderr, code = helpers.run_oxitest(tmp.path, "--warnings")

    # Assert
    assert code != 0, (
        f"a module-lifetime fixture is built once for whichever test arrives "
        f"first, so TestIdentity has no answer and must be refused\n{stdout}"
    )
    assert "lifetime" in (stdout + stderr), (
        "the refusal must name the lifetime, so the author knows which of the "
        "two things to change — the annotation or the tier"
    )


def test_a_test_may_not_declare_test_identity(tmp: TempDir) -> None:
    """A test reads its own identity with oxi.current_test(), not this type."""
    # Arrange
    pkg = tmp.path / "tid"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "test_t.py").write_text(
        "from oxitest import TestIdentity\n\n\n"
        "def test_x(test: TestIdentity) -> None:\n"
        '    assert test.name, "unreachable — injection must refuse first"\n',
        encoding="utf-8",
    )

    # Act
    stdout, stderr, code = helpers.run_oxitest(tmp.path, "--warnings")

    # Assert
    assert code != 0, (
        f"a test reads its own identity with oxi.current_test(); a second "
        f"route is the duplication #1949 Q5 rejected\n{stdout}"
    )
    assert "TestIdentity is for a fixture, not for a test" in (stdout + stderr), (
        "the refusal must be the TEST-position one. Both refusals exit non-zero "
        "and both name oxi.current_test(), so a weaker assertion passes even "
        "when this guard is deleted — a mutant proved exactly that"
    )


def test_identity_is_refused_beneath_a_wider_consumer() -> None:
    """A function-lifetime fixture cached by a wider consumer refuses identity."""
    # Act
    stdout, _stderr, code = helpers.run_oxitest(_REFUSED, "--warnings")

    # Assert
    assert code != 0, (
        f"a function-lifetime fixture cached by a module-lifetime consumer has "
        f"no single test, so identity must be refused rather than pinned\n{stdout}"
    )
    assert "is not available here" in stdout, (
        "the run must fail because identity was REFUSED. Without this the "
        "assertion passes on any failure, including the leak it exists to "
        f"catch — both states exit non-zero\n{stdout}"
    )


def test_the_refusal_names_a_route_that_exists() -> None:
    """The message points at TestIdentity, not at the unexpressible workaround."""
    # Act
    message = str(TestIdentityUnavailableError("name"))

    # Assert
    assert "TestIdentity" in message, (
        "the old message prescribed handing a value to the fixture from the "
        "test body, which cannot be written — a fixture is built before the "
        "body runs, so the advice named a moment that does not exist"
    )
    assert "pass what you need into the fixture" not in message, (
        "the unexpressible workaround must be gone, not merely supplemented"
    )
