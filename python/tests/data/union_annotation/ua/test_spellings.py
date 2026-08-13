"""Both union spellings must reach the fixture the parameter names."""

from __future__ import annotations

from typing import Union

import oxitest as oxi


def test_pep604_union(thing: oxi.Fixture[str | int]) -> None:
    assert thing == "from-thing", "the modern union spelling must resolve by name"


def test_typing_union(thing: oxi.Fixture[Union[str, int]]) -> None:  # noqa: UP007
    assert thing == "from-thing", "the typing union spelling must resolve by name"
