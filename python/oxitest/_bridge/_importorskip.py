from __future__ import annotations

__all__ = ["importorskip"]

import importlib
import unittest
from typing import Any


def importorskip(modname: str, *, reason: str | None = None) -> Any:
    """Import a module, skipping the test if it is not installed.

    Args:
        modname: Name of the module to import (e.g. `"loguru"`).
        reason: Optional skip message. Defaults to
                `"could not import '<modname>': <error>"`.

    Returns:
        The imported module.

    Raises:
        unittest.SkipTest: If the module cannot be imported.

    Example::

        loguru = oxitest.importorskip("loguru")
        loguru.logger.info("only runs when loguru is installed")
    """
    try:
        return importlib.import_module(modname)
    except ImportError as e:
        raise unittest.SkipTest(
            reason or f"could not import {modname!r}: {e}"
        ) from None
