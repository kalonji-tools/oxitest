"""``Fixtures`` is the injection annotation now, not a registry (#1720).

ADR-0009 Rule 5 reuses the name rather than freeing it, so a user's
``fixtures = oxitest.Fixtures()`` does not fail with a clean ``AttributeError``
— it becomes a call on a name that means something else. That is a nastier
migration than a removal, and it is why the call raises a written message
instead of whatever the type system would have said.
"""

from __future__ import annotations

import oxitest
from oxitest import TempDir, raises
from tests import helpers


def test_calling_the_fixtures_type_raises_a_migration_error() -> None:
    """The call names the replacement, not just the failure."""
    with raises(TypeError) as exc_info:
        oxitest.Fixtures()

    message = str(exc_info.value)
    assert "@oxi.fixture" in message, (
        f"the error must name the replacement decorator — a user reading it has "
        f"a registry to migrate and needs the destination, got: {message!r}"
    )
    assert "__fixtures__.py" in message, (
        f"the error must name a declaration file, or the reader knows what to "
        f"write but not where it goes, got: {message!r}"
    )
    assert "migrate-from-old-oxitest" in message, (
        f"the error must cite the migration guide; the mapping is longer than "
        f"an exception can carry, got: {message!r}"
    )


def test_the_fixtures_type_still_annotates_the_proxy(tmp: TempDir) -> None:
    """``fx: Fixtures`` still injects the namespace accessor.

    Injection matches the annotation by **identity** (``hint is Fixtures``, at
    importer.py and _fixture_session.py), so the class object has to survive the
    repurposing. Only its behaviour goes. Without this the retirement would
    silently stop every ``fx:``-annotated parameter from resolving.
    """
    (tmp / "__fixtures__.py").write_text(
        "from oxitest import fixture\n"
        "@fixture(lifetime='function')\n"
        "def value() -> str:\n"
        "    return 'injected'\n",
        encoding="utf-8",
    )
    (tmp / "test_annot.py").write_text(
        "import oxitest\n"
        "def test_reads(fx: oxitest.Fixtures) -> None:\n"
        "    assert fx.value == 'injected', 'the annotation must inject the proxy'\n",
        encoding="utf-8",
    )
    out, _, rc = helpers.run_oxitest(tmp)
    assert rc == 0, f"the annotation must still resolve; rc={rc}\n{out}"
