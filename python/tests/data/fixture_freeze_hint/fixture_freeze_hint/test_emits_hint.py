"""Fails on purpose: the run must carry the fix suggestion (#2036)."""

from __future__ import annotations

from fixture_freeze_hint.__fixtures__ import WiderBox
from oxitest import Fixture


def test_mutating_a_wide_lifetime_value_is_refused(wider: Fixture[WiderBox]) -> None:
    wider.value = 1
