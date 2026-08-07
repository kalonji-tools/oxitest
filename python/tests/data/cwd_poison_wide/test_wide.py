"""The test passes; the damage happens in the module fixture's teardown."""

from __future__ import annotations

from pathlib import Path

from oxitest import Fixture


def test_uses_the_doomed_dir(doomed_dir: Fixture[Path]) -> None:
    assert doomed_dir.exists(), (
        "the fixture directory is live while the test runs — the deletion "
        "happens at the module drain, after every test that used it"
    )
