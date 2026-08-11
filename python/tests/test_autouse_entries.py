"""`autouse_entries` reports the *effective* autouse set, not the declared flag.

ADR-0009 Rule 7 lets a subtree opt out of an ancestor's autouse fixture by
declaring the same name without ``autouse`` at a deeper anchor. A view built by
filtering fixtures on their declared ``autouse`` flag would list a fixture the
user has switched off, so the opt-out is what separates a correct
implementation from a plausible one (#1722).

The registry is populated by calling the Python registrar directly. The Rust
side does this through ``register_declaration_homes_for_files``; running
oxitest as a subprocess would populate a registry in that process, not this one.
"""

from __future__ import annotations

from oxitest import TempDir
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._session_factory import create_session
from oxitest._bridge.importer import register_module_source_fixtures_for_module
from oxitest._bridge.query_bridge import autouse_entries


def _register(session: FixtureSession, anchor: str) -> None:
    """Register the ``__fixtures__.py`` anchored at *anchor* into *session*."""
    register_module_source_fixtures_for_module(
        registry=session.registry,
        fixture_module_path=f"{anchor}/__fixtures__.py",
        anchor_package_path=anchor,
    )


def _names_for(entries: list[dict[str, str]], module_path: str) -> list[str]:
    for entry in entries:
        if entry["module_path"] == module_path:
            return [n for n in entry["fixture_names"].split(",") if n]
    message = f"no entry for {module_path}"
    raise AssertionError(message)


def test_autouse_entries_honours_the_rule_7_opt_out(tmp: TempDir) -> None:
    """A deeper non-autouse declaration of the same name suppresses the ancestor."""
    # Arrange — root declares two autouse fixtures; api/ opts out of one.
    root = tmp / "proj"
    (root / "api").mkdir(parents=True)
    (root / "__fixtures__.py").write_text(
        "import oxitest as oxi\n\n"
        '@oxi.fixture(lifetime="module", autouse=True)\n'
        "def tracer() -> int:\n"
        "    return 1\n\n"
        '@oxi.fixture(lifetime="module", autouse=True)\n'
        "def always() -> int:\n"
        "    return 2\n",
        encoding="utf-8",
    )
    (root / "api" / "__fixtures__.py").write_text(
        "import oxitest as oxi\n\n"
        '@oxi.fixture(lifetime="module")\n'
        "def tracer() -> int:\n"
        "    return 3\n",
        encoding="utf-8",
    )
    session = create_session(rootdir=str(root))
    _register(session, str(root))
    _register(session, f"{root}/api")

    # Act
    entries = autouse_entries(
        [f"{root}/test_root.py", f"{root}/api/test_api.py"], session
    )
    root_names = _names_for(entries, f"{root}/test_root.py")
    api_names = _names_for(entries, f"{root}/api/test_api.py")

    # Assert
    assert "always" in api_names, (
        "the probe is only meaningful if the ancestor's fixtures reach api/ at "
        "all; without this the opt-out assertion below could pass because "
        "nothing registered rather than because the opt-out worked"
    )
    assert "tracer" not in api_names, (
        "ADR-0009 Rule 7 lets a subtree opt out by declaring the same name "
        "without autouse at a deeper anchor; api/ does exactly that, so listing "
        "'tracer' there would show the user a fixture they switched off — which "
        "is what a filter on the declared autouse flag would produce"
    )
    assert "tracer" in root_names, (
        "the opt-out is boundary-local: outside api/ the deeper declaration is "
        "invisible and the ancestor still applies"
    )


def test_autouse_entries_orders_widest_lifetime_first(tmp: TempDir) -> None:
    """Rule 7 fires widest lifetime first; the row order must match."""
    # Arrange
    root = tmp / "ordered"
    root.mkdir(parents=True)
    (root / "__fixtures__.py").write_text(
        "import oxitest as oxi\n\n"
        '@oxi.fixture(lifetime="module", autouse=True)\n'
        "def narrow() -> int:\n"
        "    return 1\n\n"
        '@oxi.fixture(lifetime="package", autouse=True)\n'
        "def wide() -> int:\n"
        "    return 2\n",
        encoding="utf-8",
    )
    session = create_session(rootdir=str(root))
    _register(session, str(root))

    # Act
    entries = autouse_entries([f"{root}/test_o.py"], session)
    names = _names_for(entries, f"{root}/test_o.py")

    # Assert
    assert names == ["wide", "narrow"], (
        "ADR-0009 Rule 7 fires autouse fixtures widest lifetime first — process, "
        "package, module, function — and get_autouse yields in that order, so "
        f"re-sorting the rows would contradict a documented guarantee; got {names}"
    )


def test_autouse_entries_reports_the_lifetime_not_the_scope(tmp: TempDir) -> None:
    """The tier column carries the word the declaration wrote."""
    # Arrange
    root = tmp / "tiers"
    root.mkdir(parents=True)
    (root / "__fixtures__.py").write_text(
        "import oxitest as oxi\n\n"
        '@oxi.fixture(lifetime="function", autouse=True)\n'
        "def per_test() -> int:\n"
        "    return 1\n",
        encoding="utf-8",
    )
    session = create_session(rootdir=str(root))
    _register(session, str(root))

    # Act
    entries = autouse_entries([f"{root}/test_t.py"], session)
    lifetimes = entries[0]["lifetimes"].split(",")

    # Assert
    assert lifetimes == ["function"], (
        "lifetime='function' maps to scope='each' through LIFETIME_SCOPES, so a "
        "row reading 'each' would show the caching vocabulary where the user's "
        f"own word belongs; got {lifetimes}"
    )
