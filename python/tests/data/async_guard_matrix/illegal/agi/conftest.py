"""Publishes the registrar declared in ``_registrar.py``.

``conftest_loader`` scans this module for ``Fixtures`` instances, so the
re-export is the whole registration. See ``_registrar.py`` for why the
declarations do not live here.
"""

from __future__ import annotations

from agi._registrar import db

__all__ = ["db"]
